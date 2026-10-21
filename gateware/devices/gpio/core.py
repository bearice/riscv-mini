"""Debounced press/release events. No bus, pin, or address knowledge.

`raw_async` is already the logical level: 1 means pressed. The block
synchronizes it, requires `debounce_cycles` consecutive samples, then
records one press or release. Holding a level does not repeat. A clear and
a new edge in the same cycle keep the new edge. Reading the sticky bits
does not clear them.
"""
from migen import Signal, If
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule


class ButtonEvents(LiteXModule):
    def __init__(self, raw_async, debounce_cycles):
        if debounce_cycles < 2:
            raise ValueError('debounce needs at least two samples')
        n = len(raw_async)
        self.clear = Signal(2 * n)
        self.stable = Signal(n)
        self.pressed = Signal(n)
        self.released = Signal(n)
        level = Signal(n)
        synced = Signal(n)
        self.comb += level.eq(raw_async)
        self.specials += MultiReg(level, synced)
        changes = Signal(n)
        counts = []
        for i in range(n):
            count = Signal(max=debounce_cycles)
            counts.append(count)
            self.comb += changes[i].eq((synced[i] != self.stable[i]) & (count == debounce_cycles - 1))
            self.sync += If(synced[i] == self.stable[i], count.eq(0)).Elif(
                count == debounce_cycles - 1, self.stable[i].eq(synced[i]), count.eq(0)
            ).Else(count.eq(count + 1))
        self.sync += [
            self.pressed.eq((self.pressed & ~self.clear[:n]) | (changes & synced)),
            self.released.eq((self.released & ~self.clear[n:]) | (changes & ~synced)),
        ]
