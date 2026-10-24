"""Continuous HDMI timing and tagged RGB565 scanout in the pixel domain."""
from functools import reduce
from operator import xor
from migen import Cat, Case, ClockDomain, ClockDomainsRenamer, ClockSignal, If, Mux, Signal
from migen.genlib.cdc import BusSynchronizer, MultiReg
from migen.genlib.fifo import AsyncFIFO
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI
from gateware.machines.mega138k_ae350.video_memory import VideoFrameReader
from gateware.machines.mega138k_ae350 import address_map as amap


class VideoPixels(LiteXModule):
    """Timing never waits for DDR. A missing pixel blanks the rest of its frame.

    Buffered pixels carry both a frame epoch and an absolute pixel index.
    Stale data is drained during blanking; an underflow cannot shift later
    rows or accidentally display the previous frame. A fresh epoch recovers
    at vblank, with no unilateral reset of the asynchronous FIFO.
    """
    def __init__(self, width=640, height=480, h_front=16, h_sync=96, h_back=48,
                 v_front=10, v_sync=2, v_back=33):
        self.source = source = stream.Endpoint([('data', 16), ('index', 32), ('epoch', 32)])
        self.enabled = Signal()
        self.rgb = Signal(24)  # {B,G,R}, matching the physical TMDS lane order.
        self.de = Signal()
        self.hsync = Signal()
        self.vsync = Signal()
        self.vblank = Signal()
        self.frame_start = Signal()
        self.epoch = Signal(32)
        self.frames = Signal(32)
        self.underflows = Signal(32)
        self.missing_pixels = Signal(32)
        self.frame_bad = Signal()
        self.index = Signal(32)
        h_total = width + h_front + h_sync + h_back
        v_total = height + v_front + v_sync + v_back
        self.x = x = Signal(max=h_total)
        self.y = y = Signal(max=v_total, reset=height)
        match = Signal()
        red, green, blue = Signal(8), Signal(8), Signal(8)
        pixel = Signal(16)
        pattern = Signal(24)
        started = Signal()
        self.comb += [
            self.de.eq((x < width) & (y < height)),
            self.vblank.eq(y >= height),
            self.frame_start.eq((x == 0) & (y == height)),
            self.hsync.eq(~((x >= width + h_front) & (x < width + h_front + h_sync))),
            self.vsync.eq(~((y >= height + v_front) & (y < height + v_front + v_sync))),
            match.eq(source.valid & (source.epoch == self.epoch) & (source.index == self.index)),
            source.ready.eq(~self.enabled | self.frame_bad | (source.epoch != self.epoch) |
                              (self.de & (source.index == self.index))),
            pixel.eq(Mux(match & ~self.frame_bad, source.data, 0)),
            red.eq(Cat(pixel[13:16], pixel[11:16])),
            green.eq(Cat(pixel[9:11], pixel[5:11])),
            blue.eq(Cat(pixel[2:5], pixel[:5])),
            self.rgb.eq(Mux(self.enabled, Cat(red, green, blue), pattern)),
            # Six color rectangles, 32px grid, and a one-pixel white boundary.
            pattern.eq(0x202020),
            If((x[0:5] == 31) | (y[0:5] == 31), pattern.eq(0)),
            If((x >= 96) & (x < 288),
                If((y >= 64) & (y < 160), pattern.eq(0x00003f)),
                If((y >= 192) & (y < 288), pattern.eq(0x003f00)),
                If((y >= 320) & (y < 416), pattern.eq(0x3f0000)),
            ),
            If((x >= 352) & (x < 544),
                If((y >= 64) & (y < 160), pattern.eq(0x3f3f00)),
                If((y >= 192) & (y < 288), pattern.eq(0x3f003f)),
                If((y >= 320) & (y < 416), pattern.eq(0x003f3f)),
            ),
            If((x == 0) | (x == width - 1) | (y == 0) | (y == height - 1), pattern.eq(0xffffff)),
        ]
        self.sync += [
            If(x == h_total - 1,
                x.eq(0), If(y == v_total - 1, y.eq(0)).Else(y.eq(y + 1)),
            ).Else(x.eq(x + 1)),
            If(self.frame_start,
                self.epoch.eq(self.epoch + 1), self.index.eq(0), self.frame_bad.eq(0),
                If(started, self.frames.eq(self.frames + 1)), started.eq(1),
            ).Elif(self.de,
                self.index.eq(self.index + 1),
                If(self.enabled & (~match | self.frame_bad),
                    self.missing_pixels.eq(self.missing_pixels + 1),
                    If(~self.frame_bad, self.underflows.eq(self.underflows + 1)),
                    self.frame_bad.eq(1),
                ),
            ),
        ]


