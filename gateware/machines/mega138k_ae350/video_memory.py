"""Transaction arbitration for the untagged, single-beat DDR3 user interface."""
from migen import Constant, FSM, If, Mux, NextState, NextValue, Signal
from litex.gen import LiteXModule
from litex.soc.interconnect import stream


class DDRMemoryArbiter(LiteXModule):
    """One registered round-robin grant across CPU, video and network.

    A single arbitration level avoids a ready/command/ready cone through
    cascaded arbiters. An accepted untagged read keeps its owner until its
    actual response, regardless of a client's subsequent pending level.
    """
    def __init__(self,memory,cpu,video,network):
        from migen import Array, Cat, Case
        ports=[cpu,video,network]
        self.cpu_pending,self.video_pending,self.network_pending=[Signal() for _ in ports]
        pending=[self.cpu_pending,self.video_pending,self.network_pending]
        self.cpu_commands,self.video_commands,self.network_commands=[Signal(32) for _ in ports]
        counts=[self.cpu_commands,self.video_commands,self.network_commands]
        self.max_video_wait=Signal(32);self.unexpected_response=Signal()
        self.read_pending=busy=Signal()
        grant,owner,last=Signal(2),Signal(2),Signal(2,reset=2)
        valid=Signal();chosen=Signal(2)
        any_pending=Signal();active=Signal();command=Signal()
        selected_pending=Array(pending)[grant]
        def select(field):return Array([getattr(p,field) for p in ports])[grant]
        read=Signal()
        self.comb += [any_pending.eq(Cat(*pending)!=0),
            Case(last,{0:chosen.eq(Mux(pending[1],1,Mux(pending[2],2,0))),
                       1:chosen.eq(Mux(pending[2],2,Mux(pending[0],0,1))),
                       'default':chosen.eq(Mux(pending[0],0,Mux(pending[1],1,2)))}),
            active.eq(valid & ~busy & selected_pending),read.eq(select('cmd')==1),
            memory.addr.eq(select('addr')),memory.cmd.eq(select('cmd')),memory.burst.eq(0),
            memory.wr_data.eq(select('wr_data')),memory.wr_data_mask.eq(select('wr_data_mask')),
            memory.wr_data_en.eq(active & ~read & select('cmd_en') & select('wr_data_en') &
                                memory.cmd_ready & memory.wr_data_rdy),
            memory.wr_data_end.eq(memory.wr_data_en),
            memory.cmd_en.eq(active & select('cmd_en') & (read | (select('wr_data_en') & memory.wr_data_rdy))),
            command.eq(memory.cmd_en & memory.cmd_ready)]
        for i,p in enumerate(ports):
            accepted=Signal()
            self.comb += [p.cmd_ready.eq(active & (grant==i) & memory.cmd_ready & (read|memory.wr_data_rdy)),
                p.wr_data_rdy.eq(active & (grant==i) & memory.cmd_ready & memory.wr_data_rdy),
                p.rd_data.eq(memory.rd_data),p.rd_data_valid.eq(memory.rd_data_valid & busy & (owner==i))]
            self.sync += [accepted.eq(command & (grant==i)),If(accepted,counts[i].eq(counts[i]+1))]
        waited=Signal(24)
        self.sync += [
            If(~valid & any_pending,valid.eq(1),grant.eq(chosen)),
            If(valid & ~selected_pending,valid.eq(0)),
            If(command,valid.eq(0),last.eq(grant),If(read,busy.eq(1),owner.eq(grant))),
            If(busy & memory.rd_data_valid,busy.eq(0)),
            If(memory.rd_data_valid & ~busy,self.unexpected_response.eq(1)),
            If(video.rd_data_valid | (~self.video_pending & ~(busy & (owner==1))),waited.eq(0))
            .Elif(waited!=0xffffff,waited.eq(waited+1)),
            If(waited>self.max_video_wait,self.max_video_wait.eq(waited))]


