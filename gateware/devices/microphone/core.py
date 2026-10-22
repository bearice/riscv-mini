"""Standard I2S master receiver with a bounded snapshot FIFO.

Device core: no CSR bank, no board pins, no machine address space. The 20K
adapter owns the CSR names; any machine may instantiate this core on its own
pads.

Overflow policy (fixed by the interface, not by the adapter): a snapshot window
closes at the first sample the FIFO cannot accept. That sample is NOT written,
the overrun counter increments, ``busy`` clears and ``done`` sets, so a window
never exceeds ``snapshot_samples`` and never silently wraps. ``clear``,
re-arming or disabling also resets the FIFO and the window state.
"""
from migen import Signal, If, Cat, Replicate, ResetInserter
from migen.genlib.cdc import MultiReg
from migen.genlib.fifo import SyncFIFOBuffered
from litex.gen import LiteXModule


class MicrophoneCore(LiteXModule):
    def __init__(self, pads, half_period=10, snapshot_samples=512, second=None, clock_enable=1):
        if half_period < 1 or snapshot_samples < 4:
            raise ValueError('I2S clock/snapshot too small')
        self.enable = Signal()
        self.right = Signal()
        self.stereo = Signal()
        self.arm = Signal()
        self.stream = Signal()     # 0: bounded snapshot, 1: continuous/drop-new on full
        self.clear = Signal()
        self.pop = Signal()
        self.sample = Signal(32)
        self.sample_right = Signal(32)
        self.level = Signal(16)
        self.samples = Signal(32)
        self.last_sample = Signal(32)
        self.last_right = Signal(32)
        self.overruns = Signal(32)
        self.busy = Signal()
        self.done = Signal()
        self.captured = Signal(16)
        self.accepted = Signal(32)
        self.activity = Signal()
        self.activity_right = Signal()
        run = self.enable
        right = self.right
        stereo = self.stereo
        clear = self.clear
        arm = self.arm
        self.fifo = fifo = ResetInserter()(SyncFIFOBuffered(48 if second is not None else 24, snapshot_samples-1))
        count = Signal(max=max(2, half_period))
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
            self.sample.eq(Cat(fifo.dout[:24], Replicate(fifo.dout[23], 8))),
            self.sample_right.eq(Cat(fifo.dout[24:], Replicate(fifo.dout[47], 8)) if second is not None else 0),
            self.level.eq(fifo.level),
            fifo.reset.eq(clear | arm | ~run),
            fifo.we.eq(valid & self.busy & ~clear & ~arm),
            If(stereo, fifo.din.eq(Cat(left_word, word_right))).Else(fifo.din.eq(word)),
            fifo.re.eq(self.pop & fifo.readable),
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
            self.samples.eq(self.samples+1),
            If(stereo,
                self.last_sample.eq(Cat(left_word, Replicate(left_word[23], 8))),
                self.last_right.eq(Cat(word_right, Replicate(word_right[23], 8))), have_left.eq(0)
            ).Else(self.last_sample.eq(Cat(word, Replicate(word[23], 8)))),
            If(self.busy & ~arm & ~clear,
                If(fifo.writable,
                    self.accepted.eq(self.accepted+1),
                    If(~self.stream, self.captured.eq(self.captured+1)),
                    If(~self.stream & (self.captured == snapshot_samples-1),
                        self.busy.eq(0), self.done.eq(1)))
                .Else(self.overruns.eq(self.overruns+1),
                      If(~self.stream, self.busy.eq(0), self.done.eq(1)))))
        self.sync += If(run & serial, self.activity.eq(1))
        self.sync += If(run & serial_right, self.activity_right.eq(1))
        self.sync += If(clear | arm | ~run, have_left.eq(0))
        self.sync += If(arm, self.busy.eq(1), self.done.eq(0), self.captured.eq(0), self.accepted.eq(0))
        self.sync += If(clear | ~run,
            self.busy.eq(0), self.done.eq(0), self.captured.eq(0),
            self.accepted.eq(0),
            self.overruns.eq(0), self.samples.eq(0), self.last_sample.eq(0),
            self.activity.eq(0), self.activity_right.eq(0), self.last_right.eq(0), shift_right.eq(0))
