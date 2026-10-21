"""WS2812B bit timing. No CSR, address, or package pin.

The CPU-facing color is 0xRRGGBB. The line is GRB, MSB first. Timing is
derived from the input clock: 0.40 us low-bit high, 0.80 us high-bit high,
1.25 us slot, and a 300 us reset. A send while busy is ignored.
"""
from migen import Signal, If, Cat
from litex.gen import LiteXModule


def timing(clock_hz):
    period = round(clock_hz * 1.25e-6)
    zero_high = round(clock_hz * 0.40e-6)
    one_high = round(clock_hz * 0.80e-6)
    latch = round(clock_hz * 300e-6)
    if not (0 < zero_high < one_high < period and latch > period):
        raise ValueError('WS2812 timing does not fit this clock')
    return period, zero_high, one_high, latch


class WS2812Core(LiteXModule):
    def __init__(self, pad, clock_hz=60000000):
        period, zero_high, one_high, latch = timing(clock_hz)
        self.color = Signal(24)
        self.send = Signal()
        self.busy = Signal(reset=1)
        self.completed = Signal(32)
        count = Signal(max=latch)
        shift = Signal(24)
        left = Signal(5)
        sending = Signal()
        initial = Signal(reset=1)
        self.comb += pad.eq(self.busy & sending & (
            (shift[23] & (count < one_high)) | (~shift[23] & (count < zero_high))))
        self.sync += If(self.busy,
            If(sending,
                If(count == period - 1, count.eq(0),
                    If(left == 0, sending.eq(0)).Else(left.eq(left - 1), shift.eq(shift << 1)))
                .Else(count.eq(count + 1)))
            .Else(If(count == latch - 1, self.busy.eq(0), count.eq(0), initial.eq(0),
                     If(~initial, self.completed.eq(self.completed + 1)))
                  .Else(count.eq(count + 1))))
        self.sync += If(self.send & ~self.busy, self.busy.eq(1), sending.eq(1), count.eq(0), left.eq(23),
                        shift.eq(Cat(self.color[:8], self.color[16:24], self.color[8:16])))
