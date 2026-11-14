"""Transaction arbitration for the untagged, single-beat DDR3 user interface."""
from migen import Constant, If, Mux, NextState, NextValue, Signal
from litex.gen import LiteXModule
from gateware.machines.mega138k_ae350.onehot_fsm import OneHotFSM
from litex.soc.interconnect import stream


class DDRMemoryArbiter(LiteXModule):
    """One registered round-robin grant across CPU, video and network.

    A single arbitration level avoids a ready/command/ready cone through
    cascaded arbiters. An accepted untagged read keeps its owner until its
    actual response, regardless of a client's subsequent pending level.

    Accepted commands are posted into one output register that alone drives
    the DDR controller. Client readiness therefore depends only on arbiter
    registers, never on the controller's ready or another client's command.
    A port is regranted at most every other cycle either way.
    """
    def __init__(self,memory,cpu,video,network,usb=None,gfx2d=None):
        from migen import Array, Cat, Case
        ports=[cpu,video,network]
        self.cpu_pending,self.video_pending,self.network_pending=[Signal() for _ in ports]
        pending=[self.cpu_pending,self.video_pending,self.network_pending]
        self.cpu_commands,self.video_commands,self.network_commands=[Signal(32) for _ in ports]
        counts=[self.cpu_commands,self.video_commands,self.network_commands]
        self.usb_pending = Signal()
        self.usb_commands = Signal(32)
        if usb is not None:
            ports.append(usb)
            pending.append(self.usb_pending)
            counts.append(self.usb_commands)
        self.gfx2d_pending = Signal()
        self.gfx2d_commands = Signal(32)
        if gfx2d is not None:
            ports.append(gfx2d)
            pending.append(self.gfx2d_pending)
            counts.append(self.gfx2d_commands)
        # Client pending levels come from distant domains and modules; the
        # grant logic sees them one cycle late. A stale level at most offers
        # one unused grant, which the next cycle withdraws.
        pend_q=[Signal() for _ in pending]
        self.sync += [q.eq(p) for q,p in zip(pend_q,pending)]
        pending=pend_q
        choices = {}
        for previous in range(len(ports)):
            choice = previous
            for step in range(len(ports)-1, 0, -1):
                index = (previous + step) % len(ports)
                choice = Mux(pending[index], index, choice)
            choices[previous] = choice
        self.max_video_wait=Signal(32);self.unexpected_response=Signal()
        self.read_pending=busy=Signal()
        id_bits=(len(ports)-1).bit_length()
        grant,owner,last=Signal(id_bits),Signal(id_bits),Signal(id_bits,reset=2)
        valid=Signal();chosen=Signal(id_bits)
        any_pending=Signal();active=Signal();command=Signal()
        selected_pending=Array(pending)[grant]
        def select(field):return Array([getattr(p,field) for p in ports])[grant]
        read=Signal()
        out_valid=Signal();out_read=Signal(reset_less=True);issued=Signal()
        out={field:Signal(len(getattr(memory,field)),reset_less=True)
             for field in ('addr','cmd','wr_data','wr_data_mask')}
        # Per-port readiness and response ownership are registered from the
        # next arbiter state, so each client's handshake starts at arbiter
        # registers.
        ready=[Signal() for _ in ports];own=[Signal() for _ in ports]
        ready_q=[Signal() for _ in ports]
        hit=[Signal() for _ in ports]
        # One-hot copy of grant for the wide payload mux, so the binary
        # grant register does not fan out across every payload bit.
        grant_sel=[Signal(reset_less=True) for _ in ports]
        for s in grant_sel:
            s.attr.add(("syn_maxfan", 32))
        valid_next=Signal();grant_next=Signal(id_bits)
        busy_next=Signal();out_valid_next=Signal()
        # Next state assuming no command this cycle. An accepted command
        # clears valid_next, so it only needs to gate readiness at the end.
        valid_idle=Signal();busy_idle=Signal()
        self.comb += [any_pending.eq(Cat(*pending)!=0),
            Case(last,{i:chosen.eq(value) for i,value in choices.items()}),
            # The registered grant owns readiness. Pending selects the next
            # grant; cmd_en remains the validity qualifier for acceptance.
            active.eq(Cat(*ready)!=0),read.eq(select('cmd')==1),
            # Writers raise cmd_en together with their data only after seeing
            # ready, so readiness must not depend on either enable.
            command.eq(Cat(*[r & p.cmd_en for r,p in zip(ready,ports)])!=0),
            valid_idle.eq(valid),grant_next.eq(grant),
            If(~valid & any_pending,valid_idle.eq(1),grant_next.eq(chosen)),
            If(valid & ~selected_pending,valid_idle.eq(0)),
            valid_next.eq(valid_idle & ~command),
            busy_idle.eq(busy & ~memory.rd_data_valid),
            busy_next.eq(busy),
            If(command & read,busy_next.eq(1)),
            If(busy & memory.rd_data_valid,busy_next.eq(0)),
            # A command is only possible with the slot empty, and the slot
            # can only issue when full, so each side needs one term only.
            out_valid_next.eq(Mux(out_valid,~issued,
                Cat(*[q & p.cmd_en for q,p in zip(ready_q,ports)])!=0)),
            *[getattr(memory,field).eq(value) for field,value in out.items()],
            memory.burst.eq(0),
            memory.cmd_en.eq(out_valid & (out_read | memory.wr_data_rdy)),
            memory.wr_data_en.eq(out_valid & ~out_read & memory.cmd_ready & memory.wr_data_rdy),
            memory.wr_data_end.eq(memory.wr_data_en),
            issued.eq(memory.cmd_en & memory.cmd_ready)]
        # The payload loads whenever the slot is free, so its enable is a
        # register and not the selected client's command decode.
        def select_onehot(field):
            terms=[Mux(s,getattr(p,field),0) for s,p in zip(grant_sel,ports)]
            value=terms[0]
            for t in terms[1:]:
                value=value|t
            return value
        self.sync += [
            *[s.eq(grant_next==i) for i,s in enumerate(grant_sel)],
            If(~out_valid,*[value.eq(select_onehot(field)) for field,value in out.items()],
               out_read.eq(read)),
            out_valid.eq(out_valid_next)]
        # Read responses are registered once beside the controller, one cycle
        # after the raw response; each port's valid is its own register.
        rd_data_q=Signal(len(memory.rd_data),reset_less=True)
        rd_valid_q=[Signal() for _ in ports]
        self.sync += rd_data_q.eq(memory.rd_data)
        # The registered readiness assumes no command this cycle; a command
        # always fills the output slot, so gating with out_valid withdraws it
        # in the following cycle without client enables reaching the register.
        # Without a command the slot's next state is out_valid & ~issued, so
        # the same gate also covers a slot still waiting for the controller.
        for i,p in enumerate(ports):
            # Once granted, this port's command is the selected command.
            self.comb += [ready[i].eq(ready_q[i] & ~out_valid),
                p.cmd_ready.eq(ready[i]),p.wr_data_rdy.eq(ready[i]),
                p.rd_data.eq(rd_data_q),p.rd_data_valid.eq(rd_valid_q[i])]
            self.sync += [
                ready_q[i].eq(valid_idle & ~busy_idle & (grant_next==i)),
                If(command & read & ready[i],own[i].eq(1))
                .Elif(memory.rd_data_valid,own[i].eq(0)),
                rd_valid_q[i].eq(memory.rd_data_valid & own[i]),
                hit[i].eq(command & ready[i]),
                If(hit[i],counts[i].eq(counts[i]+1))]
        video_waiting = Signal()
        self.sync += video_waiting.eq(self.video_pending | own[1])
        waited=Signal(24)
        self.sync += [
            valid.eq(valid_next),grant.eq(grant_next),busy.eq(busy_next),
            If(command,last.eq(grant),If(read,owner.eq(grant))),
            If(memory.rd_data_valid & ~busy,self.unexpected_response.eq(1)),
            If(video.rd_data_valid | ~video_waiting,waited.eq(0))
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
        self.request = request = stream.Endpoint([('base', 32), ('stride', 32), ('epoch', 32), ('run', 1),
                                                  ('wrap_start', 32), ('wrap_end', 32)])
        self.source = source = stream.Endpoint([('data', 256), ('block', 32), ('epoch', 32)])
        self.pending = Signal()
        # Registered source.block == 0, valid whenever source.valid is.
        self.first_block = Signal()
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
        wrap_start = Signal(32)
        wrap_end = Signal(32)
        wrap_enabled = Signal()
        wrap_valid = Signal()
        wrap_span = Signal(32)
        wrap_limit = Signal(32)
        wrapped_row = Signal(32)
        column = Signal(11) if runtime else Signal(max=max(2, blocks_per_row))
        index = Signal(23 if runtime else 32)
        data = Signal(256)
        elapsed = Signal(max=response_timeout + 1)
        end = Signal(33)
        self.fsm = fsm = OneHotFSM(reset_state='IDLE')
        self.comb += [
            ui.cmd.eq(1), ui.burst.eq(0),
            ui.addr.eq(address[2:31] & ~Constant(7, 29)),
            ui.wr_data_en.eq(0), ui.wr_data_end.eq(0), ui.wr_data_mask.eq(0xffffffff),
            source.data.eq(data), source.block.eq(index), source.epoch.eq(epoch),
            self.busy.eq(~fsm.ongoing('IDLE')),
            wrapped_row.eq(row_base + stride - wrap_span),
        ]

        def accept():
            return [NextValue(base, request.base), NextValue(stride, request.stride),
                    NextValue(epoch, request.epoch), NextValue(self.enabled, request.run),
                    NextValue(wrap_start, request.wrap_start), NextValue(wrap_end, request.wrap_end),
                    NextValue(address, request.base),
                    NextValue(row_base, request.base), NextValue(column, 0), NextValue(index, 0),
                    NextValue(elapsed, 0), NextState('EXTENT')]

        fsm.act('IDLE', request.ready.eq(1), If(request.valid, *accept()))
        # Bound calculation is once per frame, away from the FIFO admission
        # and invalid-address state transition. It adds no per-block latency.
        # The base/stride checks are registered in BOUND, so CHECK only ORs
        # flags into the address error and state decode.
        layout_bad = Signal()
        layout_check = NextValue(layout_bad,
            (base < min_base) | (base[:5] != 0) | (stride < row_bytes) |
            (stride[:5] != 0) | (stride >= 65536))
        if runtime:
            row_span = Signal(32)
            # Inputs are stable from accept() onward; BOUND reads the value
            # sampled in EXTENT, so no FSM-decoded enable is needed.
            self.sync += row_span.eq((height-1)*stride[:16])
            fsm.act('EXTENT',
                NextValue(wrap_span, wrap_end - wrap_start),
                NextValue(wrap_limit, wrap_end - row_bytes),
                NextState('BOUND')
            )
            fsm.act('BOUND',
                NextValue(end, base + row_span + row_bytes),
                layout_check,
                NextValue(wrap_enabled, wrap_end != 0),
                NextValue(wrap_valid,
                    (wrap_start[:5] == 0) & (wrap_end[:5] == 0) &
                    (wrap_start >= min_base) & (wrap_end <= ram_bytes) &
                    (wrap_end > wrap_start) &
                    (wrap_span >= stride) &
                    (base >= wrap_start) & (base <= wrap_limit)
                ),
                NextState('CHECK')
            )
        else:
            fsm.act('EXTENT',
                NextValue(end, base + (height-1)*stride + row_bytes),
                NextValue(wrap_span, wrap_end - wrap_start),
                NextValue(wrap_limit, wrap_end - row_bytes),
                NextState('BOUND')
            )
            fsm.act('BOUND',
                layout_check,
                NextValue(wrap_enabled, wrap_end != 0),
                NextValue(wrap_valid,
                    (wrap_start[:5] == 0) & (wrap_end[:5] == 0) &
                    (wrap_start >= min_base) & (wrap_end <= ram_bytes) &
                    (wrap_end > wrap_start) &
                    (wrap_span >= stride) &
                    (base >= wrap_start) & (base <= wrap_limit)
                ),
                NextState('CHECK')
            )
        fsm.act('CHECK',
            If(~self.enabled, NextState('IDLE'))
            .Elif(layout_bad | Mux(wrap_enabled, ~wrap_valid, end > ram_bytes),
                NextValue(self.address_error, 1), NextState('IDLE'),
            ).Else(NextState('SEND')),
        )
        # A request superseding SEND or PUSH work is a cancellation.
        self.sync += If((fsm.ongoing('SEND') | fsm.ongoing('PUSH')) & request.valid,
                        self.cancelled.eq(self.cancelled + 1))
        fsm.act('SEND',
            request.ready.eq(1),
            If(request.valid,
                *accept(),
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
        nxt_normal_row = Signal(32)
        nxt_wrapped_row = Signal(32)
        nxt_will_wrap = Signal()
        nxt_row = Signal(32)
        self.comb += nxt_row.eq(Mux(wrap_enabled & nxt_will_wrap, nxt_wrapped_row, nxt_normal_row))
        # Index/column settle in SEND/READ before PUSH. Reuse their
        # registered terminal flags rather than a subtract/compare CE cone.
        # Next-row addresses are also registered ahead of time so last_column
        # carries no 32-bit addition or comparator cones.
        index_p1 = Signal(len(index))
        self.sync += [
            index_p1.eq(index + 1),
            self.first_block.eq(index == 0),
            last_block.eq(index == blocks - 1),
            last_column.eq(column == blocks_per_row - 1),
            nxt_normal_row.eq(row_base + stride),
            nxt_wrapped_row.eq(wrapped_row),
            nxt_will_wrap.eq(row_base + stride > wrap_limit),
        ]
        self.comb += self.reserve_next.eq(fsm.ongoing('PUSH') & source.ready &
                                         ~request.valid & ~last_block)
        fsm.act('PUSH',
            request.ready.eq(1),
            If(request.valid,
                *accept(),
            ).Else(
                source.valid.eq(1),
                If(source.ready,
                    If(last_block,
                        NextValue(self.frames, self.frames + 1), NextState('IDLE'),
                    ).Else(
                        NextValue(index, index_p1), NextValue(elapsed, 0),
                        If(last_column,
                            NextValue(column, 0),
                            NextValue(row_base, nxt_row),
                            NextValue(address, nxt_row),
                        ).Else(NextValue(column, column + 1), NextValue(address, address + 32)),
                        NextState('SEND'),
                    ),
                ),
            ),
        )
