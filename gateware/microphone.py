"""Standard I2S master receiver: continuous clocks, bounded snapshot FIFO."""
from gateware.csr_layout import packed_status
from migen import Signal, If, Cat, Replicate, ResetInserter
from migen.genlib.cdc import MultiReg
from migen.genlib.fifo import SyncFIFOBuffered
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus


class Microphone(LiteXModule):
    def __init__(self, pads, half_period=10, snapshot_samples=512, second=None, clock_enable=1):
        if half_period < 1 or snapshot_samples < 4:
            raise ValueError('I2S clock/snapshot too small')
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
        clear = self._clear.wr_stb
        arm = self._capture.wr_stb & run
        self.fifo = fifo = ResetInserter()(SyncFIFOBuffered(48 if second is not None else 24, snapshot_samples))
        count = Signal(max=max(2,half_period))
        bit = Signal(6, reset=63)
        bck = Signal(); ws = Signal(reset=1)
        serial = Signal(name_override='mic_serial_sync')
        self.specials += MultiReg(pads.data, serial)
        serial_right = Signal(name_override='mic_right_serial_sync')
        if second is not None:
            self.specials += MultiReg(second.data, serial_right)
            self.comb += [second.bck.eq(bck), second.ws.eq(ws & run), second.lr.eq(stereo | right)]
        shift = Signal(24)
        shift_right = Signal(24); left_word = Signal(24); have_left = Signal()
        word_right = Signal(24)
        rising = Signal(); valid = Signal(); word = Signal(24)
        self.comb += [
            pads.bck.eq(bck), pads.ws.eq(ws & run), pads.lr.eq(right & ~stereo),
            rising.eq(run & clock_enable & (count == half_period-1) & ~bck),
            # WS changes at bit0; bit0 is the I2S one-bit delay. Capture 1..24.
            valid.eq(rising & (bit[5] == (stereo | right)) & (bit[:5] == 24) & (~stereo | have_left)),
            word.eq(Cat(serial, shift[:23])),
            word_right.eq(Cat(serial_right, shift_right[:23])),
            self._sample.status.eq(Cat(fifo.dout[:24], Replicate(fifo.dout[23], 8))),
            self._sample_right.status.eq(Cat(fifo.dout[24:], Replicate(fifo.dout[47], 8)) if second is not None else 0),
            self._level.status.eq(fifo.level),
            fifo.reset.eq(clear | arm | ~run),
            fifo.we.eq(valid & self._busy.status & ~clear & ~arm),
            If(stereo, fifo.din.eq(Cat(left_word, word_right))).Else(fifo.din.eq(word)),
            fifo.re.eq(self._pop.wr_stb & fifo.readable),
        ]
        serializer = If(run,
            If(count == half_period-1,
                count.eq(0), bck.eq(~bck),
                If(bck, bit.eq(bit+1), If(bit == 31, ws.eq(1)).Elif(bit == 63, ws.eq(0)))
                .Else(
                    If((bit[5] == (right & ~stereo)) & (bit[:5] >= 1) & (bit[:5] <= 24), shift.eq(word)),
                    If(bit[5] & (bit[:5] >= 1) & (bit[:5] <= 24), shift_right.eq(word_right)),
                    If(stereo & ~bit[5] & (bit[:5] == 24), left_word.eq(word), have_left.eq(1)))
            ).Else(count.eq(count+1))
        ).Else(count.eq(0), bck.eq(0), ws.eq(1), bit.eq(63), shift.eq(0))
        self.sync += If(clock_enable | ~run, serializer)
        self.sync += If(valid,
            self._samples.status.eq(self._samples.status+1),
            If(stereo,
                self._last_sample.status.eq(Cat(left_word, Replicate(left_word[23], 8))),
                self._last_right.status.eq(Cat(word_right, Replicate(word_right[23], 8))), have_left.eq(0)
            ).Else(self._last_sample.status.eq(Cat(word, Replicate(word[23], 8)))),
            If(self._busy.status & ~arm & ~clear,
                If(fifo.writable,
                    self._captured.status.eq(self._captured.status+1),
                    If(self._captured.status == snapshot_samples-1,
                        self._busy.status.eq(0), self._done.status.eq(1)))
                .Else(self._overruns.status.eq(self._overruns.status+1),
                      self._busy.status.eq(0), self._done.status.eq(1))))
        self.sync += If(run & serial, self._activity.status.eq(1))
        self.sync += If(run & serial_right, self._activity_right.status.eq(1))
        self.sync += If(clear | arm | ~run, have_left.eq(0))
        self.sync += If(arm, self._busy.status.eq(1), self._done.status.eq(0), self._captured.status.eq(0))
        self.sync += If(clear | ~run,
            self._busy.status.eq(0), self._done.status.eq(0), self._captured.status.eq(0),
            self._overruns.status.eq(0), self._samples.status.eq(0), self._last_sample.status.eq(0),
            self._activity.status.eq(0), self._activity_right.status.eq(0), self._last_right.status.eq(0), shift_right.eq(0))
