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
            If(native.cmd.valid & native.cmd.ready,
                If(native.cmd.we, NextState('WRITE')).Else(NextState('READ'))))
        fsm.act('WRITE',
            If(native.wdata.valid & native.wdata.ready, NextState('SELECT')))
        fsm.act('READ',
            If(native.rdata.valid & native.rdata.ready, NextState('SELECT')))
        fsm.finalize()
        state = lambda name: fsm.state == fsm.encoding[name]
        self.comb += [
            native.cmd.valid.eq(state('COMMAND') & Array([p.cmd.valid for p in ports])[owner]),
            native.cmd.addr.eq(Array([p.cmd.addr for p in ports])[owner]),
            native.cmd.we.eq(Array([p.cmd.we for p in ports])[owner]),
            native.cmd.last.eq(Array([p.cmd.last for p in ports])[owner]),
            native.wdata.valid.eq(state('WRITE') & Array([p.wdata.valid for p in ports])[owner]),
            native.wdata.data.eq(Array([p.wdata.data for p in ports])[owner]),
            native.wdata.we.eq(Array([p.wdata.we for p in ports])[owner]),
            native.rdata.ready.eq(state('READ') & Array([p.rdata.ready for p in ports])[owner]),
            *[p.cmd.ready.eq(state('COMMAND') & native.cmd.ready & (owner == i)) for i, p in enumerate(ports)],
            *[p.wdata.ready.eq(state('WRITE') & native.wdata.ready & (owner == i)) for i, p in enumerate(ports)],
            *[p.rdata.valid.eq(state('READ') & native.rdata.valid & (owner == i)) for i, p in enumerate(ports)],
            *[p.rdata.data.eq(native.rdata.data) for p in ports]]



class NativeSDTransfer(LiteXModule):
    """SD's 32-bit stream client; burst storage belongs to the memory controller."""
    def __init__(self, port, write, endianness='little', with_csr=True):
        from litex.soc.interconnect import stream
        if port.data_width != 32:
            raise ValueError('SD requires a 32-bit buffered memory port')
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
        count = Signal(3)
        lane = Signal(2)
        offset = Signal(11)
        valid = ((self._base.storage[:4] == 0) & (self._base.storage[27:] == 8)
            & (self._base.storage[12:27] != 0x7fff)
            & (self._length.storage[:2] == 0) & (self._length.storage > 0) & (self._length.storage <= 4096))
        def swap(word):
            return Cat(word[24:32], word[16:24], word[8:16], word[:8]) if endianness == 'little' else word
        self.comb += [port.cmd.addr.eq(address), port.cmd.we.eq(write),
            port.cmd.count.eq(count), port.cancel.eq(~self._enable.storage),
            port.wdata.data.eq(swap(self.sink.data)), port.wdata.we.eq(15),
            self.source.data.eq(swap(port.rdata.data)), self.source.last.eq(remaining == 1),
            self._offset.status.eq(offset)]
        self.fsm = fsm = FSM(reset_state='IDLE')
        self.comb += self._busy.status.eq(~fsm.ongoing('IDLE') & ~fsm.ongoing('DONE'))
        fsm.act('IDLE', NextValue(self._done.status, 0), NextValue(self._error.status, 0),
            If(self._enable.storage,
                If(valid, NextValue(address, self._base.storage[4:27]),
                    NextValue(remaining, self._length.storage >> 2), NextValue(offset, 0),
                    NextState('PREPARE')).Else(NextValue(self._error.status, 1), NextState('DONE'))))
        fsm.act('PREPARE',
            If(~self._enable.storage, NextState('IDLE')).Else(
                NextValue(count, 4), If(remaining < 4, NextValue(count, remaining)),
                NextValue(lane, 0), NextState('COMMAND')))
        fsm.act('COMMAND',
            If(~self._enable.storage, NextState('IDLE')).Elif(port.cmd.ready,
                NextState('WRITE' if write else 'READ')))
        if write:
            fsm.act('WRITE',
                If(~self._enable.storage, NextState('IDLE')).Elif(self.sink.valid & port.wdata.ready,
                    NextValue(offset, offset+1), NextValue(remaining, remaining-1),
                    If(lane == count-1, NextState('COMMIT')).Else(NextValue(lane, lane+1))))
            fsm.act('COMMIT',
                If(~self._enable.storage, NextState('IDLE')).Elif(port.done.valid,
                    If(remaining == 0, NextState('DONE')).Else(
                        NextValue(address, address+1), NextState('PREPARE'))))
        else:
            fsm.act('READ',
                If(~self._enable.storage, NextState('IDLE')).Elif(port.rdata.valid & self.source.ready,
                    NextValue(offset, offset+1), NextValue(remaining, remaining-1),
                    If(remaining == 1, NextState('DONE')).Elif(port.rdata.last,
                        NextValue(address, address+1), NextState('PREPARE'))))
        fsm.act('DONE', NextValue(self._done.status, 1), If(~self._enable.storage, NextState('IDLE')))
        fsm.finalize()
        state = lambda name: fsm.state == fsm.encoding[name]
        self.comb += port.cmd.valid.eq(state('COMMAND') & self._enable.storage)
        if write:
            self.comb += [port.wdata.valid.eq(state('WRITE') & self.sink.valid & self._enable.storage),
                self.sink.ready.eq(state('WRITE') & port.wdata.ready & self._enable.storage),
                port.done.ready.eq(state('COMMIT'))]
        else:
            self.comb += [self.source.valid.eq(state('READ') & port.rdata.valid & self._enable.storage),
                port.rdata.ready.eq(state('READ') & self.source.ready & self._enable.storage)]