class FabricVideo(LiteXModule):
    """APB snapshots, atomic vblank apply, two coherent FIFOs and DDR scanout.

    CONTROL/BASE0/BASE1/STRIDE stage settings; COMMIT snapshots them once.
    A pending snapshot cannot be overwritten. The selected base and run bit
    change together at vblank. Disabling still sends a stop descriptor to the
    reader, and STOPPED waits for its outstanding read to retire.
    """
    def __init__(self, width=640, height=480, fifo_depth=2048, response_timeout=1 << 18):
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.commit = Signal()
        self.selected = Signal()
        self.rdata = Signal(32)
        self.ok = Signal()
        self.reset = Signal()
        self.ui = DDR3UI()
        self.cpu_commands = Signal(32)
        self.video_commands = Signal(32)
        self.max_wait = Signal(32)
        self.memory_fault = Signal()
        self.cd_video_ddr = ClockDomain('video_ddr')
        self.cd_video_pix = ClockDomain('video_pix')
        self.comb += [self.cd_video_ddr.clk.eq(ClockSignal('ddr')),
                      self.cd_video_pix.clk.eq(ClockSignal('hdmi_pix'))]
        self.specials += [AsyncResetSynchronizer(self.cd_video_ddr, self.reset),
                          AsyncResetSynchronizer(self.cd_video_pix, self.reset)]
        self.pixels = pixels = ClockDomainsRenamer('video_pix')(VideoPixels(width=width, height=height))
        self.reader = reader = ClockDomainsRenamer('video_ddr')(VideoFrameReader(
            self.ui, width=width, height=height, response_timeout=response_timeout))
        self.requests = requests = ClockDomainsRenamer({'write': 'video_pix', 'read': 'video_ddr'})(AsyncFIFO(97, 2))
        self.fifo = fifo = ClockDomainsRenamer({'write': 'video_ddr', 'read': 'video_pix'})(AsyncFIFO(80, fifo_depth))

        base0, base1 = Signal(32), Signal(32)
        stride = Signal(32, reset=width * 2)
        frame_span = Signal(33, reset=height * width * 2)
        control = Signal(2)
        sequence = Signal(32)
        snapshot = Signal(98)
        admission_error, command_error = Signal(), Signal()
        self.config_cdc = config_cdc = BusSynchronizer(98, 'sys', 'video_pix')
        self.comb += config_cdc.i.eq(snapshot)
        applied = Signal(32)
        active_base, active_stride = Signal(32), Signal(32, reset=width * 2)
        active_run, active_slot = Signal(), Signal()
        self.active_base_pixel = active_base
        new_config = Signal()
        next_base, next_stride, next_epoch = Signal(32), Signal(32), Signal(32)
        next_run = Signal()
        request_overflow = Signal(32)
        self.comb += [
            new_config.eq(config_cdc.o[66:98] != applied),
            next_base.eq(Mux(new_config, config_cdc.o[:32], active_base)),
            next_stride.eq(Mux(new_config, config_cdc.o[32:64], active_stride)),
            next_run.eq(Mux(new_config, config_cdc.o[64], active_run)),
            next_epoch.eq(pixels.epoch + 1),
            pixels.enabled.eq(active_run),
            requests.din.eq(Cat(next_base, next_stride, next_epoch, next_run)),
            requests.we.eq(pixels.frame_start),
            reader.request.valid.eq(requests.readable),
            reader.request.base.eq(requests.dout[:32]),
            reader.request.stride.eq(requests.dout[32:64]),
            reader.request.epoch.eq(requests.dout[64:96]),
            reader.request.run.eq(requests.dout[96]),
            requests.re.eq(reader.request.valid & reader.request.ready),
        ]
        self.sync.video_pix += If(pixels.frame_start,
            If(new_config,
                applied.eq(config_cdc.o[66:98]), active_base.eq(config_cdc.o[:32]),
                active_stride.eq(config_cdc.o[32:64]), active_run.eq(config_cdc.o[64]),
                active_slot.eq(config_cdc.o[65])),
            If(~requests.writable, request_overflow.eq(request_overflow + 1)),
        )

        holding = Signal()
        word = Signal(256)
        word_epoch, pixel_index = Signal(32), Signal(32)
        lane = Signal(4)
        self.comb += [
            reader.source.ready.eq(~holding),
            fifo.din.eq(Cat(word[:16], pixel_index, word_epoch)), fifo.we.eq(holding),
            pixels.source.valid.eq(fifo.readable), pixels.source.data.eq(fifo.dout[:16]),
            pixels.source.index.eq(fifo.dout[16:48]), pixels.source.epoch.eq(fifo.dout[48:80]),
            fifo.re.eq(fifo.readable & pixels.source.ready),
        ]
        self.sync.video_ddr += If(reader.source.valid & reader.source.ready,
            holding.eq(1), lane.eq(0), word.eq(reader.source.data),
            word_epoch.eq(reader.source.epoch), pixel_index.eq(reader.source.block << 4),
        ).Elif(holding & fifo.writable,
            word.eq(word >> 16), pixel_index.eq(pixel_index + 1), lane.eq(lane + 1),
            If(lane == 15, holding.eq(0)),
        )

        # Registered Gray write count; the synchronized level is conservative.
        pointer_bits = (fifo_depth * 2 - 1).bit_length()
        written, read = Signal(pointer_bits), Signal(pointer_bits)
        write_next, write_gray, gray_pix = (Signal(pointer_bits) for _ in range(3))
        write_pix, difference = Signal(pointer_bits), Signal(pointer_bits)
        level = Signal(pointer_bits)
        minimum = Signal(pointer_bits, reset=fifo_depth)
        self.comb += write_next.eq(written + 1)
        self.sync.video_ddr += If(holding & fifo.writable,
            written.eq(write_next), write_gray.eq(write_next ^ (write_next >> 1)))
        self.specials += MultiReg(write_gray, gray_pix, 'video_pix')
        self.comb += [write_pix[i].eq(reduce(xor, [gray_pix[j] for j in range(i, pointer_bits)]))
                      for i in range(pointer_bits)]
        self.comb += [difference.eq(write_pix - read), level.eq(Mux(difference <= fifo_depth, difference, 0))]
        self.sync.video_pix += [
            If(fifo.re, read.eq(read + 1)),
            If(pixels.frame_start & new_config, minimum.eq(fifo_depth))
            .Elif(pixels.de & active_run & (level < minimum), minimum.eq(level)),
        ]

        pixel_fields = [('sequence', applied), ('run', active_run), ('slot', active_slot),
                        ('base', active_base), ('epoch', pixels.epoch), ('frames', pixels.frames),
                        ('underflows', pixels.underflows), ('missing', pixels.missing_pixels),
                        ('minimum', minimum), ('request_overflow', request_overflow)]
        ddr_fields = [('busy', reader.busy), ('run', reader.enabled), ('holding', holding),
                      ('address_error', reader.address_error), ('timeout', reader.timeout_error),
                      ('cancelled', reader.cancelled), ('cpu_commands', self.cpu_commands),
                      ('video_commands', self.video_commands), ('max_wait', self.max_wait),
                      ('fault', self.memory_fault)]
        status = []
        for name, fields, domain in (('pixel_status', pixel_fields, 'video_pix'),
                                     ('ddr_status', ddr_fields, 'video_ddr')):
            cdc = BusSynchronizer(sum(len(signal) for _, signal in fields), domain, 'sys')
            setattr(self, name + '_cdc', cdc)
            self.comb += cdc.i.eq(Cat(*(signal for _, signal in fields)))
            values, offset = {}, 0
            for field, signal in fields:
                values[field] = cdc.o[offset:offset + len(signal)]
                offset += len(signal)
            status.append(values)
        pix, ddr = status
        self.pending = pending = Signal()
        self.stopped = Signal()
        self.comb += [pending.eq(sequence != pix['sequence']),
                      self.stopped.eq(~pix['run'] & ~ddr['run'] & ~ddr['busy'])]
        selected_base = Signal(32)
        last_byte = Signal(33)
        valid_config = Signal()
        self.comb += [
            selected_base.eq(Mux(control[1], base1, base0)),
            last_byte.eq(selected_base + frame_span),
            valid_config.eq(~control[0] | ((selected_base >= 0x100000) & (selected_base[:5] == 0) &
                (stride >= width * 2) & (stride < 65536) & (stride[:5] == 0) & (last_byte <= (1 << 30)))),
        ]
        reg = amap.VIDEO_REGS
        reads = {
            0: amap.VIDEO_ID, 4: amap.ABI_VERSION, 8: amap.VIDEO_CAP,
            reg['CONTROL']: control,
            reg['STATUS']: Cat(pix['run'], pix['slot'], pending, ddr['busy'], self.stopped),
            reg['IRQ_STATUS']: 0, reg['IRQ_ENABLE']: 0,
            reg['LAST_ERROR']: Cat(admission_error,command_error,ddr['timeout'],ddr['fault']),
            reg['BASE0']: base0, reg['BASE1']: base1, reg['STRIDE']: stride, reg['COMMIT']: pending,
            reg['FRAMES']: pix['frames'], reg['UNDERFLOWS']: pix['underflows'],
            reg['ADDRESS_ERROR']: admission_error | ddr['address_error'], reg['TIMEOUT_ERROR']: ddr['timeout'],
            reg['ACTIVE_BASE']: pix['base'], reg['PIXEL_HZ']: 25000000,
            reg['GEOMETRY']: width | (height << 16), reg['EPOCH']: pix['epoch'],
            reg['FIFO_MIN']: pix['minimum'], reg['MAX_WAIT']: ddr['max_wait'],
            reg['READS']: ddr['video_commands'], reg['CPU_COMMANDS']: ddr['cpu_commands'],
            reg['MISSING']: pix['missing'], reg['SEQUENCE']: sequence, reg['ACTIVE_SEQUENCE']: pix['sequence'],
            reg['CANCELLED']: ddr['cancelled'], reg['ERRORS']: Cat(command_error,ddr['fault']),
            reg['CLEAR_ERRORS']: 0, reg['REQUEST_OVERFLOW']: pix['request_overflow'],
        }
        read_ok = Signal()
        self.comb += [read_ok.eq(0), self.rdata.eq(0),
            Case(self.off,{off:[read_ok.eq(1),self.rdata.eq(value)] for off,value in reads.items()}),
            self.ok.eq(Mux(self.write,
                ((self.off==reg['CONTROL']) & (self.wdata[2:]==0)) |
                (self.off==reg['BASE0']) | (self.off==reg['BASE1']) | (self.off==reg['STRIDE']) |
                ((self.off==reg['COMMIT']) & (self.wdata==1)) |
                ((self.off==reg['CLEAR_ERRORS']) & (self.wdata==1)) |
                (((self.off==reg['IRQ_STATUS']) | (self.off==reg['IRQ_ENABLE'])) & (self.wdata==0)),read_ok))]
        admitted=Signal()
        self.sync += admitted.eq(self.selected & self.write & self.ok)
        go=self.commit & admitted
        self.sync += If(go,Case(self.off,{
            reg['CONTROL']: control.eq(self.wdata[:2]),reg['BASE0']: base0.eq(self.wdata),
            reg['BASE1']: base1.eq(self.wdata),
            reg['STRIDE']: [stride.eq(self.wdata),
                frame_span.eq((height - 1) * self.wdata + width * 2)],
            reg['COMMIT']: If(pending,command_error.eq(1))
                .Elif(~valid_config,admission_error.eq(1))
                .Else(snapshot.eq(Cat(selected_base,stride,control,sequence+1)),sequence.eq(sequence+1)),
            reg['CLEAR_ERRORS']:[admission_error.eq(0),command_error.eq(0)],
        }))
