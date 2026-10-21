"""480x272 RGB565/INDEX8 scanout cores: backlight, palette lookup, scanner.

Device cores: no CSR bank, no machine memory map, no board pads. A machine
adapter owns the frame DMA, the address window and the pin output layer.
"""
from migen import Signal, If, Cat, Mux
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule
from litex.soc.interconnect import stream

WIDTH, HEIGHT = 480, 272
FRAME_BYTES = WIDTH*HEIGHT*2


class BacklightPWM(LiteXModule):
    """45 kHz backlight PWM at the LCD clock, integer percent duty."""
    def __init__(self, clock_freq=9_000_000):
        ticks,remainder=divmod(int(clock_freq),45_000*100)
        if remainder or ticks<2:
            raise ValueError('Backlight PWM requires a clock divisible by 4.5 MHz')
        self.duty = Signal(7,reset=100)
        self.output = Signal(reset=1)
        self.phase = phase = Signal(max=100)
        divider = Signal(max=ticks)
        active_duty = Signal(7,reset=100)
        self.sync += [
            # Register the pad drive to avoid glitches from counter carry chains.
            self.output.eq(phase<active_duty),
            If(divider==ticks-1,divider.eq(0),
                # phase is always <100, so larger duty values saturate naturally.
                If(phase==99,phase.eq(0),active_duty.eq(self.duty))
                .Else(phase.eq(phase+1)))
            .Else(divider.eq(divider+1)),
        ]


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
        self.skip = Signal()
        self.inside = Signal()
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
            draw.eq(active & self.enable & ~discard & (running|first) & (~self.skip | self.inside)),
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
