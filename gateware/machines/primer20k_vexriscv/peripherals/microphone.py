"""20K microphone adapter: CSR bank and machine pin naming over the device core."""
from gateware.devices.microphone.core import MicrophoneCore
from gateware.machines.primer20k_vexriscv.csr_layout import packed_status
from migen import Signal
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus


class Microphone(LiteXModule):
    def __init__(self, pads, half_period=10, snapshot_samples=512, second=None, clock_enable=1):
        self._control = CSRStorage(3, name='control')  # enable, mono right slot, stereo pair
        self._capture = CSR(name='capture')
        self._clear = CSR(name='clear')
        self._pop = CSR(name='pop')
        self._sample = CSRStatus(32, name='sample')  # sign-extended PCM24
        packed_status(self, 'mic')
        self._samples = CSRStatus(32, name='samples')
        self._last_sample = CSRStatus(32, name='last_sample')
        self._overruns = CSRStatus(32, name='overruns')
        self._sample_right = CSRStatus(32, name='sample_right')
        self._last_right = CSRStatus(32, name='last_right')
        self.enabled = run = self._control.storage[0]
        right = self._control.storage[1]
        stereo = self._control.storage[2] if second is not None else 0
        self.core = core = MicrophoneCore(pads, half_period=half_period, snapshot_samples=snapshot_samples,
                                          second=second, clock_enable=clock_enable)
        self.comb += [
            core.enable.eq(run), core.right.eq(right), core.stereo.eq(stereo),
            core.arm.eq(self._capture.wr_stb & run), core.clear.eq(self._clear.wr_stb),
            core.pop.eq(self._pop.wr_stb),
            self._sample.status.eq(core.sample), self._sample_right.status.eq(core.sample_right),
            self._level.status.eq(core.level), self._samples.status.eq(core.samples),
            self._last_sample.status.eq(core.last_sample), self._last_right.status.eq(core.last_right),
            self._overruns.status.eq(core.overruns), self._busy.status.eq(core.busy),
            self._done.status.eq(core.done), self._captured.status.eq(core.captured),
            self._activity.status.eq(core.activity), self._activity_right.status.eq(core.activity_right),
        ]
        # Kept for adapters/tests that observed the FIFO end-stop through the old name.
        self.fifo = core.fifo
