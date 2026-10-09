"""Autonomous descriptor rings over native DDR; MAC packet SRAM is staging.

16-byte descriptor: buffer address, capacity/TX length, ownership/status, cookie.
OWN=bit31, DONE=bit30, ERROR=bit29; completion length is status bits 0..11.
The CPU publishes TX producer and RX consumer sequences after a memory fence.
Hardware publishes completion only after payload commit / MAC SRAM reader completion.
"""
from migen import Signal, If, FSM, NextState, NextValue, Mux
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStorage, CSRStatus
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourceLevel, EventSourcePulse
from gateware.memory import MemoryPort
from gateware.memory_map import RAM_END, BOOT_RAM_BASE, BOOT_RAM_SIZE, ETHMAC_BASE
from gateware.ethernet_dma import EthernetDMA


class EthernetRingDMA(LiteXModule):
    def __init__(self, bus, reader, writer):
        self._control = CSRStorage(name='control')
        self._rx_base = CSRStorage(32, name='rx_base')
        self._tx_base = CSRStorage(32, name='tx_base')
        self._mask = CSRStorage(4, name='mask')
        self._rx_consumer = CSRStorage(16, name='rx_consumer')
        self._tx_producer = CSRStorage(16, name='tx_producer')
        self._rx_producer = CSRStatus(16, name='rx_producer')
        self._tx_consumer = CSRStatus(16, name='tx_consumer')
        self._busy = CSRStatus(name='busy')
        self._error = CSRStatus(name='error')
        self._rx_packets = CSRStatus(32, name='rx_packets')
        self._tx_packets = CSRStatus(32, name='tx_packets')
        self.active = Signal()
        self.rx_valid, self.rx_slot, self.rx_length = Signal(), Signal(), Signal(12)
        self.rx_release = Signal()
        self.tx_ready, self.tx_done = Signal(), Signal()
        self.tx_start, self.tx_slot, self.tx_length = Signal(), Signal(), Signal(12)
        self.ev = EventManager()
        self.ev.rx = EventSourceLevel()
        self.ev.tx = EventSourcePulse()
        self.ev.finalize()

        def region_valid(base, size):
            end = Signal(33)
            self.comb += end.eq(base + size)
            return ((base[:4] == 0) & (base >= 4096) & (end <= RAM_END) &
                ((end <= BOOT_RAM_BASE) | (base >= BOOT_RAM_BASE + BOOT_RAM_SIZE)))

        depth = Signal(5)
        ring_bytes = Signal(9)
        mask = self._mask.storage
        rx_base, tx_base = self._rx_base.storage, self._tx_base.storage
        self.comb += [depth.eq(mask + 1), ring_bytes.eq(depth << 4)]
        config_valid = (((mask == 0) | (mask == 1) | (mask == 3) | (mask == 7) | (mask == 15)) &
            region_valid(rx_base, ring_bytes) & region_valid(tx_base, ring_bytes) &
            ((rx_base + ring_bytes <= tx_base) | (tx_base + ring_bytes <= rx_base)))
        enable = self._control.storage & config_valid
        self.comb += self.active.eq(self._control.storage)
        rx_head, tx_head = self._rx_producer.status, self._tx_consumer.status
        rx_used, tx_used = Signal(16), Signal(16)
        self.comb += [rx_used.eq(rx_head - self._rx_consumer.storage),
            tx_used.eq(self._tx_producer.storage - tx_head)]

        cr, cw = MemoryPort('read', reader.address_width), MemoryPort('write', writer.address_width)
        dr, dw = MemoryPort('read', reader.address_width), MemoryPort('write', writer.address_width)
        self.copy = EthernetDMA(bus, cr, cw, with_csr=False)
        copy = self.copy.transfer
        receive, turn = Signal(), Signal()
        descriptor = Signal(reader.address_width)
        buffer, capacity, flags = Signal(32), Signal(32), Signal(32)
        size = Signal(12)
        mac_slot = Signal()
        lane = Signal(2)
        failed = Signal()
        completed = Signal()
        self.fsm = fsm = FSM(reset_state='IDLE')
        copying = fsm.ongoing('COPY')
        # Descriptor traffic and packet traffic share the same buffered native
        # clients. A complete copy ends before descriptor completion publication.
        for actual, payload, desc in ((reader, cr, dr), (writer, cw, dw)):
            for field in ('valid', 'addr', 'we', 'count'):
                self.comb += getattr(actual.cmd, field).eq(Mux(copying,
                    getattr(payload.cmd, field), getattr(desc.cmd, field)))
            self.comb += [payload.cmd.ready.eq(actual.cmd.ready & copying),
                desc.cmd.ready.eq(actual.cmd.ready & ~copying),
                actual.cancel.eq(~enable | Mux(copying, payload.cancel, desc.cancel))]
        self.comb += [
            reader.rdata.ready.eq(Mux(copying, cr.rdata.ready, dr.rdata.ready)),
            cr.rdata.valid.eq(reader.rdata.valid & copying), cr.rdata.data.eq(reader.rdata.data),
            dr.rdata.valid.eq(reader.rdata.valid & ~copying), dr.rdata.data.eq(reader.rdata.data),
            writer.wdata.valid.eq(Mux(copying, cw.wdata.valid, dw.wdata.valid)),
            writer.wdata.data.eq(Mux(copying, cw.wdata.data, dw.wdata.data)),
            writer.wdata.we.eq(Mux(copying, cw.wdata.we, dw.wdata.we)),
            cw.wdata.ready.eq(writer.wdata.ready & copying), dw.wdata.ready.eq(writer.wdata.ready & ~copying),
            writer.done.ready.eq(Mux(copying, cw.done.ready, dw.done.ready)),
            cw.done.valid.eq(writer.done.valid & copying), dw.done.valid.eq(writer.done.valid & ~copying),
            dr.cancel.eq(~enable), dw.cancel.eq(~enable),
            copy.enable.eq(copying & enable), copy.receive.eq(receive), copy.memory.eq(buffer),
            copy.slot.eq(ETHMAC_BASE + Mux(receive, 0, 0x1000) + (mac_slot << 11)),
            copy.length.eq(size), self.tx_slot.eq(mac_slot), self.tx_length.eq(size),
            dr.cmd.addr.eq(descriptor), dr.cmd.we.eq(0), dr.cmd.count.eq(4),
            dw.cmd.addr.eq(descriptor), dw.cmd.we.eq(1), dw.cmd.count.eq(4),
            dw.wdata.data.eq((1 << 30) | (failed << 29) | size),
            dw.wdata.we.eq(Mux(lane == 2, 15, 0)),
            self.ev.rx.trigger.eq(rx_head != self._rx_consumer.storage),
            self.ev.tx.trigger.eq(completed),
            self._busy.status.eq(~fsm.ongoing('IDLE') | copy.busy | ~reader.idle | ~writer.idle)]
        self.sync += [completed.eq(0),
            If(self._control.storage & ~config_valid, self._error.status.eq(1))]

        def action(name, *statements):
            fsm.act(name, If(~enable, NextState('IDLE')).Else(*statements))

        fsm.act('IDLE',
            If(~self._control.storage,
                NextValue(rx_head, 0), NextValue(tx_head, 0),
                NextValue(self._rx_packets.status, 0), NextValue(self._tx_packets.status, 0),
                NextValue(self._error.status, 0), NextValue(turn, 0)),
            If(enable,
                If((rx_used > depth) | (tx_used > depth), NextValue(self._error.status, 1))
                .Elif(self.rx_valid & (rx_used < depth) & (~turn | (tx_used == 0)),
                    NextValue(receive, 1), NextValue(size, self.rx_length),
                    NextValue(mac_slot, self.rx_slot), NextValue(descriptor, (rx_base >> 4) + (rx_head & mask)),
                    NextValue(turn, 1), NextState('DESC_COMMAND'))
                .Elif((tx_used != 0) & self.tx_ready,
                    NextValue(receive, 0), NextValue(mac_slot, tx_head[0]),
                    NextValue(descriptor, (tx_base >> 4) + (tx_head & mask)),
                    NextValue(turn, 0), NextState('DESC_COMMAND'))))
        action('DESC_COMMAND', dr.cmd.valid.eq(1),
            If(dr.cmd.ready, NextValue(lane, 0), NextState('DESC_READ')))
        action('DESC_READ', dr.rdata.ready.eq(1), If(dr.rdata.valid,
            If(lane == 0, NextValue(buffer, dr.rdata.data)),
            If(lane == 1, NextValue(capacity, dr.rdata.data)),
            If(lane == 2, NextValue(flags, dr.rdata.data)),
            If(lane == 3, NextState('VALIDATE')).Else(NextValue(lane, lane + 1))))
        payload_size = Mux(receive, size, capacity)
        payload_valid = (region_valid(buffer, payload_size) & (payload_size >= 14) & (payload_size <= 1530) &
            (~receive | (size <= capacity)) &
            ((buffer + payload_size <= rx_base) | (buffer >= rx_base + ring_bytes)) &
            ((buffer + payload_size <= tx_base) | (buffer >= tx_base + ring_bytes)))
        action('VALIDATE',
            If(~flags[31], NextValue(self._error.status, 1), NextState('IDLE'))
            .Else(NextValue(failed, ~payload_valid),
                If(~receive, NextValue(size, capacity[:12])),
                If(payload_valid, NextState('COPY'))
                .Else(NextValue(self._error.status, 1), NextState('COMPLETE_COMMAND'))))
        # A stopped copy keeps the packet bus selected until its outstanding
        # Wishbone cycle acknowledges. PortBuffer cancellation drains DDR work.
        fsm.act('COPY',
            If(~enable, If(~copy.busy, NextState('IDLE')))
            .Elif(copy.done,
                NextValue(failed, copy.error),
                If(copy.error, NextValue(self._error.status, 1), NextState('COMPLETE_COMMAND'))
                .Elif(receive, NextState('COMPLETE_COMMAND')).Else(NextState('TX_START'))))
        action('TX_START', self.tx_start.eq(1),
            If(self.tx_ready, NextState('TX_WAIT')))
        action('TX_WAIT', If(self.tx_done, NextState('COMPLETE_COMMAND')))
        action('COMPLETE_COMMAND', dw.cmd.valid.eq(1),
            If(dw.cmd.ready, NextValue(lane, 0), NextState('COMPLETE_DATA')))
        action('COMPLETE_DATA', dw.wdata.valid.eq(1), If(dw.wdata.ready,
            If(lane == 3, NextState('COMPLETE_COMMIT')).Else(NextValue(lane, lane + 1))))
        action('COMPLETE_COMMIT', dw.done.ready.eq(1),
            If(dw.done.valid, NextState('PUBLISH')))
        action('PUBLISH',
            If(receive, self.rx_release.eq(1), NextValue(rx_head, rx_head + 1),
                If(~failed, NextValue(self._rx_packets.status, self._rx_packets.status + 1)))
            .Else(NextValue(tx_head, tx_head + 1), NextValue(completed, 1),
                If(~failed, NextValue(self._tx_packets.status, self._tx_packets.status + 1))),
            NextState('IDLE'))


