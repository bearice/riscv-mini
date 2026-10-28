"""DVI-compatible TMDS encoding in Migen, three pixel-register latency.

Derived from SVO, Copyright (C) 2014 Clifford Wolf <clifford@clifford.at>.
Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.
THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""
from functools import reduce
from operator import xor
from migen import Cat, Case, Constant, If, Mux, Signal
from litex.gen import LiteXModule


def popcount8(module, bits):
    pairs = [Signal(2) for _ in range(4)]
    halves = [Signal(3) for _ in range(2)]
    count = Signal(4)
    module.comb += [pairs[i].eq(bits[2*i] + bits[2*i+1]) for i in range(4)]
    module.comb += [halves[i].eq(pairs[2*i] + pairs[2*i+1]) for i in range(2)]
    module.comb += count.eq(halves[0] + halves[1])
    return count


class TMDSEncoder(LiteXModule):
    def __init__(self):
        self.resetn = Signal(reset=1)
        self.din = Signal(8)
        self.de = Signal()
        self.ctrl = Signal(2)
        self.dout = Signal(10)
        ones = popcount8(self, self.din)
        xnor_mode = Signal()
        transition = Signal(9)
        self.comb += xnor_mode.eq((ones > 4) | ((ones == 4) & ~self.din[0]))
        self.comb += [transition[i].eq(reduce(xor, self.din[:i+1]) ^
                                     (xnor_mode if i % 2 else 0)) for i in range(8)]
        self.comb += transition[8].eq(~xnor_mode)
        q_m, n1, active, control = Signal(9), Signal(4), Signal(), Signal(2)
        transition_ones = popcount8(self, transition[:8])
        # Explicit signed differences avoid unsigned subtraction changing
        # the running-disparity comparisons or the width of corrections.
        balance = Signal((6, True))
        disparity = Signal((8, True))
        symbol = Signal(10)
        self.comb += balance.eq((n1 << 1) - 8)
        self.sync += If(~self.resetn,
            q_m.eq(0), n1.eq(0), active.eq(0), control.eq(0)
        ).Else(q_m.eq(transition), n1.eq(transition_ones), active.eq(self.de), control.eq(self.ctrl))
        self.sync += If(~self.resetn,
            disparity.eq(0), symbol.eq(0)
        ).Elif(~active,
            disparity.eq(0),
            Case(control, {i:symbol.eq(word) for i,word in enumerate(
                (0b1101010100, 0b0010101011, 0b0101010100, 0b1010101011))})
        ).Elif((disparity == 0) | (balance == 0),
            symbol.eq(Cat(Mux(q_m[8], q_m[:8], ~q_m[:8]), q_m[8], ~q_m[8])),
            disparity.eq(Mux(q_m[8], disparity + balance, disparity - balance))
        ).Elif(((disparity > 0) & (balance > 0)) | ((disparity < 0) & (balance < 0)),
            symbol.eq(Cat(~q_m[:8], q_m[8], Constant(1, 1))),
            disparity.eq(disparity - balance + Mux(q_m[8], 2, 0))
        ).Else(
            symbol.eq(Cat(q_m[:8], q_m[8], Constant(0, 1))),
            disparity.eq(disparity + balance - Mux(q_m[8], 0, 2)))
        # Matches the original output stage, including its reset behavior.
        self.sync += self.dout.eq(symbol)
