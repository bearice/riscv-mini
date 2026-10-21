"""PT8211 20K adapter: DDR ring reader, address/window checks and CSR bank.

The serializer, FIFO and audio counters live in gateware/devices/audio/core.py.
What stays here is machine-specific: the single-word Wishbone DMA that walks the
payload ring, the RAM window validation that depends on this machine's memory
map, and the CSR names the BIOS and C accessors use.
"""
from gateware.devices.audio.core import AudioCore
from gateware.machines.primer20k_vexriscv.memory_map import PAYLOAD_LIMIT, RAM_BASE
from gateware.machines.primer20k_vexriscv.csr_layout import packed_status
from migen import Signal, If, Cat, Constant
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus


class Audio(LiteXModule):
    def __init__(self, pads, bus, half_period=20, fifo_depth=512, clock_enable=1):
        self._control = CSRStorage(3, name='control')  # run, DMA, mute
        self._clear = CSR(name='clear')  # stop/drain before reconfiguration
        self._sample = CSR(32, name='sample')  # PIO packed L low16 / R high16
        packed_status(self, 'audio')
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
        self.core = core = AudioCore(pads, half_period=half_period, fifo_depth=fifo_depth,
                                     clock_enable=clock_enable)
        clear = self._clear.wr_stb
        run, dma, mute = (self._control.storage[i] for i in range(3))
        busy = Signal(); aborted = Signal(); address = Signal(bus.adr_width)
        index = Signal(16); available = Signal(32); valid_config = Signal()
        dma_result = Signal()
        self.comb += [
            core.run.eq(run), core.mute.eq(mute), core.clear.eq(clear),
            core.pio_stb.eq(self._sample.wr_stb & ~dma & ~clear),
            core.pio_data.eq(self._sample.wr_data),
            core.dma_stb.eq(dma_result), core.dma_data.eq(bus.dat_r),
            self._amplifier.status.eq(core.pa_en), self._busy.status.eq(busy),
            self._level.status.eq(core.level),
            self._frames.status.eq(core.frames), self._played.status.eq(core.played),
            self._underruns.status.eq(core.underruns), self._overruns.status.eq(core.overruns),
            self._last_sample.status.eq(core.last_sample),
            available.eq(self._producer.storage-self._fetched.status),
            valid_config.eq((self._base.storage[:2] == 0) & (self._base.storage >= RAM_BASE)
                & (self._capacity.storage != 0)
                & ((Cat(self._base.storage, Constant(0, 1)) + (self._capacity.storage << 2)) <= PAYLOAD_LIMIT)),
            bus.cyc.eq(busy), bus.stb.eq(busy), bus.adr.eq(address), bus.we.eq(0), bus.sel.eq(15),
            dma_result.eq(busy & bus.ack & ~bus.err & ~aborted & dma & ~clear),
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
                .Elif((available != 0) & core.writable,
                    busy.eq(1), aborted.eq(0), address.eq((self._base.storage >> 2)+index))),
        ]
        self.sync += If(clear,
            index.eq(0), self._fetched.status.eq(0), self._wraps.status.eq(0), self._errors.status.eq(0))
        # Kept for tests that observed the FIFO through the old attribute.
        self.fifo = core.fifo
