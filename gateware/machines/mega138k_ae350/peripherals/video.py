"""Continuous HDMI timing and tagged RGB565 scanout in the pixel domain."""
from functools import reduce
from operator import xor
from gateware.machines.mega138k_ae350.video_modes import MODES, BYTES_PER_PIXEL, pll_config
from migen import Cat, Case, ClockDomain, ClockDomainsRenamer, ClockSignal, Constant, If, Memory, Mux, Signal
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
                 v_front=10, v_sync=2, v_back=33, runtime=False):
        self.source = source = stream.Endpoint([('data', 32 if runtime else 16), ('index', 32), ('epoch', 32)])
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
        reset_height = height
        self.format = Signal(3)
        h_positive, v_positive = 0, 0
        if runtime:
            defaults = dict(width=width, height=height, h_front=h_front, h_sync=h_sync,
                            h_back=h_back, v_front=v_front, v_sync=v_sync, v_back=v_back)
            for name, value in defaults.items():
                setattr(self, name, Signal(12, reset=value))
            width, height = self.width, self.height
            h_front, h_sync, h_back = self.h_front, self.h_sync, self.h_back
            v_front, v_sync, v_back = self.v_front, self.v_sync, self.v_back
            self.h_positive, self.v_positive = Signal(), Signal()
            h_positive, v_positive = self.h_positive, self.v_positive
        h_total = width + h_front + h_sync + h_back
        v_total = height + v_front + v_sync + v_back
        self.x = x = Signal(12) if runtime else Signal(max=h_total)
        self.y = y = Signal(12, reset=reset_height) if runtime else Signal(max=v_total, reset=height)
        match = Signal()
        red, green, blue = Signal(8), Signal(8), Signal(8)
        pixel = Signal(32 if runtime else 16)
        pattern = Signal(24)
        started = Signal()
        self.comb += [
            self.de.eq((x < width) & (y < height)),
            self.vblank.eq(y >= height),
            self.frame_start.eq((x == 0) & (y == height)),
            self.hsync.eq(~((x >= width + h_front) & (x < width + h_front + h_sync)) ^ h_positive),
            self.vsync.eq(~((y >= height + v_front) & (y < height + v_front + v_sync)) ^ v_positive),
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
        if runtime:
            # Formats: 0=RGB565 (above), 1/2=RGB888/XRGB8888 B,G,R[,X] bytes,
            # 3=XRGB1555 X[15],R[14:10],G[9:5],B[4:0] with 5->8 expansion,
            # 4=INDEX8 (uses RGB565 expansion of looked-up 16-bit color).
            self.comb += If(self.format == 3,
                red.eq(Cat(pixel[12:15], pixel[10:15])),
                green.eq(Cat(pixel[7:10], pixel[5:10])),
                blue.eq(Cat(pixel[2:5], pixel[:5])),
            ).Elif((self.format == 1) | (self.format == 2),
                red.eq(pixel[16:24]), green.eq(pixel[8:16]), blue.eq(pixel[:8]))
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


class HDMIHotplugStatus(LiteXModule):
    """Sys-domain HPD: synchronize input and require 2ms stable at sys50."""
    def __init__(self, debounce_cycles=100000):
        if debounce_cycles < 2:
            raise ValueError('HPD debounce must span at least two clocks')
        self.input = Signal()
        self.current = Signal()
        self.connected = Signal()
        self.events = Signal(2)
        self.pending = Signal(2)
        self.enable = Signal(2)
        self.clear = Signal(2)
        self.irq = Signal()
        self.specials += MultiReg(self.input, self.current, 'sys')
        count = Signal(max=debounce_cycles)
        self.comb += [self.events.eq(0),
            If((self.current != self.connected) & (count == debounce_cycles-1),
               self.events.eq(Mux(self.current, 1, 2))),
            self.irq.eq((self.pending & self.enable) != 0)]
        # Pending survives masking; a new edge wins over W1C in the same cycle.
        self.sync += self.pending.eq((self.pending & ~self.clear) | self.events)
        self.sync += If(self.current == self.connected, count.eq(0)).Else(
            If(count == debounce_cycles-1,
               self.connected.eq(self.current), count.eq(0)
            ).Else(count.eq(count+1)))


class VideoPaletteLookup(LiteXModule):
    """Synchronous BRAM palette lookup for INDEX8 mode.

    When indexed=1 (INDEX8 mode), sink.data[:8] is looked up in the 512x16 dual-port RAM
    (2 banks of 256 RGB565 entries) using the specified palette_bank.
    When indexed=0, sink passes through registered data with 1-cycle pipeline latency.
    Zero combinational path cuts timing between FIFO and scanner.
    """
    def __init__(self, palette_port):
        self.sink = sink = stream.Endpoint([('data', 32), ('index', 32), ('epoch', 32)])
        self.source = source = stream.Endpoint([('data', 32), ('index', 32), ('epoch', 32)])
        self.indexed = Signal()
        self.palette_bank = Signal()

        have = Signal()
        bypass_data = Signal(32)
        is_indexed_reg = Signal()
        index_reg = Signal(8)
        bank_reg = Signal()
        pix_index_reg = Signal(32)
        epoch_reg = Signal(32)

        accept = Signal()
        consume = Signal()

        self.comb += [
            sink.ready.eq(~have | source.ready),
            accept.eq(sink.valid & sink.ready),
            consume.eq(have & source.ready),

            source.valid.eq(have),
            source.data.eq(Mux(is_indexed_reg, Cat(palette_port.dat_r, Constant(0, 16)), bypass_data)),
            source.index.eq(pix_index_reg),
            source.epoch.eq(epoch_reg),

            palette_port.adr.eq(Cat(
                Mux(accept, sink.data[:8], index_reg),
                Mux(accept, self.palette_bank, bank_reg)
            )),
        ]

        self.sync += [
            If(consume, have.eq(0)),
            If(accept,
                have.eq(1),
                is_indexed_reg.eq(self.indexed),
                bypass_data.eq(sink.data),
                index_reg.eq(sink.data[:8]),
                bank_reg.eq(self.palette_bank),
                pix_index_reg.eq(sink.index),
                epoch_reg.eq(sink.epoch)
            )
        ]


class FabricVideo(LiteXModule):
    """APB snapshots, atomic vblank apply, two coherent FIFOs and DDR scanout.

    CONTROL/BASE0/BASE1/STRIDE stage settings; COMMIT snapshots them once.
    A pending snapshot cannot be overwritten. The selected base and run bit
    change together at vblank. Disabling still sends a stop descriptor to the
    reader, and STOPPED waits for its outstanding read to retire.
    """
    def __init__(self, width=640, height=480, fifo_depth=2048, response_timeout=1 << 18, runtime=False,
                 hpd_supported=False, hpd_debounce_cycles=100000):
        self.hpd_input = Signal()
        self.hpd_connected = Signal()
        self.irq = Signal()
        self.hpd_pending, self.hpd_irq_enable = Signal(2), Signal(2)
        hpd_sync = Signal()
        if hpd_supported:
            self.hpd_monitor = HDMIHotplugStatus(hpd_debounce_cycles)
            self.comb += [self.hpd_monitor.input.eq(self.hpd_input),
                          hpd_sync.eq(self.hpd_monitor.current),
                          self.hpd_connected.eq(self.hpd_monitor.connected),
                          self.hpd_pending.eq(self.hpd_monitor.pending),
                          self.hpd_monitor.enable.eq(self.hpd_irq_enable),
                          self.irq.eq(self.hpd_monitor.irq)]
        self.video_enable = Signal(reset=1)
        self.mode = Signal(max=len(MODES)+1)
        self.format = Signal(3)
        self.mode_busy = Signal()
        self.mode_apply = Signal()
        self.mode_sequence = Signal(32,reset=1)
        mode_ack_pixel,mode_ack_ddr=Signal(32),Signal(32)
        ack_pixel_sys,ack_ddr_sys=Signal(32),Signal(32)
        self.clock_reset = Signal()
        self.clock_locked = Signal(reset=1)
        self.mode_hold = Signal()
        self.pipeline_reset = Signal()
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.commit = Signal()
        self.selected = Signal()
        self.rdata = Signal(32)
        self.ok = Signal()
        self.reset = Signal()
        self.comb += self.pipeline_reset.eq(self.reset | self.mode_hold)
        self.ui = DDR3UI()
        self.cpu_commands = Signal(32)
        self.video_commands = Signal(32)
        self.max_wait = Signal(32)
        self.memory_fault = Signal()
        self.cd_video_ddr = ClockDomain('video_ddr')
        self.cd_video_pix = ClockDomain('video_pix')
        self.comb += [self.cd_video_ddr.clk.eq(ClockSignal('ddr')),
                      self.cd_video_pix.clk.eq(ClockSignal('hdmi_pix'))]
        self.specials += [AsyncResetSynchronizer(self.cd_video_ddr, self.pipeline_reset),
                          AsyncResetSynchronizer(self.cd_video_pix, self.pipeline_reset)]
        palette = Memory(16, 512, name='video_palette')
        palette_write = palette.get_port(write_capable=True, has_re=True, clock_domain='sys')
        palette_read = palette.get_port(clock_domain='video_pix')
        self.specials += palette, palette_write, palette_read
        palette_addr = Signal(9)
        self.pixels = pixels = ClockDomainsRenamer('video_pix')(VideoPixels(width=width, height=height, runtime=runtime))
        self.reader = reader = ClockDomainsRenamer('video_ddr')(VideoFrameReader(
            self.ui, width=width, height=height, response_timeout=response_timeout, runtime=runtime))
        self.requests = requests = ClockDomainsRenamer({'write': 'video_pix', 'read': 'video_ddr'})(AsyncFIFO(161, 2))
        self.fifo = fifo = ClockDomainsRenamer({'write': 'video_ddr', 'read': 'video_pix'})(AsyncFIFO(96 if runtime else 80, fifo_depth))
        # Epoch/index checks at the consumer must not feed the current BRAM
        # output back into its own next read address in one pixel clock.
        pixel_bits = 32 if runtime else 16
        self.pixel_output = output = ClockDomainsRenamer('video_pix')(stream.Buffer(
            [('data',pixel_bits),('index',32),('epoch',32)], pipe_valid=True, pipe_ready=True))
        self.palette_lookup = ClockDomainsRenamer('video_pix')(VideoPaletteLookup(palette_read))
        active_bank, next_bank = Signal(), Signal()
        self.comb += [output.sink.valid.eq(fifo.readable),
            output.sink.data.eq(fifo.dout[:pixel_bits]),
            output.sink.index.eq(fifo.dout[pixel_bits:pixel_bits+32]),
            output.sink.epoch.eq(fifo.dout[pixel_bits+32:pixel_bits+64]),
            fifo.re.eq(fifo.readable & output.sink.ready),
            output.source.connect(self.palette_lookup.sink),
            self.palette_lookup.indexed.eq(pixels.format == 4),
            self.palette_lookup.palette_bank.eq(active_bank),
            self.palette_lookup.source.connect(pixels.source)]

        base0, base1 = Signal(32), Signal(32)
        stride = Signal(32, reset=width * 2)
        wrap_start, wrap_end = Signal(32), Signal(32)
        active_wrap_start, active_wrap_end = Signal(32), Signal(32)
        next_wrap_start, next_wrap_end = Signal(32), Signal(32)
        frame_span = Signal(33, reset=height * width * 2)
        control = Signal(3)
        sequence = Signal(32)
        snapshot = Signal(163)
        admission_error, command_error = Signal(), Signal()
        self.config_cdc = config_cdc = BusSynchronizer(163, 'sys', 'video_pix')
        self.comb += config_cdc.i.eq(snapshot)
        applied = Signal(32)
        active_base, active_stride = Signal(32), Signal(32, reset=width * 2)
        active_run, active_slot = Signal(), Signal()
        self.active_base_pixel = active_base
        new_config = Signal()
        next_base, next_stride, next_epoch = Signal(32), Signal(32), Signal(32)
        next_run = Signal()
        request_overflow = Signal(32)
        request_sink = reader.request
        if runtime:
            self.request_buffer = ClockDomainsRenamer('video_ddr')(stream.Buffer(
                reader.request.description,pipe_valid=True,pipe_ready=False))
            request_sink = self.request_buffer.sink
            mode_busy_ddr=Signal()
            self.specials += MultiReg(self.mode_busy,mode_busy_ddr,'video_ddr')
            self.comb += self.request_buffer.source.connect(reader.request,omit={'run'})
            # A reset/clock restart may temporarily expose old FIFO pointers.
            # No descriptor can start a read until fresh mode acknowledgments
            # and the stopped snapshot have completed.
            self.comb += reader.request.run.eq(self.request_buffer.source.run & ~mode_busy_ddr)
        self.comb += [
            new_config.eq(config_cdc.o[131:163] != applied),
            next_base.eq(Mux(new_config, config_cdc.o[:32], active_base)),
            next_stride.eq(Mux(new_config, config_cdc.o[32:64], active_stride)),
            next_run.eq(Mux(new_config, config_cdc.o[64], active_run)),
            next_bank.eq(Mux(new_config, config_cdc.o[66], active_bank)),
            next_wrap_start.eq(Mux(new_config, config_cdc.o[67:99], active_wrap_start)),
            next_wrap_end.eq(Mux(new_config, config_cdc.o[99:131], active_wrap_end)),
            next_epoch.eq(pixels.epoch + 1),
            pixels.enabled.eq(active_run),
            requests.din.eq(Cat(next_base, next_stride, next_epoch, next_run, next_wrap_start, next_wrap_end)),
            requests.we.eq(pixels.frame_start),
            request_sink.valid.eq(requests.readable),
            request_sink.base.eq(requests.dout[:32]),
            request_sink.stride.eq(requests.dout[32:64]),
            request_sink.epoch.eq(requests.dout[64:96]),
            request_sink.run.eq(requests.dout[96]),
            request_sink.wrap_start.eq(requests.dout[97:129]),
            request_sink.wrap_end.eq(requests.dout[129:161]),
            requests.re.eq(request_sink.valid & request_sink.ready),
        ]
        self.sync.video_pix += If(pixels.frame_start,
            If(new_config,
                applied.eq(config_cdc.o[131:163]), active_base.eq(config_cdc.o[:32]),
                active_stride.eq(config_cdc.o[32:64]), active_run.eq(config_cdc.o[64]),
                active_slot.eq(config_cdc.o[65]), active_bank.eq(config_cdc.o[66]),
                active_wrap_start.eq(config_cdc.o[67:99]), active_wrap_end.eq(config_cdc.o[99:131])),
            If(~requests.writable, request_overflow.eq(request_overflow + 1)),
        )

        holding = Signal()
        if runtime:
            # Mode changes are accepted only after drained stop. This stable
            # bundle settles while both FIFO ends are held in reset.
            fields = dict(width=12,height=12,h_front=12,h_sync=12,h_back=12,
                          v_front=12,v_sync=12,v_back=12,h_positive=1,v_positive=1)
            mode_values = {name: Signal(bits) for name,bits in fields.items()}
            # Custom mode (index len(MODES)): timing comes from the CUS register
            # bank, KMS-style. Registers are packed to keep the APB map small:
            #   CUS_WH:  width[11:0] | height[27:16]
            #   CUS_H:   h_front[11:0] | h_sync[23:12] | h_back[31:24] (<=255)
            #   CUS_V:   v_front[7:0] | v_sync[15:8] | v_back[23:16] |
            #            h_positive[24] | v_positive[25]
            #   CUS_PLL: idiv[1:0] | m8[11:2] | pixel_div[18:12] | serial_div[25:19]
            #   CUS_PCLK: requested pixel Hz (software-computed, informational)
            self.cus_wh, self.cus_h, self.cus_v = Signal(32), Signal(32), Signal(32)
            self.cus_pll, self.cus_pclk = Signal(32), Signal(32)
            custom_values = dict(
                width=self.cus_wh[0:12], height=self.cus_wh[16:28],
                h_front=self.cus_h[0:12], h_sync=self.cus_h[12:24],
                h_back=Cat(self.cus_h[24:32], Constant(0,4)),
                v_front=Cat(self.cus_v[0:8], Constant(0,4)),
                v_sync=Cat(self.cus_v[8:16], Constant(0,4)),
                v_back=Cat(self.cus_v[16:24], Constant(0,4)),
                h_positive=self.cus_v[24], v_positive=self.cus_v[25])
            case = {i: [mode_values[n].eq(getattr(m,n)) for n in fields]
                    for i,m in enumerate(MODES)}
            case[len(MODES)] = [mode_values[n].eq(custom_values[n]) for n in fields]
            self.comb += Case(self.mode, case)
            bpp = Signal(3)
            row_bytes = Signal(16)
            self.comb += Case(self.format,{i:bpp.eq(v) for i,v in enumerate(BYTES_PER_PIXEL)})
            self.sync += row_bytes.eq(mode_values['width'] * bpp)
            # Custom-mode gateware validation: geometry, totals, PLL ranges
            # and the 74.25MHz pixel-clock ceiling (same profile as the
            # fixed table) are checked; divider consistency is the driver's
            # job (KMS computes PLL parameters). Any failure rejects the
            # MODE_CONFIG write with command_error.
            cus_idiv, cus_m8 = self.cus_pll[0:2], self.cus_pll[2:12]
            cus_pixdiv, cus_serdiv = self.cus_pll[12:19], self.cus_pll[19:26]
            cus_vco = Signal(37)  # 50e6 * m8
            cus_h_total, cus_v_total = Signal(14), Signal(14)
            cus_bpp = Signal(3)
            cus_row_bytes = Signal(16)
            cus_bad = Signal()
            self.comb += [
                Case(self.wdata[8:11], {i: cus_bpp.eq(v) for i, v in enumerate(BYTES_PER_PIXEL)}),
                cus_row_bytes.eq(self.cus_wh[0:12] * cus_bpp),
                cus_vco.eq(Constant(50000000) * cus_m8),
                cus_h_total.eq(self.cus_wh[0:12]+self.cus_h[0:12]+self.cus_h[12:24]+Cat(self.cus_h[24:32],Constant(0,4))),
                cus_v_total.eq(self.cus_wh[16:28]+Cat(self.cus_v[0:8],Constant(0,4))+Cat(self.cus_v[8:16],Constant(0,4))+Cat(self.cus_v[16:24],Constant(0,4))),
                cus_bad.eq((self.cus_wh[0:12] == 0) | (self.cus_wh[16:28] == 0) |
                    (cus_row_bytes[:5] != 0) |
                    (cus_h_total >= 4096) | (cus_v_total >= 4096) |
                    (cus_idiv != 1) & (cus_idiv != 2) |
                    (cus_m8 < 16) | (cus_m8 > 1023) |
                    (cus_pixdiv != 5 * cus_serdiv) |
                    (cus_serdiv < 2) | (cus_serdiv > 25) |
                    (cus_vco < Constant(650000000) * (cus_idiv << 3)) |
                    (cus_vco > Constant(1300000000) * (cus_idiv << 3)) |
                    (cus_vco > Constant(74250000) * (cus_idiv << 3) * cus_pixdiv)),
            ]
            for domain in ('video_pix','video_ddr'):
                bundle = Cat(*(mode_values.values()),self.format,row_bytes,self.mode_sequence)
                cdc = BusSynchronizer(len(bundle),'sys',domain)
                setattr(self,domain+'_mode_cdc',cdc)
                self.comb += cdc.i.eq(bundle)
                offset=0
                for name,bits in fields.items():
                    if domain=='video_pix': self.comb += getattr(pixels,name).eq(cdc.o[offset:offset+bits])
                    elif name=='height': self.comb += reader.rows.eq(cdc.o[offset:offset+bits])
                    offset+=bits
                token=cdc.o[offset+19:offset+51]
                if domain=='video_pix':
                    self.comb += pixels.format.eq(cdc.o[offset:offset+3])
                    self.sync.video_pix += If(token!=0,mode_ack_pixel.eq(token))
                else:
                    ddr_format=cdc.o[offset:offset+3]
                    self.comb += reader.row_bytes.eq(cdc.o[offset+3:offset+19])
                    self.sync.video_ddr += If(token!=0,mode_ack_ddr.eq(token))
            self.specials += [MultiReg(mode_ack_pixel,ack_pixel_sys,'sys'),
                              MultiReg(mode_ack_ddr,ack_ddr_sys,'sys')]
            # Byte reservoir preserves packed RGB888 pixels across 32-byte
            # burst boundaries. X bytes are consumed but never transmitted.
            reservoir=Signal(288)
            available=Signal(6)
            step=Signal(3)
            word_epoch,pixel_index=Signal(32),Signal(21)
            step_next=Signal(3)
            self.comb += Case(ddr_format,{i:step_next.eq(v) for i,v in enumerate(BYTES_PER_PIXEL)})
            self.sync.video_ddr += step.eq(step_next)
            self.pixel_buffer = ClockDomainsRenamer('video_ddr')(stream.Buffer(
                [('data',32),('index',32),('epoch',32)],pipe_valid=True,pipe_ready=True))
            buffered = self.pixel_buffer
            self.comb += [reader.source.ready.eq(~holding),
                buffered.sink.valid.eq(holding),buffered.sink.data.eq(reservoir[:32]),
                buffered.sink.index.eq(pixel_index),buffered.sink.epoch.eq(word_epoch),
                buffered.source.ready.eq(fifo.writable),
                fifo.din.eq(Cat(buffered.source.data,buffered.source.index,buffered.source.epoch)),
                fifo.we.eq(buffered.source.valid)]
            self.sync.video_ddr += If(reader.source.valid & reader.source.ready,
                holding.eq(1),
                If(reader.source.block==0,
                    reservoir.eq(reader.source.data),available.eq(32),pixel_index.eq(0),
                    word_epoch.eq(reader.source.epoch),
                ).Else(reservoir.eq(reservoir | (reader.source.data << Cat(Constant(0,3),available[:2]))),
                       available.eq(available+32)),
            ).Elif(holding & buffered.sink.ready,
                reservoir.eq(reservoir >> Cat(Constant(0,3),step)),available.eq(available-step),
                pixel_index.eq(pixel_index+1),
                If(available < Cat(Constant(0,1),step),holding.eq(0)))
        else:
            word = Signal(256)
            word_epoch, pixel_index = Signal(32), Signal(32)
            lane = Signal(4)
            self.comb += [
                reader.source.ready.eq(~holding),
                fifo.din.eq(Cat(word[:16], pixel_index, word_epoch)), fifo.we.eq(holding),
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
        self.sync.video_ddr += If(fifo.we & fifo.writable,
            written.eq(write_next), write_gray.eq(write_next ^ (write_next >> 1)))
        self.specials += MultiReg(write_gray, gray_pix, 'video_pix')
        self.sync.video_pix += [write_pix[i].eq(reduce(xor, [gray_pix[j] for j in range(i, pointer_bits)]))
                      for i in range(pointer_bits)]
        self.comb += [difference.eq(write_pix - read), level.eq(Mux(difference <= fifo_depth, difference, 0))]
        self.sync.video_pix += [
            If(fifo.re, read.eq(read + 1)),
            If(pixels.frame_start & new_config, minimum.eq(fifo_depth))
            .Elif(pixels.de & active_run & (level < minimum), minimum.eq(level)),
        ]

        pixel_fields = [('sequence', applied), ('run', active_run), ('slot', active_slot),
                        ('bank', active_bank), ('base', active_base), ('epoch', pixels.epoch),
                        ('frames', pixels.frames), ('underflows', pixels.underflows),
                        ('missing', pixels.missing_pixels), ('minimum', minimum),
                        ('request_overflow', request_overflow)]
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
        if runtime:
            width = mode_values['width']
            height = mode_values['height']
        else:
            row_bytes = width * 2
        selected_base = Signal(32)
        last_byte = Signal(33)
        wrap_enabled = Signal()
        wrap_valid = Signal()
        wrap_len_ok = Signal()
        self.sync += wrap_len_ok.eq(wrap_end - wrap_start >= row_bytes)
        valid_config = Signal()
        self.comb += [
            selected_base.eq(Mux(control[1], base1, base0)),
            last_byte.eq(selected_base + frame_span),
            wrap_enabled.eq(wrap_end != 0),
            wrap_valid.eq((wrap_start[:5] == 0) & (wrap_end[:5] == 0) &
                          (wrap_start >= 0x100000) & (wrap_end <= (1 << 30)) &
                          (wrap_end > wrap_start) &
                          wrap_len_ok &
                          (selected_base >= wrap_start) & (selected_base < wrap_end)),
            valid_config.eq(~control[0] | ((selected_base >= 0x100000) & (selected_base[:5] == 0) &
                (stride >= row_bytes) & (stride < 65536) & (stride[:5] == 0) &
                Mux(wrap_enabled, wrap_valid, last_byte <= (1 << 30)))),
        ]
        reg = amap.VIDEO_REGS
        ctrl_reads = {
            0: amap.VIDEO_ID, 4: amap.ABI_VERSION, 8: (amap.VIDEO_CAP & ~0x180) | (0x180 if hpd_supported else 0) if runtime else 15,
            reg['CONTROL']: control,
            reg['STATUS']: Cat(pix['run'], pix['slot'], pending, ddr['busy'], self.stopped, pix['bank']),
            reg['IRQ_STATUS']: self.hpd_pending, reg['IRQ_ENABLE']: self.hpd_irq_enable,
            reg['LAST_ERROR']: Cat(admission_error,command_error,ddr['timeout'],ddr['fault']),
            reg['BASE0']: base0, reg['BASE1']: base1, reg['STRIDE']: stride, reg['COMMIT']: pending,
            reg['WRAP_START']: wrap_start, reg['WRAP_END']: wrap_end,
            reg['PALETTE_ADDR']: palette_addr, reg['PALETTE_DATA']: 0,
            reg['PALETTE_READBACK']: palette_write.dat_r, reg['PALETTE_ACTIVE']: pix['bank'],
            reg['FRAMES']: pix['frames'], reg['UNDERFLOWS']: pix['underflows'],
            reg['ADDRESS_ERROR']: admission_error | ddr['address_error'], reg['TIMEOUT_ERROR']: ddr['timeout'],
            reg['ACTIVE_BASE']: pix['base'], reg['PIXEL_HZ']: 25000000,
            reg['GEOMETRY']: Cat(width,height) if runtime else width | (height << 16), reg['EPOCH']: pix['epoch'],
            reg['FIFO_MIN']: pix['minimum'], reg['MAX_WAIT']: ddr['max_wait'],
            reg['READS']: ddr['video_commands'], reg['CPU_COMMANDS']: ddr['cpu_commands'],
            reg['MISSING']: pix['missing'], reg['SEQUENCE']: sequence, reg['ACTIVE_SEQUENCE']: pix['sequence'],
            reg['CANCELLED']: ddr['cancelled'], reg['ERRORS']: Cat(command_error,ddr['fault']),
            reg['CLEAR_ERRORS']: 0, reg['REQUEST_OVERFLOW']: pix['request_overflow'],
        }
        info_reads = {}
        if runtime:
            geometry=Signal(32)
            frequency=Signal(32)
            self.comb += [geometry.eq(mode_values['width'] | (mode_values['height'] << 16)),
                Case(self.mode,{i:frequency.eq(pll_config(m.pixel_hz)['actual_hz']) for i,m in enumerate(MODES)}),
                If(self.mode==len(MODES),frequency.eq(self.cus_pclk))]
            ctrl_reads.update({reg['MODE']:self.mode,reg['FORMAT']:self.format,
                          reg['MODE_STATUS']:self.mode_busy,reg['MODE_COUNT']:len(MODES),
                          reg['ENABLE']:self.video_enable,
                          reg['HPD']:Cat(self.hpd_connected,hpd_sync),
                          reg['GEOMETRY']:geometry,reg['PIXEL_HZ']:frequency,
                          reg['CUS_WH']:self.cus_wh,reg['CUS_H']:self.cus_h,
                          reg['CUS_V']:self.cus_v,reg['CUS_PLL']:self.cus_pll,
                          reg['CUS_PCLK']:self.cus_pclk})
            for i,mode in enumerate(MODES):
                actual=pll_config(mode.pixel_hz)['actual_hz']
                info=(mode.width | (mode.height<<16),actual,mode.h_total | (mode.v_total<<16),
                    mode.h_front | (mode.h_sync<<10) | (mode.h_back<<20),
                    mode.v_front | (mode.v_sync<<10) | (mode.v_back<<20),
                    int(mode.h_positive) | (int(mode.v_positive)<<1),
                    round(actual*1000/(mode.h_total*mode.v_total)),mode.pixel_hz)
                info_reads.update({(reg['MODE_INFO_BASE']+i*32+j*4) & 0xff: v for j,v in enumerate(info)})
        read_ok = Signal()
        ctrl_rdata, info_rdata = Signal(32), Signal(32)
        ctrl_ok, info_ok = Signal(), Signal()
        self.comb += [
            ctrl_ok.eq(0), ctrl_rdata.eq(0),
            info_ok.eq(0), info_rdata.eq(0),
            Case(self.off[:8], {off: [ctrl_ok.eq(1), ctrl_rdata.eq(value)] for off, value in ctrl_reads.items()}),
            Case(self.off[:8], {off: [info_ok.eq(1), info_rdata.eq(value)] for off, value in info_reads.items()}),
            self.rdata.eq(Mux(self.off[8], info_rdata, ctrl_rdata)),
            read_ok.eq(Mux(self.off[8], info_ok, ctrl_ok)),
            self.ok.eq(Mux(self.write,
                ((self.off==reg['CONTROL']) & (self.wdata[3:]==0)) |
                (self.off==reg['BASE0']) | (self.off==reg['BASE1']) | (self.off==reg['STRIDE']) |
                (self.off==reg['WRAP_START']) | (self.off==reg['WRAP_END']) |
                ((self.off==reg['PALETTE_ADDR']) & (self.wdata[9:]==0)) |
                ((self.off==reg['PALETTE_DATA']) & (self.wdata[16:]==0)) |
                ((self.off==reg['COMMIT']) & (self.wdata==1)) |
                ((self.off==reg['CLEAR_ERRORS']) & (self.wdata==1)) |
                (((self.off==reg['IRQ_STATUS']) | (self.off==reg['IRQ_ENABLE'])) & (self.wdata < (4 if hpd_supported else 1))) |
                (((self.off==reg['ENABLE']) & (self.wdata<2)) if runtime else 0) |
                (((self.off==reg['MODE_CONFIG']) & (self.wdata[:8]<len(MODES)+1) &
                  (self.wdata[8:11]<5) & (self.wdata[11:]==0)) if runtime else 0) |
                ((self.off==reg['CUS_WH']) | (self.off==reg['CUS_H']) |
                 (self.off==reg['CUS_V']) | (self.off==reg['CUS_PLL']) |
                 (self.off==reg['CUS_PCLK']) if runtime else 0),read_ok))]
        admitted=Signal()
        self.sync += admitted.eq(self.selected & self.write & self.ok)
        go=self.commit & admitted
        palette_we = Signal()
        self.comb += [
            palette_we.eq(go & (self.off == reg['PALETTE_DATA'])),
            palette_write.adr.eq(palette_addr),
            palette_write.dat_w.eq(self.wdata[:16]),
            palette_write.we.eq(palette_we),
            palette_write.re.eq(~palette_we),
        ]
        if hpd_supported:
            self.comb += self.hpd_monitor.clear.eq(Mux(go & (self.off==reg['IRQ_STATUS']), self.wdata[:2], 0))
            self.sync += If(go & (self.off==reg['IRQ_ENABLE']), self.hpd_irq_enable.eq(self.wdata[:2]))
        if runtime:
            # Reject mode writes while ownership is live. The PLL controller
            # holds reset then waits for lock and CDC settling before ready.
            timer=Signal(16)
            lock=Signal()
            cus_wr=Signal()
            self.comb += cus_wr.eq((self.mode!=len(MODES)) | (~self.video_enable & self.stopped & ~self.mode_busy))
            self.specials += MultiReg(self.clock_locked,lock)
            self.sync += [self.mode_apply.eq(0),
                If(self.mode_busy,
                    If((timer>=512) & ~lock,timer.eq(512))
                    .Elif(timer!=65535,timer.eq(timer+1)),
                    If((timer>=1024) & lock & ~pending & self.stopped &
                       (ack_pixel_sys==self.mode_sequence) & (ack_ddr_sys==self.mode_sequence),
                       self.mode_busy.eq(0))),
                If(go & (self.off==reg['MODE_CONFIG']),
                    If(self.video_enable | ~self.stopped | pending | self.mode_busy,command_error.eq(1))
                    .Elif((self.wdata[:8]==len(MODES)) & cus_bad,command_error.eq(1))
                    .Else(self.mode.eq(self.wdata[:8]),self.format.eq(self.wdata[8:11]),
                          self.mode_busy.eq(1),self.mode_apply.eq(1),timer.eq(0),
                          self.mode_sequence.eq(Mux(self.mode_sequence==0xffffffff,1,self.mode_sequence+1)))),
                If(go & (self.off==reg['ENABLE']),
                    If(self.mode_busy | pending | ~self.stopped,command_error.eq(1))
                    .Else(self.video_enable.eq(self.wdata[0]))),
                If(go & (self.off==reg['CUS_WH']) & cus_wr,self.cus_wh.eq(self.wdata)),
                If(go & (self.off==reg['CUS_H']) & cus_wr,self.cus_h.eq(self.wdata)),
                If(go & (self.off==reg['CUS_V']) & cus_wr,self.cus_v.eq(self.wdata)),
                If(go & (self.off==reg['CUS_PLL']) & cus_wr,self.cus_pll.eq(self.wdata)),
                If(go & (self.off==reg['CUS_PCLK']) & cus_wr,self.cus_pclk.eq(self.wdata))]
            self.comb += [self.clock_reset.eq(self.mode_busy & (timer<32)),
                          self.mode_hold.eq(self.mode_busy & ((timer<768) | ~lock))]
            # Mode-dependent row span is recomputed only for admission; the
            # reader independently validates every descriptor in the DDR domain.
            self.sync += frame_span.eq((height-1)*stride[:16]+row_bytes)
        self.sync += If(go,Case(self.off,{
            reg['CONTROL']: control.eq(self.wdata[:3]),reg['BASE0']: base0.eq(self.wdata),
            reg['BASE1']: base1.eq(self.wdata),
            reg['STRIDE']: [stride.eq(self.wdata)] + ([] if runtime else [frame_span.eq((height - 1) * self.wdata[:16] + width * 2)]),
            reg['WRAP_START']: wrap_start.eq(self.wdata),
            reg['WRAP_END']: wrap_end.eq(self.wdata),
            reg['PALETTE_ADDR']: palette_addr.eq(self.wdata[:9]),
            reg['PALETTE_DATA']: palette_addr.eq(palette_addr + 1),
            reg['COMMIT']: If(pending | self.mode_busy | ((control[0] & ~self.video_enable) if runtime else 0),command_error.eq(1))
                .Elif(~valid_config,admission_error.eq(1))
                .Else(snapshot.eq(Cat(selected_base,stride,control,wrap_start,wrap_end,sequence+1)),sequence.eq(sequence+1)),
            reg['CLEAR_ERRORS']:[admission_error.eq(0),command_error.eq(0)],
        }))
