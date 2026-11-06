"""GFX2D APB register skeleton (Step 1).

Only the control plane exists: ID/ABI/CAP reads and descriptor register
latching. START reports ERR_UNSUPPORTED and never touches DDR; the DDR
data plane (arbiter port + engine FSM) arrives in Step 2. See
docs/platforms/mega138k_ae350/gfx2d.md for the register contract.
"""
from migen import Case, If, Mux, Signal
from litex.gen import LiteXModule
from gateware.machines.mega138k_ae350 import address_map as amap

ERR_NONE = 0
ERR_UNSUPPORTED = 1
ERR_BUSY = 2


class Gfx2D(LiteXModule):
    """APB register block for the 2D pixel engine (sys domain only)."""

    def __init__(self):
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.commit = Signal()
        self.selected = Signal()
        self.rdata = Signal(32)
        self.ok = Signal()
        self.irq = Signal()

        reg = amap.GFX2D_REGS
        # Descriptor registers (latched, writable only while idle).
        desc = {}
        for name in ('OP', 'SRC_ADDR', 'DST_ADDR', 'SRC_STRIDE', 'DST_STRIDE',
                     'WIDTH', 'HEIGHT', 'FG_COLOR', 'BG_COLOR', 'BLIT_SRC_WB'):
            desc[name] = Signal(32)
            setattr(self, name.lower(), desc[name])
        self.busy = Signal()
        self.done = Signal()
        self.error = Signal()
        self.last_error = Signal(32)
        self.commands = Signal(32)
        self.errors = Signal(32)
        self.irq_pending = Signal()
        self.irq_enable = Signal()

        reads = {
            0: amap.GFX2D_ID,
            4: amap.ABI_VERSION,
            8: amap.GFX2D_CAP,
            reg['STATUS']: self.busy | (self.done << 1) | (self.error << 2),
            reg['IRQ_STATUS']: self.irq_pending,
            reg['IRQ_ENABLE']: self.irq_enable,
            reg['LAST_ERROR']: self.last_error,
            reg['COMMANDS']: self.commands,
            reg['ERRORS']: self.errors,
            reg['CLEAR_ERRORS']: 0,
        }
        for name, sig in desc.items():
            reads[reg[name]] = sig

        read_ok = Signal()
        self.comb += [
            read_ok.eq(0),
            self.rdata.eq(0),
            Case(self.off, {off: [read_ok.eq(1), self.rdata.eq(value)]
                            for off, value in reads.items()}),
        ]

        write_offsets = [reg['CONTROL'], reg['IRQ_ENABLE'], reg['IRQ_STATUS'],
                         reg['CLEAR_ERRORS']] + [reg[n] for n in desc]
        is_write_reg = Signal()
        is_control = Signal()
        self.comb += [
            is_write_reg.eq(0),
            is_control.eq(self.off == reg['CONTROL']),
            Case(self.off, {off: is_write_reg.eq(1) for off in write_offsets}),
        ]
        write_ok = Signal()
        self.comb += [
            write_ok.eq(is_write_reg & (~is_control | (self.wdata == 1))),
            self.ok.eq(self.selected & Mux(self.write, write_ok, read_ok)),
        ]

        admitted = Signal()
        self.sync += admitted.eq(self.selected & self.write & self.ok)
        go = self.commit & admitted

        # Registered interrupt output: keeps the fabric IRQ Cat shallow.
        self.sync += self.irq.eq(self.irq_pending & self.irq_enable)

        # Skeleton behavior: START is accepted at the APB level, counted,
        # then immediately reported as UNSUPPORTED. No DDR traffic.
        self.sync += If(go, Case(self.off, {
            reg['CONTROL']: [
                If(self.busy, self.errors.eq(self.errors + 1),
                   self.last_error.eq(ERR_BUSY), self.error.eq(1))
                .Else(self.commands.eq(self.commands + 1),
                      self.done.eq(1), self.error.eq(1),
                      self.last_error.eq(ERR_UNSUPPORTED),
                      self.errors.eq(self.errors + 1),
                      self.irq_pending.eq(1))],
            reg['IRQ_ENABLE']: self.irq_enable.eq(self.wdata[0]),
            reg['IRQ_STATUS']: If(self.wdata[0], self.irq_pending.eq(0)),
            reg['CLEAR_ERRORS']: [
                If(self.wdata[0], self.done.eq(0), self.error.eq(0),
                   self.last_error.eq(ERR_NONE))],
            **{reg[name]: [If(self.busy, self.errors.eq(self.errors + 1),
                             self.last_error.eq(ERR_BUSY), self.error.eq(1))
                           .Else(sig.eq(self.wdata))]
               for name, sig in desc.items()},
        }))
