"""Activity indicator: one visible flash per burst, blinking under load.

`activity_async` is an asynchronous level; any sampled high starts a flash.
A flash holds `on` for `period_cycles`, then forces it off for the same time
so sustained traffic blinks instead of reading as a steady light. Activity
seen during the forced-off time queues exactly one more flash.
"""
from migen import Signal, If
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule


class ActivityFlash(LiteXModule):
    def __init__(self, activity_async, period_cycles):
        if period_cycles < 1:
            raise ValueError('activity flash needs a positive period')
        self.on = Signal()
        active = Signal()
        pending = Signal()
        count = Signal(max=period_cycles + 1)
        self.specials += MultiReg(activity_async, active)
        self.sync += [
            If(active & ~self.on & (count != 0), pending.eq(1)),
            If(count != 0, count.eq(count - 1)
            ).Elif(self.on, self.on.eq(0), count.eq(period_cycles)
            ).Elif(active | pending, self.on.eq(1), count.eq(period_cycles), pending.eq(0)),
        ]
