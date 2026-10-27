"""U7 mode-0 QSPI engine. Captured 1..4 byte transfers, MSB first.

CS stays asserted between transfers. Control may change only while idle;
DATA starts exactly one transfer at the APB commit edge. All logic is sys.
"""
from migen import *
from litex.gen import LiteXModule


class UserQSPI(LiteXModule):
    def __init__(self, clock_hz, size_bytes):
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.selected = Signal()
        self.commit = Signal()
        self.ok = Signal()
        self.rdata = Signal(32)
        self.cs_n = Signal(reset=1)
        self.clk = Signal()
        self.dq_o = Signal(4)
        self.dq_oe = Signal(4)
        self.dq_i = Signal(4)
        control = Signal(10)
        busy = Signal()
        tx, rx = Signal(32), Signal(32)
        result = Signal(32)
        left = Signal(6)
        divider = Signal(8, reset=0)
        timer = Signal(8)
        quad, reading = Signal(), Signal()
        step = Signal(3)
        valid_ctrl = (self.wdata & ~0x307) == 0
        self.comb += [
            self.cs_n.eq(~control[0]), step.eq(Mux(quad, 4, 1)),
            # In single mode IO2/3 must remain high (WP/HOLD). IO1 is input.
            self.dq_o.eq(Mux(quad, tx[28:32], Cat(tx[31], Constant(0, 1), Constant(3, 2)))),
            self.dq_oe.eq(Mux(control[1], Mux(control[2], 0, 15), 13)),
            self.ok.eq(self.selected & Mux(self.write,
                ~busy & (((self.off == 0xc) & valid_ctrl) |
                         ((self.off == 0x14) & (self.wdata < 256)) |
                         ((self.off == 0x18) & control[0])),
                (self.off == 0) | (self.off == 4) | (self.off == 8) |
                (self.off == 0xc) | (self.off == 0x10) | (self.off == 0x14) |
                (self.off == 0x1c) | (self.off == 0x20) | (self.off == 0x24))),
            self.rdata.eq(0),
            Case(self.off, {0: self.rdata.eq(0xae350b01), 4: self.rdata.eq(1),
                8: self.rdata.eq(3), 0xc: self.rdata.eq(control),
                0x10: self.rdata.eq(busy), 0x14: self.rdata.eq(divider),
                0x1c: self.rdata.eq(result), 0x20: self.rdata.eq(clock_hz),
                0x24: self.rdata.eq(size_bytes)}),
        ]
        self.sync += [
            If(self.commit & self.ok & self.write,
                Case(self.off, {
                    0xc: control.eq(self.wdata),
                    0x14: divider.eq(self.wdata),
                    0x18: [tx.eq(self.wdata), rx.eq(0), busy.eq(1),
                        left.eq((control[8:10] + 1) << 3),
                        quad.eq(control[1]), reading.eq(control[2]), timer.eq(divider)],
                })),
            If(busy,
                If(timer != 0, timer.eq(timer - 1)).Else(
                    timer.eq(divider), self.clk.eq(~self.clk),
                    If(~self.clk,
                        rx.eq(Mux(quad, Cat(self.dq_i, rx[:28]), Cat(self.dq_i[1], rx[:31]))),
                    ).Else(
                        tx.eq(Mux(quad, tx << 4, tx << 1)), left.eq(left - step),
                        If(left == step, busy.eq(0), result.eq(rx)),
                    ),
                )),
        ]
