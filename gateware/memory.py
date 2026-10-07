"""Narrow client ports with private burst buffers in front of the shared L2.

A port command names one 16-byte line and the number of client words to
transfer. Read data is a consumed snapshot, never a reusable private cache.
Write data acceptance is independent of commit: completion follows L2 ACK.
"""
from migen import Signal, Array, If, Case, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litedram.common import LiteDRAMNativePort
from gateware.shared_l2 import SharedL2

BURST_BYTES = 16


class MemoryPort:
    """Aligned burst command, narrow data, and explicit write completion.

    addr is a 16-byte-line address relative to DDR, count is 1..words_per_burst.
    cancel discards partial writes and unread responses; offered native commands
    remain stable until accepted and then drain, even when a client stops.
    Only one command can be outstanding on each port.
    """
    def __init__(self, mode, address_width=23, data_width=32):
        if mode not in ('read', 'write', 'both') or data_width not in (16, 32):
            raise ValueError('Invalid buffered memory port')
        self.mode, self.address_width, self.data_width = mode, address_width, data_width
        self.words_per_burst = BURST_BYTES * 8 // data_width
        self.cmd = stream.Endpoint([('addr', address_width), ('we', 1),
                                    ('count', (self.words_per_burst).bit_length())])
        self.wdata = stream.Endpoint([('data', data_width), ('we', data_width // 8)])
        self.rdata = stream.Endpoint([('data', data_width)])
        self.done = stream.Endpoint([])
        self.cancel = Signal()


class PortBuffer(LiteXModule):
    """Hold a whole burst without holding the global arbiter on client stalls."""
    def __init__(self, port, native, cancel=0, pause=0):
        if native.data_width != 128:
            raise ValueError('Port buffer requires a 128-bit memory backend')
        width, words = port.data_width, port.words_per_burst
        payload = Signal(128)
        mask = Signal(16)
        address = Signal(native.address_width)
        count = Signal(max=words + 1)
        lane = Signal(max=words)
        write = Signal()
        abandoned = Signal()
        stop = port.cancel | cancel
        self.busy = Signal()
        self.pending_write = Signal()
        self.fsm = fsm = FSM(reset_state='IDLE')
        self.comb += [
            native.cmd.addr.eq(address), native.cmd.we.eq(write), native.cmd.last.eq(1),
            native.wdata.data.eq(payload), native.wdata.we.eq(mask),
            port.rdata.data.eq(Array(payload[i*width:(i+1)*width] for i in range(words))[lane]),
            port.rdata.last.eq(lane == count - 1),
        ]
        self.sync += If(stop & self.busy, abandoned.eq(1))
        valid_command = (port.cmd.count != 0) & (port.cmd.count <= words)
        if port.mode != 'both':
            valid_command = valid_command & (port.cmd.we == (port.mode == 'write'))
        fsm.act('IDLE',
            If(port.cmd.valid & port.cmd.ready,
                NextValue(address, port.cmd.addr), NextValue(count, port.cmd.count),
                NextValue(write, port.cmd.we), NextValue(lane, 0), NextValue(mask, 0),
                NextValue(abandoned, 0),
                If(port.cmd.we, NextState('COLLECT')).Else(NextState('COMMAND'))))
        fsm.act('COLLECT',
            If(stop, NextState('IDLE')).Elif(port.wdata.valid,
                Case(lane, {i: [NextValue(payload[i*width:(i+1)*width], port.wdata.data),
                    NextValue(mask[i*(width//8):(i+1)*(width//8)], port.wdata.we)] for i in range(words)}),
                If(lane == count - 1, NextValue(lane, 0), NextState('COMMAND'))
                .Else(NextValue(lane, lane + 1))))
        # No global command is presented until all write data is buffered.
        fsm.act('COMMAND',
            If(native.cmd.ready,
                If(write, NextState('COMMIT')).Else(NextState('RECEIVE'))))
        fsm.act('COMMIT',
            If(native.wdata.ready,
                If(stop | abandoned, NextState('IDLE')).Else(NextState('DONE'))))
        fsm.act('DONE',
            If(stop | abandoned | port.done.ready, NextState('IDLE')))
        fsm.act('RECEIVE',
            If(native.rdata.valid,
                NextValue(payload, native.rdata.data), NextValue(lane, 0),
                If(stop | abandoned, NextState('IDLE')).Else(NextState('DELIVER'))))
        fsm.act('DELIVER',
            If(stop | abandoned, NextState('IDLE')).Elif(port.rdata.ready,
                If(lane == count - 1, NextState('IDLE')).Else(NextValue(lane, lane + 1))))

        # Decode endpoint outputs directly from the state register. Keeping
        # valid/ready out of the next-state block avoids grouped combinational
        # handshake feedback in emitted Verilog when two FSMs are connected.
        fsm.finalize()
        state = lambda name: fsm.state == fsm.encoding[name]
        self.comb += [self.busy.eq(~state('IDLE')),
            self.pending_write.eq(write & ~state('IDLE') & ~state('DONE')),
            port.cmd.ready.eq(state('IDLE') & ~stop & ~pause & valid_command),
            port.wdata.ready.eq(state('COLLECT') & ~stop),
            native.cmd.valid.eq(state('COMMAND')),
            native.wdata.valid.eq(state('COMMIT')),
            native.rdata.ready.eq(state('RECEIVE')),
            port.done.valid.eq(state('DONE') & ~stop & ~abandoned),
            port.rdata.valid.eq(state('DELIVER') & ~stop & ~abandoned)]


class SharedMemoryController(LiteXModule):
    """Own narrow port buffers, arbitration and the common writeback cache."""
    def __init__(self, cpu, backend, size=4096, enabled=1, video=True, sd=True):
        from gateware.native_dma import NativeDMAArbiter
        video_native = LiteDRAMNativePort('read', backend.address_width, 128)
        dma_native = LiteDRAMNativePort('both', backend.address_width, 128)
        self.video = MemoryPort('read', backend.address_width, 16)
        self.sd_read = MemoryPort('read', backend.address_width, 32)
        self.sd_write = MemoryPort('write', backend.address_width, 32)
        self.sd_reset = Signal()
        maintenance_pending = Signal()
        maintenance_ready = Signal(reset=1)
        if video:
            self.video_buffer = PortBuffer(self.video, video_native)
        else:
            self.comb += [video_native.cmd.valid.eq(0), video_native.rdata.ready.eq(1)]
        if sd:
            self.sd_arbiter = NativeDMAArbiter(dma_native, 2)
            self.sd_read_buffer = PortBuffer(self.sd_read, self.sd_arbiter.ports[0], self.sd_reset)
            self.sd_write_buffer = PortBuffer(self.sd_write, self.sd_arbiter.ports[1],
                                              self.sd_reset, maintenance_pending)
            self.comb += maintenance_ready.eq(~self.sd_write_buffer.pending_write)
        else:
            self.comb += [dma_native.cmd.valid.eq(0), dma_native.wdata.valid.eq(0),
                          dma_native.rdata.ready.eq(1), maintenance_ready.eq(1)]
        self.l2 = SharedL2(cpu, backend, size=size, writeback=True, enabled=enabled,
                           video=video_native, dma=dma_native, boot_ram=True,
                           maintenance_ready=maintenance_ready)
        self.comb += maintenance_pending.eq(self.l2.maintenance_pending)
        # SoC exposes the existing l2 CSR bank through its public alias.
        self.autocsr_exclude = {'l2'}
