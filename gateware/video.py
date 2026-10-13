"""480x272 RGB565/INDEX8 scanout. One bounded DMA frame per scan period."""
from gateware.memory_map import RAM_BASE, RAM_SIZE
from gateware.csr_layout import packed_status
from migen import Signal, Memory, If, Cat, Mux, ClockSignal, ClockDomainsRenamer
from migen.genlib.cdc import MultiReg, PulseSynchronizer
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStorage, CSRStatus
from litex.build.io import DDROutput, SDROutput

WIDTH, HEIGHT = 480, 272
FRAME_BYTES = WIDTH*HEIGHT*2


class PalettePixels(LiteXModule):
    """Packed INDEX8 lookup ahead of the scanner; RGB565 bypasses unchanged.

    The synchronous RAM prefetches the next color at the consuming edge.
    Low byte is first, last belongs to the high byte, and stalls retain color.
    """
    def __init__(self, palette_port):
        self.sink = sink = stream.Endpoint([('data',16)])
        self.source = source = stream.Endpoint([('data',16)])
        self.indexed = Signal()
        self.palette_bank = Signal()
        have,second,last,bank = (Signal() for _ in range(4))
        word = Signal(16)
        index = Signal(8)
        accept,consume = Signal(),Signal()
        self.comb += [
            sink.ready.eq(Mux(self.indexed,~have | (second & source.ready),source.ready)),
            source.valid.eq(Mux(self.indexed,have,sink.valid)),
            source.data.eq(Mux(self.indexed,palette_port.dat_r,sink.data)),
            source.last.eq(Mux(self.indexed,last & second,sink.last)),
            accept.eq(self.indexed & sink.valid & sink.ready),
            consume.eq(self.indexed & have & source.ready),
            palette_port.adr.eq(Cat(Mux(accept,sink.data[:8],
                Mux(consume & ~second,word[8:],index)),Mux(accept,self.palette_bank,bank))),
        ]
        self.sync += If(~self.indexed,have.eq(0),second.eq(0)).Else(
            If(consume,
                If(~second,second.eq(1),index.eq(word[8:])).Else(have.eq(0))),
            If(accept,have.eq(1),second.eq(0),word.eq(sink.data),last.eq(sink.last),
                index.eq(sink.data[:8]),bank.eq(self.palette_bank)))


class LCDScan(LiteXModule):
    """Pure pixel-domain scanner; last-marked input permits underrun recovery."""
    def __init__(self):
        self.sink = sink = stream.Endpoint([('data',16)])
        self.enable = Signal()
        self.frame_start = Signal()
        self.frame_done = Signal()
        self.underflow = Signal()
        self.hsync = Signal(reset=1)
        self.vsync = Signal(reset=1)
        self.de = Signal()
        self.pixel = Signal(16)
        self.h = h = Signal(max=525)
        self.v = v = Signal(max=286)
        running = Signal()
        discard = Signal()
        active = Signal()
        first = Signal()
        draw = Signal()
        self.comb += [
            active.eq((h>=45)&(v>=14)),
            first.eq((h==45)&(v==14)),
            self.frame_start.eq((h==0)&(v==0)),
            self.hsync.eq(~((h>=2)&(h<43))),
            self.vsync.eq(~((v>=2)&(v<12))),
            self.de.eq(active),
            draw.eq(active & self.enable & ~discard & (running|first)),
            sink.ready.eq(discard | ~self.enable | (draw & sink.valid)),
            self.pixel.eq(0),
            If(draw & sink.valid, self.pixel.eq(sink.data)),
            self.underflow.eq(draw & ~sink.valid),
            self.frame_done.eq(sink.valid & sink.ready & sink.last),
        ]
        self.sync += [
            If(h==524, h.eq(0), If(v==285,v.eq(0)).Else(v.eq(v+1))).Else(h.eq(h+1)),
            If(self.frame_start,running.eq(0)),
            If(first & self.enable & ~discard,running.eq(1)),
            If(self.underflow | (~self.enable & running),discard.eq(1),running.eq(0)),
            If(self.frame_done,discard.eq(0),running.eq(0)),
        ]


