"""Bounded native DMA clients sharing one DDR port, without additional RAM."""
from migen import Signal, Array, Cat, Constant, If, FSM, NextState, NextValue, Case
from migen.genlib.roundrobin import RoundRobin, SP_CE
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStorage, CSRStatus
from litedram.common import LiteDRAMNativePort


class NativeDMAArbiter(LiteXModule):
    """One owned command at a time; round robin between complete transfers."""
    def __init__(self, native, count):
        self.ports = ports = [LiteDRAMNativePort('both', native.address_width, 128) for _ in range(count)]
        self.rr = rr = RoundRobin(count, SP_CE)
        owner = Signal(max=max(2, count))
        self.fsm = fsm = FSM(reset_state='SELECT')
        self.comb += rr.request.eq(Cat(*[p.cmd.valid for p in ports]))
        fsm.act('SELECT', rr.ce.eq(1),
            If(Array([p.cmd.valid for p in ports])[rr.grant],
                NextValue(owner, rr.grant), NextState('COMMAND')))
        fsm.act('COMMAND',
            native.cmd.valid.eq(Array([p.cmd.valid for p in ports])[owner]),
            native.cmd.addr.eq(Array([p.cmd.addr for p in ports])[owner]),
            native.cmd.we.eq(Array([p.cmd.we for p in ports])[owner]),
            native.cmd.last.eq(Array([p.cmd.last for p in ports])[owner]),
            *[p.cmd.ready.eq(native.cmd.ready & (owner == i)) for i, p in enumerate(ports)],
            If(native.cmd.valid & native.cmd.ready,
                If(native.cmd.we, NextState('WRITE')).Else(NextState('READ'))))
        fsm.act('WRITE',
            native.wdata.valid.eq(Array([p.wdata.valid for p in ports])[owner]),
            native.wdata.data.eq(Array([p.wdata.data for p in ports])[owner]),
            native.wdata.we.eq(Array([p.wdata.we for p in ports])[owner]),
            *[p.wdata.ready.eq(native.wdata.ready & (owner == i)) for i, p in enumerate(ports)],
            If(native.wdata.valid & native.wdata.ready, NextState('SELECT')))
        fsm.act('READ', native.rdata.ready.eq(Array([p.rdata.ready for p in ports])[owner]),
            *[p.rdata.valid.eq(native.rdata.valid & (owner == i)) for i, p in enumerate(ports)],
            If(native.rdata.valid & native.rdata.ready, NextState('SELECT')))
        self.comb += [p.rdata.data.eq(native.rdata.data) for p in ports]


class NativeSDTransfer(LiteXModule):
    """16-byte aligned, 4-byte sized SD blocks; pack four words per DDR beat."""
    def __init__(self, port, write, endianness='little', with_csr=True):
        from litex.soc.interconnect import stream
        self._base = CSRStorage(32, name='base')
        self._length = CSRStorage(13, name='length')
        self._enable = CSRStorage(name='enable')
        self._done = CSRStatus(name='done')
        self._error = CSRStatus(name='error')
        self._offset = CSRStatus(11, name='offset')
        self._busy = CSRStatus(name='busy')
        self.sink = stream.Endpoint([('data', 32)])
        self.source = stream.Endpoint([('data', 32)])
        address = Signal(port.address_width)
        remaining = Signal(11)
        lane = Signal(2)
        data = Signal(128)
        mask = Signal(16)
        offset = Signal(11)
        valid = ((self._base.storage[:4] == 0) & (self._base.storage[27:] == 8)
            & (self._base.storage[12:27] != 0x7fff)
            & (self._length.storage[:2] == 0) & (self._length.storage > 0) & (self._length.storage <= 4096))
        def swap(word):
            return Cat(word[24:32], word[16:24], word[8:16], word[:8]) if endianness == 'little' else word
        self.comb += [port.cmd.addr.eq(address), port.cmd.we.eq(write),
            port.wdata.data.eq(data if write else 0), port.wdata.we.eq(mask if write else 0), self._offset.status.eq(offset)]
        self.fsm = fsm = FSM(reset_state='IDLE')
        self.comb += self._busy.status.eq(~fsm.ongoing('IDLE') & ~fsm.ongoing('DONE'))
        fsm.act('IDLE', NextValue(self._done.status, 0), NextValue(self._error.status, 0),
            If(self._enable.storage,
                If(valid, NextValue(address, self._base.storage[4:27]),
                    NextValue(remaining, self._length.storage >> 2), NextValue(offset, 0),
                    NextValue(lane, 0), NextValue(mask, 0), NextState('LOAD' if write else 'COMMAND'))
                .Else(NextValue(self._error.status, 1), NextState('DONE'))))
        if write:
            fsm.act('LOAD', self.sink.ready.eq(self._enable.storage),
                If(~self._enable.storage, NextState('IDLE')).Elif(self.sink.valid,
                    Case(lane, {i: NextValue(data[32*i:32*(i+1)], swap(self.sink.data)) for i in range(4)}),
                    # Keep four shift bits in emitted Verilog; lane << 2 has
                    # a self-determined two-bit width and aliases every lane.
                    NextValue(mask, mask | (15 << Cat(0,0,lane))), NextValue(offset, offset+1),
                    NextValue(remaining, remaining-1),
                    If((lane == 3) | (remaining == 1), NextState('COMMAND')).Else(NextValue(lane, lane+1))))
        fsm.act('COMMAND', port.cmd.valid.eq(1),
            If(port.cmd.ready, NextState('WRITE' if write else 'READ')))
        if write:
            fsm.act('WRITE', port.wdata.valid.eq(1),
                If(port.wdata.ready,
                    If(~self._enable.storage, NextState('IDLE')).Elif(remaining == 0, NextState('DONE'))
                    .Else(NextValue(address, address+1), NextValue(lane, 0), NextValue(mask, 0), NextState('LOAD'))))
        if not write:
            fsm.act('READ', port.rdata.ready.eq(1),
                If(port.rdata.valid,
                    NextValue(data, port.rdata.data), NextValue(lane, 0),
                    If(self._enable.storage, NextState('DRAIN')).Else(NextState('IDLE'))))
            fsm.act('DRAIN', self.source.valid.eq(self._enable.storage), self.source.last.eq(remaining == 1),
                Case(lane, {i: self.source.data.eq(swap(data[32*i:32*(i+1)])) for i in range(4)}),
                If(~self._enable.storage, NextState('IDLE')).Elif(self.source.ready,
                    NextValue(offset, offset+1), NextValue(remaining, remaining-1),
                    If(remaining == 1, NextState('DONE')).Elif(lane == 3,
                        NextValue(address, address+1), NextState('COMMAND')).Else(NextValue(lane, lane+1))))
        fsm.act('DONE', NextValue(self._done.status, 1), If(~self._enable.storage, NextState('IDLE')))
