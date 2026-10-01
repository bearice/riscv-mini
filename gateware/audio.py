"""PT8211: sys clock enables, stereo FIFO and bounded single-word DDR DMA."""
from migen import Signal, If, Cat, Mux, ResetInserter, Constant
from migen.genlib.fifo import SyncFIFOBuffered
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus


class Audio(LiteXModule):
    def __init__(self, pads, bus, half_period=20, fifo_depth=512):
        if half_period < 2 or fifo_depth < 4:
            raise ValueError('Audio clock/FIFO too small')
        self._control = CSRStorage(3, name='control')  # run, DMA, mute
        self._clear = CSR(name='clear')  # stop/drain before reconfiguration
        self._sample = CSR(32, name='sample')  # PIO packed L low16 / R high16
        self._level = CSRStatus(16, name='level')
        self._frames = CSRStatus(32, name='frames')
        self._played = CSRStatus(32, name='played')
        self._underruns = CSRStatus(32, name='underruns')
        self._overruns = CSRStatus(32, name='overruns')
        self._last_sample = CSRStatus(32, name='last_sample')
        self._base = CSRStorage(32, name='base')
        self._capacity = CSRStorage(16, name='capacity')  # stereo frames
        self._producer = CSRStorage(32, name='producer')  # monotonic committed frames
        self._fetched = CSRStatus(32, name='fetched')  # monotonic DMA frames, ring ownership
        self._wraps = CSRStatus(32, name='wraps')
        self._errors = CSRStatus(3, name='errors')  # config / ownership / bus
        self._busy = CSRStatus(name='busy')
        self._amplifier = CSRStatus(name='amplifier')
        self.fifo = fifo = ResetInserter()(SyncFIFOBuffered(32, fifo_depth-1))
        clear = self._clear.wr_stb
        run, dma, mute = (self._control.storage[i] for i in range(3))
        busy = Signal(); aborted = Signal(); address = Signal(bus.adr_width)
        index = Signal(16); available = Signal(32); valid_config = Signal()
        dma_result = Signal(); pio = Signal(); take = Signal(); boundary = Signal()
        count = Signal(max=half_period); bck = Signal(); bit = Signal(5, reset=31)
        shift = Signal(32); din = Signal(); ws = Signal()
        incoming = Signal(32)
        self.comb += [
            pads.bck.eq(bck), pads.ws.eq(ws), pads.din.eq(din),
            pads.pa_en.eq(run & ~mute & ~clear), self._amplifier.status.eq(pads.pa_en),
            self._busy.status.eq(busy), self._level.status.eq(fifo.level),
            fifo.reset.eq(clear), available.eq(self._producer.storage-self._fetched.status),
            valid_config.eq((self._base.storage[:2] == 0) & (self._base.storage >= 0x40000000)
                & (self._capacity.storage != 0)
                & ((Cat(self._base.storage, Constant(0, 1)) + (self._capacity.storage << 2)) <= 0x47e00000)),
            bus.cyc.eq(busy), bus.stb.eq(busy), bus.adr.eq(address), bus.we.eq(0), bus.sel.eq(15),
            dma_result.eq(busy & bus.ack & ~bus.err & ~aborted & dma & ~clear),
            pio.eq(self._sample.wr_stb & ~dma & ~clear),
            fifo.we.eq(dma_result | pio), fifo.din.eq(Mux(dma_result, bus.dat_r, self._sample.wr_data)),
            boundary.eq((count == half_period-1) & bck & (bit == 31)),
            take.eq(boundary & run & fifo.readable & ~clear), fifo.re.eq(take),
            incoming.eq(Mux(take & ~mute, fifo.dout, 0)),
        ]
        # Never cancel an outstanding DDR request: stop marks it stale and drains
        # its reply before idle. Each request releases Wishbone after one word.
        self.sync += [
            If(busy,
                If(~dma | clear, aborted.eq(1)),
                If(bus.ack | bus.err,
                    busy.eq(0),
                    If(dma_result,
                        self._fetched.status.eq(self._fetched.status+1),
                        If(index == self._capacity.storage-1,
                            index.eq(0), self._wraps.status.eq(self._wraps.status+1)
                        ).Else(index.eq(index+1))),
                    If(bus.err & ~aborted & dma & ~clear, self._errors.status.eq(self._errors.status | 4))
                )
            ).Elif(dma & ~clear & (self._errors.status == 0),
                If(~valid_config, self._errors.status.eq(1))
                .Elif(available > self._capacity.storage, self._errors.status.eq(2))
                .Elif((available != 0) & fifo.writable,
                    busy.eq(1), aborted.eq(0), address.eq((self._base.storage >> 2)+index))),
            If(pio & ~fifo.writable, self._overruns.status.eq(self._overruns.status+1)),
        ]
        # DIN/WS change only on falling BCK, with a full half-period of setup.
        # A 32-bit FIFO word transmits right MSB first, then left MSB first.
        self.sync += If(count == half_period-1,
            count.eq(0), bck.eq(~bck),
            If(bck,
                If(bit == 31,
                    bit.eq(0), ws.eq(0), shift.eq(incoming), din.eq(incoming[31]),
                    self._last_sample.status.eq(incoming),
                    If(run & ~clear,
                        self._frames.status.eq(self._frames.status+1),
                        If(take, self._played.status.eq(self._played.status+1))
                        .Else(self._underruns.status.eq(self._underruns.status+1)))
                ).Else(bit.eq(bit+1), shift.eq(shift << 1), din.eq(shift[30]), ws.eq(bit >= 15))
            )
        ).Else(count.eq(count+1))
        # Stop/clear immediately mutes the amp; discard a partial old word too.
        self.sync += If(~run | clear, shift.eq(0), din.eq(0))
        self.sync += If(clear,
            index.eq(0), self._fetched.status.eq(0), self._wraps.status.eq(0), self._errors.status.eq(0),
            self._frames.status.eq(0), self._played.status.eq(0), self._underruns.status.eq(0),
            self._overruns.status.eq(0), self._last_sample.status.eq(0))
