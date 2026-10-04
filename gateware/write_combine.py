"""Bounded native write combining with optional posted classic stores.

The final beat waits for native submission. A read, a different line, or
abandoning the burst drains the partial word. Posted mode also drains on the
external barrier or idle timeout; classic writes remain non-posted by default.
"""
from migen import Signal, If, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litedram.common import LiteDRAMNativePort


class NativeWriteCombiner(LiteXModule):
    def __init__(self, backend, defer, abandoned, posted=False, idle_cycles=32, burst_active=1):
        self.port = port = LiteDRAMNativePort('both', backend.address_width, 128)
        address = Signal(backend.address_width)
        data = Signal(128)
        mask = Signal(16)
        pending = Signal()
        delayed = Signal()
        terminal = Signal()
        self.drain = Signal()
        self.idle = Signal()
        self.pending = pending
        burst_pending = Signal()
        age = Signal(max=idle_cycles+1)
        self.sync += If(~pending | port.wdata.valid, age.eq(0)).Elif(
            age < idle_cycles, age.eq(age+1))
        self.fsm = fsm = FSM(reset_state='COMMAND')
        self.comb += [self.idle.eq(~pending & fsm.ongoing('COMMAND')),
                      backend.flush.eq(port.flush & self.idle),
                      backend.wdata.data.eq(data),backend.wdata.we.eq(mask)]
        merge = [NextValue(data[8*i:8*(i+1)], port.wdata.data[8*i:8*(i+1)])
                 for i in range(16)]
        merge = [If(port.wdata.we[i], merge[i]) for i in range(16)]
        fsm.act('COMMAND',
            If(pending & (self.drain | (age == idle_cycles if posted else 0) |
                    (abandoned & (burst_pending if posted else 1)) | (port.cmd.valid &
                    (~port.cmd.we | (port.cmd.addr != address)))),
                NextValue(terminal, 0), NextState('FLUSH_COMMAND')
            ).Elif(port.cmd.valid,
                If(port.cmd.we,
                    port.cmd.ready.eq(1), NextValue(address, port.cmd.addr),
                    NextValue(delayed, defer & ~self.drain),
                    NextValue(burst_pending, burst_active), NextState('DATA')
                ).Else(
                    backend.cmd.valid.eq(1), backend.cmd.addr.eq(port.cmd.addr),
                    backend.cmd.last.eq(port.cmd.last),
                    port.cmd.ready.eq(backend.cmd.ready),
                    If(backend.cmd.ready, NextState('READ')))))
        fsm.act('DATA',
            If(port.wdata.valid,
                *merge, NextValue(mask, mask | port.wdata.we), NextValue(pending, 1),
                If(delayed,
                    port.wdata.ready.eq(1), NextState('COMMAND')
                ).Else(NextValue(terminal, 1), NextState('FLUSH_COMMAND'))))
        fsm.act('FLUSH_COMMAND',
            backend.cmd.valid.eq(1), backend.cmd.we.eq(1),
            backend.cmd.addr.eq(address),
            If(backend.cmd.ready, NextState('FLUSH_DATA')))
        fsm.act('FLUSH_DATA',
            backend.wdata.valid.eq(1),
            If(backend.wdata.ready,
                NextValue(pending, 0), NextValue(mask, 0),
                If(terminal, NextState('ACK_DATA')).Else(NextState('COMMAND'))))
        fsm.act('ACK_DATA',
            port.wdata.ready.eq(1),
            If(port.wdata.valid, NextState('COMMAND')))
        fsm.act('READ',
            port.rdata.valid.eq(backend.rdata.valid), port.rdata.data.eq(backend.rdata.data),
            backend.rdata.ready.eq(port.rdata.ready),
            If(backend.rdata.valid & port.rdata.ready, NextState('COMMAND')))