class VideoMemoryArbiter(LiteXModule):
    """Round-robin CPU transactions and read-only scanout, in the DDR domain.

    A read retains ownership until the actual response arrives, even if its
    client times out. An untagged late response must never complete the other
    client's read. CPU writes accept command and data together, as required
    by the existing AHB adapter. ``cpu_pending`` is independent of ready: a
    write's enables alone cannot indicate demand because they depend on grant.
    """
    def __init__(self, memory, cpu, video, video_read_only=True):
        self.cpu_pending = Signal()
        self.video_pending = Signal()
        self.cpu_commands = Signal(32)
        self.video_commands = Signal(32)
        self.max_video_wait = Signal(32)
        self.unexpected_response = Signal()
        self.read_pending = busy = Signal()
        owner = Signal()  # 0 CPU, 1 video
        last = Signal(reset=1)
        choose_video = Signal()
        grant_valid = Signal()
        grant_owner = Signal()
        next_video = Signal()
        selected_pending = Signal()
        active = Signal()
        selected_read = Signal()
        command = Signal()
        waited = Signal(24)
        self.comb += [
            next_video.eq(self.video_pending & (~self.cpu_pending | ~last)),
            choose_video.eq(grant_owner),
            selected_pending.eq(Mux(grant_owner,self.video_pending,self.cpu_pending)),
            active.eq(grant_valid & ~busy),
            selected_read.eq(Mux(choose_video, 1 if video_read_only else video.cmd == 1, cpu.cmd == 1)),
            memory.addr.eq(Mux(choose_video, video.addr, cpu.addr)),
            memory.cmd.eq(Mux(choose_video, 1 if video_read_only else video.cmd, cpu.cmd)),
            memory.burst.eq(0),
            memory.wr_data.eq(cpu.wr_data if video_read_only else Mux(choose_video,video.wr_data,cpu.wr_data)),
            memory.wr_data_mask.eq(cpu.wr_data_mask if video_read_only else Mux(choose_video,video.wr_data_mask,cpu.wr_data_mask)),
            memory.wr_data_en.eq(active & ~selected_read &
                Mux(choose_video,0 if video_read_only else video.wr_data_en & video.cmd_en,
                    cpu.wr_data_en & cpu.cmd_en) & memory.cmd_ready & memory.wr_data_rdy),
            memory.wr_data_end.eq(memory.wr_data_en),
            memory.cmd_en.eq(active & Mux(choose_video, video.cmd_en if video_read_only else
                video.cmd_en & ((video.cmd==1)|(video.wr_data_en & memory.wr_data_rdy)),
                cpu.cmd_en & ((cpu.cmd == 1) | (cpu.wr_data_en & memory.wr_data_rdy)))),
            cpu.cmd_ready.eq(active & ~choose_video & memory.cmd_ready &
                              ((cpu.cmd == 1) | memory.wr_data_rdy)),
            cpu.wr_data_rdy.eq(active & ~choose_video & memory.cmd_ready & memory.wr_data_rdy),
            video.cmd_ready.eq(active & choose_video & memory.cmd_ready &
                (1 if video_read_only else ((video.cmd==1)|memory.wr_data_rdy))),
            video.wr_data_rdy.eq(0 if video_read_only else
                active & choose_video & memory.cmd_ready & memory.wr_data_rdy),
            command.eq(memory.cmd_en & memory.cmd_ready),
            cpu.rd_data.eq(memory.rd_data), video.rd_data.eq(memory.rd_data),
            # The physical DRAM response has at least one clock of latency;
            # never reinterpret an unsolicited beat as a newly accepted read.
            cpu.rd_data_valid.eq(memory.rd_data_valid & busy & ~owner),
            video.rd_data_valid.eq(memory.rd_data_valid & busy & owner),
        ]
        # Grants can be reserved while a previous read waits for its beat.
        # They are independent of this cycle's client enables, cutting the
        # client -> arbitration -> ready -> client state combinational loop.
        video.cmd_ready.attr.add(('syn_keep',1))
        cpu.cmd_ready.attr.add(('syn_keep',1))
        cpu_accepted,video_accepted=Signal(),Signal()
        self.sync += [cpu_accepted.eq(command & ~choose_video),
                      video_accepted.eq(command & choose_video),
                      If(cpu_accepted,self.cpu_commands.eq(self.cpu_commands+1)),
                      If(video_accepted,self.video_commands.eq(self.video_commands+1))]
        self.sync += [
            If(~grant_valid & (self.cpu_pending | self.video_pending),
                grant_valid.eq(1),grant_owner.eq(next_video)),
            If(grant_valid & ~selected_pending,grant_valid.eq(0)),
            If(command,
                grant_valid.eq(0),
                last.eq(choose_video),
                If(selected_read,
                    busy.eq(1), owner.eq(choose_video)),
            ),
            If(busy & memory.rd_data_valid, busy.eq(0)),
            If(memory.rd_data_valid & ~busy,
                self.unexpected_response.eq(1)),
            # Include both arbitration and physical DRAM response latency.
            If(video.rd_data_valid | (~self.video_pending & ~(busy & owner)), waited.eq(0))
            .Elif(waited != Constant(0xffffff, 24), waited.eq(waited + 1)),
            If(waited > self.max_video_wait, self.max_video_wait.eq(waited)),
        ]