class RGBLCD(LiteXModule):
    def __init__(self, port, pads):
        if port.data_width != 16: raise ValueError('LCD requires a 16-bit buffered memory port')
        shift=4
        words=FRAME_BYTES//16
        self._enable = CSRStorage(name='enable')
        self._select = CSRStorage(name='select')
        self._base0 = CSRStorage(32,name='base0')
        self._base1 = CSRStorage(32,name='base1')
        self._address_error = CSRStatus(name='address_error')
        packed_status(self, 'rgb_lcd')
        self._frames = CSRStatus(32,name='frames')
        self._completed = CSRStatus(32,name='completed')
        self._underflows = CSRStatus(32,name='underflows')
        # Exclusive ring end; zero preserves the original contiguous scanout.
        self._wrap_start = CSRStorage(32,name='wrap_start')
        self._wrap_end = CSRStorage(32,name='wrap_end')
        # Append-only register additions preserve legacy RGB565 offsets.
        self._format = CSRStorage(1,name='format') # 0 RGB565, 1 INDEX8; change while stopped
        self._commit = CSRStorage(2,name='commit') # atomically select framebuffer + palette
        self._palette_address = CSRStorage(9,name='palette_address')
        self._palette_data = CSRStorage(16,name='palette_data')
        self._palette_readback = CSRStatus(16,name='palette_readback')
        self._palette_active = CSRStatus(1,name='palette_active')
        self._window_start = CSRStorage(20,name='window_start') # scan h0[0:10],v0[10:19],enable
        self._window_end = CSRStorage(19,name='window_end') # exclusive scan h1[0:10],v1[10:19]
        palette = Memory(16,512,name='lcd_palette')
        # LiteX forces READ_FIRST on memories with two clocks, while GW2A DPB
        # rejects that write mode. Suppress the sys read during a write so the
        # emitted RAM infers no-change; readback resumes on the following cycle.
        palette_write = palette.get_port(write_capable=True,has_re=True,clock_domain='sys')
        palette_read = palette.get_port(clock_domain='video')
        self.specials += palette,palette_write,palette_read
        self.comb += [palette_write.adr.eq(self._palette_address.storage),
            palette_write.dat_w.eq(self._palette_data.storage),
            palette_write.we.eq(self._palette_data.re),
            palette_write.re.eq(~self._palette_data.re),
            self._palette_readback.status.eq(palette_write.dat_r)]
        self.fifo = fifo = stream.SyncFIFO([('data',16)],depth=8192//2,buffered=True)
        self.cdc = cdc = stream.ClockDomainCrossing([('data',16)],cd_from='sys',cd_to='video')
        self.scan = scan = ClockDomainsRenamer('video')(LCDScan())
        self.pixels = pixels = ClockDomainsRenamer('video')(PalettePixels(palette_read))
        format_video,bank_video = Signal(),Signal()
        selected_bank = Signal()
        window_start,window_end = Signal(20),Signal(19)
        self.specials += MultiReg(self._format.storage,format_video,'video'),MultiReg(selected_bank,bank_video,'video')
        self.specials += MultiReg(self._window_start.storage,window_start,'video'),MultiReg(self._window_end.storage,window_end,'video')
        self.comb += [pixels.indexed.eq(format_video),pixels.palette_bank.eq(bank_video),
            self._palette_active.status.eq(selected_bank)]
        final_burst=Signal()
        self.comb += [port.rdata.connect(fifo.sink,omit={'last'}),
                      fifo.sink.last.eq(port.rdata.last & final_burst),
                      fifo.source.connect(cdc.sink),cdc.source.connect(pixels.sink),pixels.source.connect(scan.sink)]
        enable_source=Signal(name_override='lcd_video_enable')
        enable_source.attr.add('keep')
        self.comb += enable_source.eq(self._enable.storage)
        enable_video=Signal()
        armed=Signal()
        self.specials += MultiReg(enable_source,enable_video,'video')
        self.sync.video += If(scan.frame_start,armed.eq(enable_video))
        self.comb += scan.enable.eq(enable_video & armed)
        self.start = start = PulseSynchronizer('video','sys')
        self.done = done = PulseSynchronizer('video','sys')
        self.error = error = PulseSynchronizer('video','sys')
        self.comb += [start.i.eq(scan.frame_start),done.i.eq(scan.frame_done),error.i.eq(scan.underflow)]
        busy = Signal()
        issuing = Signal()
        pending = self.pending = Signal()
        selected = Signal()
        requested_slot = Signal()
        frame_words = Signal(max=words+1)
        frame_base = self.frame_base = Signal(32)
        requested_base = Signal(32)
        wrap_enabled = Signal()
        wrap_valid = Signal()
        ring_start = Signal(32)
        ring_end = Signal(32)
        cursor = Signal(32)
        valid_base = self.valid_base = Signal()
        self.comb += [requested_slot.eq(Mux(self._format.storage,self._commit.storage[0],self._select.storage)),
            requested_base.eq(Mux(requested_slot,self._base1.storage,self._base0.storage)),
            wrap_enabled.eq(self._wrap_end.storage != 0),
            wrap_valid.eq((self._wrap_start.storage[:shift] == 0)
                & (self._wrap_end.storage[:shift] == 0)
                & (self._wrap_start.storage >= RAM_BASE+4096)
                & (self._wrap_end.storage <= RAM_BASE+RAM_SIZE)
                & (self._wrap_end.storage > self._wrap_start.storage)
                & (requested_base >= self._wrap_start.storage)
                & (requested_base < self._wrap_end.storage)),
            valid_base.eq((requested_base[:shift] == 0) & (requested_base >= RAM_BASE+4096)
                & Mux(wrap_enabled, wrap_valid,
                      Mux(self._format.storage,
                          requested_base <= RAM_BASE+RAM_SIZE-FRAME_BYTES//2,
                          requested_base <= RAM_BASE+RAM_SIZE-FRAME_BYTES)))]
        offset = Signal(max=words)
        self.comb += [self._active.status.eq(selected),self._busy.status.eq(busy),
            port.cmd.valid.eq(issuing),port.cmd.we.eq(0),port.cmd.count.eq(8),
            port.cmd.addr.eq((cursor-RAM_BASE) >> shift)]
        self.sync += [
            If(start.o,self._frames.status.eq(self._frames.status+1),pending.eq(self._enable.storage)),
            If(error.o,self._underflows.status.eq(self._underflows.status+1)),
            If(done.o,busy.eq(0),self._completed.status.eq(self._completed.status+1)),
            If(pending & ~busy,
                pending.eq(0),
                If(self._enable.storage,
                    self._address_error.status.eq(~valid_base),
                    If(valid_base,busy.eq(1),issuing.eq(1),offset.eq(0),
                        frame_words.eq(Mux(self._format.storage,words//2,words)),
                        selected.eq(requested_slot),selected_bank.eq(self._commit.storage[1]),frame_base.eq(requested_base),
                        cursor.eq(requested_base),ring_start.eq(self._wrap_start.storage),
                        ring_end.eq(self._wrap_end.storage)))),
            If(port.cmd.valid & port.cmd.ready,
                final_burst.eq(offset==frame_words-1),
                If(offset==frame_words-1,issuing.eq(0)).Else(offset.eq(offset+1)),
                If((ring_end != 0) & (cursor == ring_end-16),
                    cursor.eq(ring_start)).Else(cursor.eq(cursor+16))),
        ]
        self.pixel = pixel = Signal(16)
        inside = Signal()
        self.comb += [inside.eq((scan.h>=window_start[:10]) & (scan.h<window_end[:10])
            & (scan.v>=window_start[10:19]) & (scan.v<window_end[10:19])),
            pixel.eq(Mux(format_video & window_start[19] & ~inside,0,scan.pixel))]
        if pads is None: return
        # Register all LCD data/controls at pixel rising edge; forwarded clock
        # rises half a cycle later to provide data setup time at the connector.
        self.specials += DDROutput(i1=0,i2=1,o=pads.clk,clk=ClockSignal('video'))
        for value,pin in [(scan.hsync,pads.hsync),(scan.vsync,pads.vsync),(scan.de,pads.de)]:
            self.specials += SDROutput(i=value,o=pin,clk=ClockSignal('video'))
        for data,pins in [(pixel[11:16],pads.r),(pixel[5:11],pads.g),(pixel[:5],pads.b)]:
            for i,pin in enumerate(pins): self.specials += SDROutput(i=data[i],o=pin,clk=ClockSignal('video'))