def attach_ring_to_mac(ring, mac):
    """Checked seam into the pinned LiteEth status/command FIFOs.

    The ring exclusively owns MAC queue release/start, including while stopped.
    Fail generation if the pinned library changes these assignments.
    """
    from migen.fhdl.structure import _Assign
    sram = mac.interface.sram
    rx, tx = sram.writer, sram.reader
    def replace(module, predicate, value):
        matches = [i for i, stmt in enumerate(module._fragment.comb)
            if isinstance(stmt, _Assign) and predicate(stmt)]
        if len(matches) != 1:
            raise ValueError('Pinned LiteEth ring DMA handshake changed')
        i = matches[0]
        stmt = module._fragment.comb[i]
        module._fragment.comb[i] = stmt.l.eq(value(stmt.r))
    replace(rx, lambda s: s.l is rx.stat_fifo.source.ready,
        lambda old: ring.rx_release)
    replace(tx, lambda s: s.r is tx._start.wr_stb,
        lambda old: ring.tx_start)
    replace(tx, lambda s: s.r is tx._slot.storage,
        lambda old: ring.tx_slot)
    replace(tx, lambda s: s.r is tx._length.storage,
        lambda old: ring.tx_length)
    # Do not expose inert legacy transmit commands in the DMA register map.
    legacy_commands = [tx._start, tx._slot, tx._length]
    mac.csrs = [csr for csr in mac.csrs
        if not any(csr is command for command in legacy_commands)]
    for name in ('_start', '_slot', '_length'):
        delattr(tx, name)
    ring.comb += [ring.rx_valid.eq(rx.stat_fifo.source.valid),
        ring.rx_slot.eq(rx.stat_fifo.source.slot), ring.rx_length.eq(rx.stat_fifo.source.length),
        ring.tx_ready.eq(tx._ready.status), ring.tx_done.eq(tx.ev.done.trigger)]
