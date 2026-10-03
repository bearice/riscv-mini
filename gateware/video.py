"""480x272 RGB565 LCD scanout. One bounded DMA frame per scan period."""
from gateware.csr_layout import packed_status
from migen import Signal, If, Cat, ClockSignal, ClockDomainsRenamer
from migen.genlib.cdc import MultiReg, PulseSynchronizer
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStorage, CSRStatus
from litex.build.io import DDROutput, SDROutput
from litedram.frontend.dma import LiteDRAMDMAReader

WIDTH, HEIGHT = 480, 272
BASES = (0x47e00000, 0x47e40000)
FRAME_BYTES = WIDTH*HEIGHT*2


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
        if port.data_width % 16: raise ValueError('LCD DMA requires complete RGB565 pixels')
        from migen.fhdl.bitcontainer import log2_int
        word_bytes=port.data_width//8
        shift=log2_int(word_bytes)
        words=FRAME_BYTES//word_bytes
        if FRAME_BYTES%word_bytes: raise ValueError('Frame must contain complete native words')
        self._enable = CSRStorage(name='enable')
        self._select = CSRStorage(name='select')
        packed_status(self, 'rgb_lcd')
        self._frames = CSRStatus(32,name='frames')
        self._completed = CSRStatus(32,name='completed')
        self._underflows = CSRStatus(32,name='underflows')
        self.dma = dma = LiteDRAMDMAReader(port,fifo_depth=8192//word_bytes,fifo_buffered=True)
        self.conv = conv = stream.Converter(port.data_width,16)
        self.cdc = cdc = stream.ClockDomainCrossing([('data',16)],cd_from='sys',cd_to='video')
        self.scan = scan = ClockDomainsRenamer('video')(LCDScan())
        self.comb += [dma.source.connect(conv.sink),conv.source.connect(cdc.sink),cdc.source.connect(scan.sink)]
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
        pending = Signal()
        selected = Signal()
        offset = Signal(max=words)
        self.comb += [self._active.status.eq(selected),self._busy.status.eq(busy),
            dma.sink.valid.eq(issuing),dma.sink.last.eq(offset==words-1),
            dma.sink.address.eq((BASES[0]-0x40000000)//word_bytes + (selected<<(18-shift)) + offset)]
        self.sync += [
            If(start.o,self._frames.status.eq(self._frames.status+1),pending.eq(self._enable.storage)),
            If(error.o,self._underflows.status.eq(self._underflows.status+1)),
            If(done.o,busy.eq(0),self._completed.status.eq(self._completed.status+1)),
            If(pending & ~busy,
                pending.eq(0),
                If(self._enable.storage,busy.eq(1),issuing.eq(1),offset.eq(0),selected.eq(self._select.storage))),
            If(dma.sink.valid & dma.sink.ready,
                If(dma.sink.last,issuing.eq(0)).Else(offset.eq(offset+1))),
        ]
        if pads is None: return
        # Register all LCD data/controls at pixel rising edge; forwarded clock
        # rises half a cycle later to provide data setup time at the connector.
        self.specials += DDROutput(i1=0,i2=1,o=pads.clk,clk=ClockSignal('video'))
        for value,pin in [(scan.hsync,pads.hsync),(scan.vsync,pads.vsync),(scan.de,pads.de)]:
            self.specials += SDROutput(i=value,o=pin,clk=ClockSignal('video'))
        pixel=scan.pixel
        for data,pins in [(pixel[11:16],pads.r),(pixel[5:11],pads.g),(pixel[:5],pads.b)]:
            for i,pin in enumerate(pins): self.specials += SDROutput(i=data[i],o=pin,clk=ClockSignal('video'))
