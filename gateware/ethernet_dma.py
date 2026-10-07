"""Packet DMA with native DDR ports and Wishbone packet SRAM."""
from gateware.memory_map import RAM_BASE, RAM_END, BOOT_RAM_BASE, BOOT_RAM_SIZE, ETHMAC_BASE
from migen import Signal, If, FSM, NextState, NextValue, Mux
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStorage, CSRStatus


class EthernetDMA(LiteXModule):
    """DDR uses SD's buffered MemoryPort contract; software owns packet slots."""
    def __init__(self, bus, reader, writer):
        self._control = CSRStorage(2, name='control')
        self._memory = CSRStorage(32, name='memory')
        self._slot = CSRStorage(32, name='slot')
        self._length = CSRStorage(12, name='length')
        self._busy = CSRStatus(name='busy')
        self._done = CSRStatus(name='done')
        self._error = CSRStatus(name='error')
        address = Signal(reader.address_width)
        slot = Signal(bus.adr_width)
        remaining = Signal(12)
        word, receive = Signal(32), Signal()
        count, lane = Signal(3), Signal(2)
        enable = self._control.storage[0]
        rx = self._control.storage[1]
        length = self._length.storage
        base = self._memory.storage
        end = Signal(33)
        self.comb += end.eq(base + length)
        valid = ((base[:4] == 0) & (base >= RAM_BASE) &
                 (end <= RAM_END) & (length > 0) & (length <= 1536) &
                 ((end <= BOOT_RAM_BASE) | (base >= (BOOT_RAM_BASE+BOOT_RAM_SIZE))) &
                 (self._slot.storage[:11] == 0) &
                 (self._slot.storage >= Mux(rx, ETHMAC_BASE, (ETHMAC_BASE+0x1000))) &
                 (self._slot.storage < Mux(rx, (ETHMAC_BASE+0x1000), (ETHMAC_BASE+0x2000))))
        self.fsm = fsm = FSM(reset_state='IDLE')
        fsm.act('IDLE', NextValue(self._done.status, 0), NextValue(self._error.status, 0),
            If(enable,
                If(valid, NextValue(address, (base-RAM_BASE) >> 4),
                    NextValue(slot, self._slot.storage >> 2), NextValue(remaining, length),
                    NextValue(receive, rx), NextState('PREPARE'))
                .Else(NextValue(self._error.status, 1), NextState('DONE'))))
        fsm.act('PREPARE',
            If(~enable, NextState('IDLE')).Else(
                NextValue(count, 4), NextValue(lane, 0),
                If(remaining < 16, NextValue(count, (remaining+3) >> 2)), NextState('COMMAND')))
        fsm.act('COMMAND',
            If(~enable, NextState('IDLE')).Elif(Mux(receive, writer.cmd.ready, reader.cmd.ready),
                If(receive, NextState('SLOT_READ')).Else(NextState('DDR_READ'))))
        fsm.act('SLOT_READ',
            If(bus.ack | bus.err,
                If(bus.err, NextValue(self._error.status, 1), NextState('DONE'))
                .Elif(~enable, NextState('IDLE'))
                .Else(NextValue(word, bus.dat_r), NextState('DDR_WRITE'))))
        fsm.act('DDR_WRITE',
            If(~enable, NextState('IDLE')).Elif(writer.wdata.ready,
                NextValue(remaining, Mux(remaining <= 4, 0, remaining-4)), NextValue(slot, slot+1),
                If(lane == count-1, NextState('COMMIT'))
                .Else(NextValue(lane, lane+1), NextState('SLOT_READ'))))
        fsm.act('COMMIT',
            If(~enable, NextState('IDLE')).Elif(writer.done.valid,
                If(remaining == 0, NextState('DONE')).Else(
                    NextValue(address, address+1), NextState('PREPARE'))))
        fsm.act('DDR_READ',
            If(~enable, NextState('IDLE')).Elif(reader.rdata.valid,
                NextValue(word, reader.rdata.data), NextState('SLOT_WRITE')))
        fsm.act('SLOT_WRITE',
            If(bus.ack | bus.err,
                If(bus.err, NextValue(self._error.status, 1), NextState('DONE'))
                .Elif(~enable, NextState('IDLE'))
                .Else(NextValue(remaining, Mux(remaining <= 4, 0, remaining-4)), NextValue(slot, slot+1),
                    If(remaining <= 4, NextState('DONE')).Elif(lane == count-1,
                        NextValue(address, address+1), NextState('PREPARE'))
                    .Else(NextValue(lane, lane+1), NextState('DDR_READ')))))
        fsm.act('DONE', NextValue(self._done.status, 1), If(~enable, NextState('IDLE')))
        fsm.finalize()
        state = lambda name: fsm.state == fsm.encoding[name]
        tail_mask = Mux(remaining == 1, 1, Mux(remaining == 2, 3, Mux(remaining == 3, 7, 15)))
        self.comb += [
            self._busy.status.eq(~state('IDLE') & ~state('DONE')),
            bus.cyc.eq(state('SLOT_READ') | state('SLOT_WRITE')), bus.stb.eq(bus.cyc),
            bus.we.eq(state('SLOT_WRITE')), bus.dat_w.eq(word), bus.adr.eq(slot), bus.sel.eq(15),
            reader.cmd.valid.eq(state('COMMAND') & ~receive & enable), reader.cmd.we.eq(0),
            writer.cmd.valid.eq(state('COMMAND') & receive & enable), writer.cmd.we.eq(1),
            reader.cmd.addr.eq(address), writer.cmd.addr.eq(address),
            reader.cmd.count.eq(count), writer.cmd.count.eq(count),
            reader.cancel.eq(~enable | self._error.status), writer.cancel.eq(~enable | self._error.status),
            reader.rdata.ready.eq(state('DDR_READ') & enable),
            writer.wdata.valid.eq(state('DDR_WRITE') & enable), writer.wdata.data.eq(word),
            writer.wdata.we.eq(tail_mask), writer.done.ready.eq(state('COMMIT') & enable)]