class VideoFrameReader(LiteXModule):
    """One RGB565 frame per request, with cancellable prefetch and tagged beats.

    A fresh vblank request supersedes unsent work. An accepted read is drained
    first. A timed-out untagged read stays quarantined until its real response,
    so neither the arbiter nor the next frame can consume it accidentally.
    Backpressure may stop prefetch indefinitely without sending extra commands.
    """
    def __init__(self, ui, width=640, height=480, min_base=0x100000,
                 ram_bytes=1 << 30, response_timeout=1 << 18, runtime=False):
        if width <= 0 or width % 16 or height <= 0:
            raise ValueError('RGB565 frame width must be a positive multiple of 16')
        if runtime:
            self.row_bytes = Signal(16, reset=width * 2)
            self.rows = Signal(12, reset=height)
            row_bytes, height = self.row_bytes, self.rows
            blocks_per_row = Signal(11)
            blocks = Signal(23)
            self.sync += [blocks_per_row.eq(self.row_bytes >> 5),
                          blocks.eq(blocks_per_row * self.rows)]
        else:
            row_bytes = width * 2
            blocks_per_row = width // 16
        if not runtime:
            blocks = blocks_per_row * height
        self.request = request = stream.Endpoint([('base', 32), ('stride', 32), ('epoch', 32), ('run', 1)])
        self.source = source = stream.Endpoint([('data', 256), ('block', 32), ('epoch', 32)])
        self.pending = Signal()
        self.reserve_next = Signal()
        self.address_error = Signal()
        self.timeout_error = Signal()
        self.frames = Signal(32)
        self.cancelled = Signal(32)
        self.busy = Signal()
        self.enabled = Signal()
        base = Signal(32)
        stride = Signal(32)
        epoch = Signal(32)
        address = Signal(32)
        row_base = Signal(32)
        column = Signal(11) if runtime else Signal(max=max(2, blocks_per_row))
        index = Signal(23 if runtime else 32)
        data = Signal(256)
        elapsed = Signal(max=response_timeout + 1)
        end = Signal(33)
        self.fsm = fsm = FSM(reset_state='IDLE')
        self.comb += [
            ui.cmd.eq(1), ui.burst.eq(0),
            ui.addr.eq(address[2:31] & ~Constant(7, 29)),
            ui.wr_data_en.eq(0), ui.wr_data_end.eq(0), ui.wr_data_mask.eq(0xffffffff),
            source.data.eq(data), source.block.eq(index), source.epoch.eq(epoch),
            self.busy.eq(~fsm.ongoing('IDLE')),
        ]

        def accept():
            return [NextValue(base, request.base), NextValue(stride, request.stride),
                    NextValue(epoch, request.epoch), NextValue(self.enabled, request.run),
                    NextValue(address, request.base),
                    NextValue(row_base, request.base), NextValue(column, 0), NextValue(index, 0),
                    NextValue(elapsed, 0), NextState('EXTENT')]

        fsm.act('IDLE', request.ready.eq(1), If(request.valid, *accept()))
        # Bound calculation is once per frame, away from the FIFO admission
        # and invalid-address state transition. It adds no per-block latency.
        if runtime:
            row_span = Signal(32)
            fsm.act('EXTENT',NextValue(row_span,(height-1)*stride[:16]),NextState('BOUND'))
            fsm.act('BOUND',NextValue(end,base+row_span+row_bytes),NextState('CHECK'))
        else:
            fsm.act('EXTENT',NextValue(end,base+(height-1)*stride+row_bytes),NextState('CHECK'))
        fsm.act('CHECK',
            If(~self.enabled, NextState('IDLE'))
            .Elif((base < min_base) | (base[:5] != 0) | (stride < row_bytes) |
               (stride[:5] != 0) | (stride >= 65536) | (end > ram_bytes),
                NextValue(self.address_error, 1), NextState('IDLE'),
            ).Else(NextState('SEND')),
        )
        fsm.act('SEND',
            request.ready.eq(1),
            If(request.valid,
                NextValue(self.cancelled, self.cancelled + 1), *accept(),
            ).Else(
                self.pending.eq(1), ui.cmd_en.eq(1),
                If(ui.cmd_ready,
                    NextValue(elapsed, 0), NextState('READ'),
                ).Elif(elapsed == response_timeout,
                    NextValue(self.timeout_error, 1), NextState('IDLE'),
                ).Else(NextValue(elapsed, elapsed + 1)),
            ),
        )
        fsm.act('READ',
            If(ui.rd_data_valid,
                NextValue(data, ui.rd_data), NextState('PUSH'),
            ).Elif(elapsed == response_timeout,
                NextValue(self.timeout_error, 1), NextState('DRAIN'),
            ).Else(NextValue(elapsed, elapsed + 1)),
        )
        fsm.act('DRAIN', If(ui.rd_data_valid, NextState('IDLE')))
        last_block = Signal()
        last_column = Signal()
        # Index/column settle in SEND/READ before PUSH. Reuse their
        # registered terminal flags rather than a subtract/compare CE cone.
        self.sync += [last_block.eq(index == blocks - 1),
                      last_column.eq(column == blocks_per_row - 1)]
        self.comb += self.reserve_next.eq(fsm.ongoing('PUSH') & source.ready &
                                         ~request.valid & ~last_block)
        fsm.act('PUSH',
            request.ready.eq(1),
            If(request.valid,
                NextValue(self.cancelled, self.cancelled + 1), *accept(),
            ).Else(
                source.valid.eq(1),
                If(source.ready,
                    If(last_block,
                        NextValue(self.frames, self.frames + 1), NextState('IDLE'),
                    ).Else(
                        NextValue(index, index + 1), NextValue(elapsed, 0),
                        If(last_column,
                            NextValue(column, 0), NextValue(row_base, row_base + stride),
                            NextValue(address, row_base + stride),
                        ).Else(NextValue(column, column + 1), NextValue(address, address + 32)),
                        NextState('SEND'),
                    ),
                ),
            ),
        )
