"""PT8211 audio device core: clock enables, stereo FIFO and word serializer.

Device core: no CSR bank, no Wishbone bus, no machine memory map. The 20K
adapter keeps the DDR ring reader and the address/window checks, because those
depend on that machine's memory map, and feeds the core with accepted DMA words
and PIO words.

The core owns the FIFO occupancy view (``writable``/``level``), the transmit
pipeline and the frame/played/underrun/overrun counters, so any machine adapter
gets identical audio timing and identical loss accounting.
"""
from migen import Signal, If, Mux, ResetInserter
from migen.genlib.fifo import SyncFIFOBuffered
from litex.gen import LiteXModule


class AudioCore(LiteXModule):
    def __init__(self, pads, half_period=20, fifo_depth=512, clock_enable=1):
        if half_period < 1 or fifo_depth < 4:
            raise ValueError('Audio clock/FIFO too small')
        self.run = Signal()
        self.mute = Signal()
        self.clear = Signal()
        self.dma_stb = Signal()      # adapter accepted one DDR word this cycle
        self.dma_data = Signal(32)
        self.pio_stb = Signal()      # PIO sample write, already gated by the adapter
        self.pio_data = Signal(32)
        self.pa_en = Signal()
        self.level = Signal(16)
        self.writable = Signal()
        self.frames = Signal(32)
        self.played = Signal(32)
        self.underruns = Signal(32)
        self.overruns = Signal(32)
        self.last_sample = Signal(32)
        run = self.run; mute = self.mute; clear = self.clear
        self.fifo = fifo = ResetInserter()(SyncFIFOBuffered(32, fifo_depth-1))
        busy = Signal()
        count = Signal(max=max(2, half_period)); bck = Signal(); bit = Signal(5, reset=31)
        shift = Signal(32); din = Signal(); ws = Signal()
        incoming = Signal(32); take = Signal(); boundary = Signal()
        self.comb += [
            pads.bck.eq(bck), pads.ws.eq(ws), pads.din.eq(din), pads.pa_en.eq(run & ~mute & ~clear),
            self.pa_en.eq(pads.pa_en), self.level.eq(fifo.level), self.writable.eq(fifo.writable),
            fifo.reset.eq(clear),
            fifo.we.eq(self.dma_stb | self.pio_stb),
            fifo.din.eq(Mux(self.dma_stb, self.dma_data, self.pio_data)),
            boundary.eq(clock_enable & (count == half_period-1) & bck & (bit == 31)),
            take.eq(boundary & run & fifo.readable & ~clear), fifo.re.eq(take),
            incoming.eq(Mux(take & ~mute, fifo.dout, 0)),
        ]
        # DIN/WS change only on falling BCK, with a full half-period of setup.
        # A 32-bit FIFO word transmits right MSB first, then left MSB first.
        serializer = If(count == half_period-1,
            count.eq(0), bck.eq(~bck),
            If(bck,
                If(bit == 31,
                    bit.eq(0), ws.eq(0), shift.eq(incoming), din.eq(incoming[31]),
                    self.last_sample.eq(incoming),
                    If(run & ~clear,
                        self.frames.eq(self.frames+1),
                        If(take, self.played.eq(self.played+1))
                        .Else(self.underruns.eq(self.underruns+1)))
                ).Else(bit.eq(bit+1), shift.eq(shift << 1), din.eq(shift[30]), ws.eq(bit >= 15))
            )
        ).Else(count.eq(count+1))
        self.sync += If(clock_enable, serializer)
        self.sync += If(self.pio_stb & ~self.writable, self.overruns.eq(self.overruns+1))
        # Stop/clear immediately mutes the amp; discard a partial old word too.
        self.sync += If(~run | clear, shift.eq(0), din.eq(0))
        self.sync += If(clear,
            self.frames.eq(0), self.played.eq(0), self.underruns.eq(0),
            self.overruns.eq(0), self.last_sample.eq(0))
