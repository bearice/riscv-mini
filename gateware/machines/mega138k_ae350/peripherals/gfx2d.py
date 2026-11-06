"""GFX2D 2D pixel engine: APB control (sys) + DDR data plane (ddr).

Step 1 landed the APB register skeleton; Step 2 adds the DDR data plane
for FILL_RECT (OP=0). COPY_AREA / IMAGE_BLIT report ERR_UNSUPPORTED
until their engines land. See docs/platforms/mega138k_ae350/gfx2d.md.

CDC protocol: sys-side descriptor registers stay stable from the last
APB write until completion. START toggles ``req``; the ddr engine
synchronizes it, latches the descriptor, runs, then toggles ``ack``
with the result code in ``done_err``. sys synchronizes ``ack`` and
samples ``done_err`` only after the toggle arrives, so both multi-bit
buses are stable for many cycles before they are sampled.
"""
from migen import Array, Cat, ClockDomainsRenamer, Constant, FSM, If, Mux, NextState, NextValue, Signal
from litex.gen import LiteXModule
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI

ERR_NONE = 0
ERR_UNSUPPORTED = 1
ERR_BUSY = 2
ERR_PARAM = 3
ERR_ADDR = 4
ERR_TIMEOUT = 5

OP_FILL_RECT = 0
OP_COPY_AREA = 1
OP_IMAGE_BLIT = 2

DESC_NAMES = ('OP', 'SRC_ADDR', 'DST_ADDR', 'SRC_STRIDE', 'DST_STRIDE',
              'WIDTH', 'HEIGHT', 'FG_COLOR', 'BG_COLOR', 'BLIT_SRC_WB')


class Gfx2DEngine(LiteXModule):
    """Fill-rect engine, runs in the ``ddr`` clock domain.

    Drives one untagged single-beat DDR3UI port. Address is the
    32-byte block number (byte_addr[2:31] with low bits cleared, same
    encoding as VideoFrameReader / DDR3AHB); ``wr_data_mask`` covers
    the partial blocks at row edges, so arbitrary dst/stride/width
    are handled without read-modify-write.
    """
    def __init__(self, ddr_size=1 << 30, response_timeout=1 << 18):
        self.ui = DDR3UI()
        self.req = Signal()
        self.ack = Signal()
        self.done_err = Signal(32)
        self.desc = desc = [Signal(32) for _ in DESC_NAMES]
        self.busy = Signal()

        op, src_addr, dst_addr, src_stride, dst_stride = desc[:5]
        width, height, fg, bg, blit_wb = desc[5:]

        req_sync = [Signal() for _ in range(2)]
        self.sync += [req_sync[0].eq(self.req), req_sync[1].eq(req_sync[0])]
        start = req_sync[1] != self.ack

        row_bytes = Signal(32)
        row = Signal(32)
        beat = Signal(32)
        beats = Signal(32)
        elapsed = Signal(max=response_timeout + 1)
        err = Signal(32)

        beat_base = Signal(32)
        offset = Signal(32)
        row_start = Signal(32)  # incremental: no row*stride multiplier
        src_row_start = Signal(32)  # copy: src row addr; blit: src row byte addr
        limit = Signal(32)      # ddr_size - row_bytes, loaded in INIT
        src_limit = Signal(32)  # copy: ddr_size - row_bytes; blit: ddr_size - blit_wb
        dir_down = Signal()     # copy: src < dst -> iterate backwards (overlap)
        self.sync += row_bytes.eq(width << 1)  # RGB565: 2 bytes/pixel
        # One adder per state (see FSM): ROW_INC adds stride, ROW_SETUP
        # derives geometry from the registered row_start.
        nxt_row_start = Signal(32)
        nxt_src_row_start = Signal(32)
        self.comb += [
            nxt_row_start.eq(Mux(dir_down & (op == OP_COPY_AREA),
                                 row_start - dst_stride, row_start + dst_stride)),
            nxt_src_row_start.eq(Mux(op == OP_COPY_AREA,
                                     Mux(dir_down, src_row_start - src_stride,
                                         src_row_start + src_stride),
                                     src_row_start + blit_wb)),
        ]
        beat_byte = Signal(32)
        self.comb += beat_byte.eq(beat_base + (beat << 5))
        # Per-row edge masks, registered in ROW_SETUP: middle beats are
        # full, only first/last can be partial. Per-beat work is a mux.
        first_mask, last_mask = Signal(32), Signal(32)
        beats_w = Signal(32)
        self.comb += beats_w.eq((row_start[0:5] + row_bytes + 31) >> 5)
        first_end = Signal(6)
        self.comb += first_end.eq(Mux(row_bytes > 32 - row_start[0:5], 32,
                                     row_start[0:5] + row_bytes))
        first_bits = [(row_start[0:5] <= i) & (i < first_end) for i in range(32)]
        # Last beat end: (offset + row_bytes - 1) mod 32, +1 for count
        # (full 32 when the total is a multiple of 32).
        last_count = Signal(6)
        self.comb += last_count.eq((row_start[0:5] + row_bytes - 1)[0:5] + 1)
        last_bits = [i < last_count for i in range(32)]
        beat_mask = Signal(32)
        self.comb += beat_mask.eq(Mux(beat == 0, first_mask,
                                     Mux(beat == beats - 1, last_mask, 0xffffffff)))
        # COPY_AREA source beat base (offsets are equal, see ROW_SETUP).
        src_beat_base = Signal(32)
        # IMAGE_BLIT source byte stream state.
        src_beat_buf = Signal(256)
        src_beat_loaded = Signal()
        src_byte_off = Signal(32)
        src_bytes_left = Signal(32)
        bit_buf = Signal(16)
        bit_cnt = Signal(5)
        copy_data = Signal(256)
        # Replicated pixels are block-aligned; shift by the row's byte
        # parity so odd dst/stride keep pixel phase (pattern period is
        # one pixel = 2 bytes, so parity shift is sufficient).
        wr_pattern = Signal(256)
        self.comb += wr_pattern.eq(Cat(*[fg[:16]] * 16) << Cat(Constant(0, 3), offset[0]))
        # Blit expansion: top 16 bits of bit_buf, MSB-first per byte.
        # Registered in B_EXP, shifted in B_PREP (two stages for timing).
        blit_expanded = Signal(256)
        blit_pix = [Mux(bit_buf[15 - j], fg[:16], bg[:16]) for j in range(16)]
        self.comb += blit_expanded.eq(Cat(*blit_pix))  # pix0 is LSB = first pixel
        blit_exp_reg = Signal(256)
        # No per-beat shift: the dst row byte offset is absorbed at row
        # setup as doff/2 dummy pixels in the bit stream (see ROW_SETUP),
        # so beat slot s of beat i always shows stream pixel 16i+s.
        blit_shifted = Signal(256)
        self.comb += blit_shifted.eq(blit_exp_reg)
        # Registered outputs: the arbiter mux to the DDR IP must be the
        # only logic after these registers (it fits timing for the
        # existing CPU/video ports; comb mask/data paths did not).
        out_addr, out_data, out_mask = Signal(29), Signal(256), Signal(32)
        prep_data = Signal(256)
        self.comb += [
            self.ui.cmd.eq(0),
            self.ui.burst.eq(0),
            self.ui.addr.eq(out_addr),
            self.ui.wr_data.eq(out_data),
            self.ui.wr_data_mask.eq(out_mask),
            self.ui.wr_data_end.eq(0),
        ]
        load_out = Signal()
        self.sync += If(load_out, out_addr.eq(beat_byte[2:31] & ~Constant(7, 29)),
                        out_data.eq(prep_data), out_mask.eq(beat_mask))
        src_byte = Signal(8)
        self.comb += src_byte.eq((src_beat_buf >> (src_byte_off[0:5] << 3))[:8])

        self.fsm = fsm = FSM(reset_state='IDLE')
        # PREP-family states register the current beat's addr/data/mask;
        # the SEND state then presents them for one DDR accept cycle.
        self.comb += load_out.eq(fsm.ongoing('PREP') | fsm.ongoing('C_WPREP') |
                                 fsm.ongoing('B_PREP'))
        self.comb += prep_data.eq(Mux(fsm.ongoing('PREP'), wr_pattern,
                                     Mux(fsm.ongoing('C_WPREP'), copy_data, blit_shifted)))
        rd_addr = Signal(29)
        self.comb += [
            self.ui.cmd.eq(Mux(fsm.ongoing('C_RSEND') | fsm.ongoing('B_RD_SEND'), 1, 0)),
            self.ui.addr.eq(Mux(fsm.ongoing('C_RSEND') | fsm.ongoing('B_RD_SEND'),
                                rd_addr, out_addr)),
        ]
        rd_base = Signal(32)
        self.comb += rd_base.eq(Mux(fsm.ongoing('B_RD_PREP'), src_beat_base,
                                    src_beat_base + (beat << 5)))
        self.sync += If(fsm.ongoing('C_RPREP') | fsm.ongoing('B_RD_PREP'),
                        rd_addr.eq(rd_base[2:31] & ~Constant(7, 29)))
        self.comb += self.busy.eq(~fsm.ongoing('IDLE'))
        fsm.act('IDLE',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), self.ui.wr_data_mask.eq(0xffffffff),
                self.ui.addr.eq(dst_addr[2:31] & ~Constant(7, 29)),
                If(start, NextValue(err, 0), NextValue(elapsed, 0),
                   NextValue(row, 0), NextValue(beat, 0), NextValue(row_start, dst_addr),
                   NextValue(src_row_start, src_addr),
                   NextState('INIT')))
        fsm.act('INIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(limit, ddr_size - row_bytes),
                NextValue(src_limit, Mux(op == OP_IMAGE_BLIT,
                                         ddr_size - blit_wb, ddr_size - row_bytes)),
                NextValue(dir_down, src_addr < dst_addr),
                NextState('ROW_START'))
        # Copy-down needs the last row's start; compute it incrementally
        # in ROW_START (one stride add per cycle would be slow, so use a
        # dedicated state that steps row/row_start from the last row:
        # actually compute via a single multiply is avoided; instead we
        # start from dst and step down by iterating ROW_START height
        # times would be O(n) states. Simpler: handle descending by
        # starting at row 0 and pre-advancing row_start in INIT2 with a
        # loop-free approach: step in ROW_START until row reaches
        # height-1 -- but that is O(height) cycles, acceptable (480 max)
        # and uses only one adder per cycle.
        fsm.act('ROW_START',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(dir_down & (op == OP_COPY_AREA) & (row != height - 1),
                   NextValue(row, row + 1), NextValue(row_start, row_start + dst_stride),
                   NextValue(src_row_start, src_row_start + src_stride), NextState('ROW_START'))
                .Else(NextState('ROW_SETUP')))
        # Range per row is a single registered compare against limit.
        pascal = [NextValue(beats, beats_w),
                 NextValue(beat_base, row_start & ~Constant(31, 32)),
                 NextValue(offset, row_start[0:5]),
                 NextValue(first_mask, Cat(*first_bits)),
                 NextValue(last_mask, Cat(*last_bits))]
        fsm.act('ROW_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(op > OP_IMAGE_BLIT, NextValue(err, ERR_UNSUPPORTED), NextState('FINISH'))
                .Elif((width == 0) | (height == 0) | (dst_stride < row_bytes),
                      NextValue(err, ERR_PARAM), NextState('FINISH'))
                .Elif((op == OP_COPY_AREA) &
                      ((src_stride < row_bytes) |
                       (src_addr[0:5] != dst_addr[0:5]) |
                       (src_stride[0:5] != dst_stride[0:5])),
                      NextValue(err, ERR_PARAM), NextState('FINISH'))
                .Elif((op == OP_IMAGE_BLIT) &
                      ((blit_wb < (width + 7) >> 3) | (dst_addr[0] != 0) | (dst_stride[0] != 0)),
                      NextValue(err, ERR_PARAM), NextState('FINISH'))
                .Elif(row_start > limit, NextValue(err, ERR_ADDR), NextState('FINISH'))
                .Elif((op != OP_FILL_RECT) & (src_row_start > src_limit),
                      NextValue(err, ERR_ADDR), NextState('FINISH'))
                .Else(*pascal,
                      If(op == OP_COPY_AREA,
                         NextValue(src_beat_base, src_row_start & ~Constant(31, 32)),
                         NextState('C_RPREP'))
                      .Elif(op == OP_IMAGE_BLIT,
                            NextValue(src_beat_base, src_row_start & ~Constant(31, 32)),
                            NextValue(src_byte_off, src_row_start[0:5]),
                            NextValue(src_beat_loaded, 0),
                            NextValue(src_bytes_left, blit_wb),
                            NextValue(bit_buf, 0),
                            NextValue(bit_cnt, row_start[0:5] >> 1),  # doff/2 dummy px
                            NextState('B_FETCH'))
                      .Else(NextState('PREP'))))
        last_row = Mux(dir_down & (op == OP_COPY_AREA), row == 0, row == height - 1)
        fsm.act('PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('SEND'))
        fsm.act('SEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1), self.ui.wr_data_end.eq(1),
                If(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    If(beat == beats - 1,
                        If(row == height - 1, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1), NextState('PREP')),
                ).Elif(elapsed == response_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('C_RPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('C_RSEND'))
        fsm.act('C_RSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(0),
                If(self.ui.cmd_ready,
                    NextValue(elapsed, 0), NextState('C_RWAIT'),
                ).Elif(elapsed == response_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('C_RWAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(self.ui.rd_data_valid,
                    NextValue(copy_data, self.ui.rd_data), NextState('C_WPREP'),
                ).Elif(elapsed == response_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('C_WPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('C_WSEND'))
        down = dir_down & (op == OP_COPY_AREA)
        fsm.act('C_WSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1), self.ui.wr_data_end.eq(1),
                If(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    If(Mux(down, beat == 0, beat == beats - 1),
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, Mux(down, beat - 1, beat + 1)),
                           NextState('C_RPREP')),
                ).Elif(elapsed == response_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('B_FETCH',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(bit_cnt >= 16, NextState('B_EXP'))
                .Elif(src_bytes_left == 0, NextState('B_EXP'))
                .Elif(src_byte_off == 32,
                      NextValue(src_beat_base, src_beat_base + 32),
                      NextValue(src_byte_off, 0), NextValue(src_beat_loaded, 0),
                      NextState('B_FETCH'))
                .Elif(~src_beat_loaded, NextState('B_RD_PREP'))
                # Valid bits are left-aligned (top of bit_buf); fetch
                # places the new byte just below the valid region.
                .Else(NextValue(bit_buf, bit_buf | (Cat(Constant(0, 8), src_byte) >> bit_cnt)),
                      NextValue(bit_cnt, bit_cnt + 8),
                      NextValue(src_byte_off, src_byte_off + 1),
                      NextValue(src_bytes_left, src_bytes_left - 1),
                      NextState('B_FETCH')))
        fsm.act('B_RD_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('B_RD_SEND'))
        fsm.act('B_RD_SEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(0),
                If(self.ui.cmd_ready,
                    NextValue(elapsed, 0), NextState('B_RD_WAIT'),
                ).Elif(elapsed == response_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('B_RD_WAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(self.ui.rd_data_valid,
                    NextValue(src_beat_buf, self.ui.rd_data),
                    NextValue(src_beat_loaded, 1), NextState('B_FETCH'),
                ).Elif(elapsed == response_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('B_EXP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(blit_exp_reg, blit_expanded), NextState('B_PREP'))
        fsm.act('B_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('B_WSEND'))
        fsm.act('B_WSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1), self.ui.wr_data_end.eq(1),
                If(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    NextValue(bit_buf, bit_buf << 16),
                    NextValue(bit_cnt, Mux(bit_cnt > 16, bit_cnt - 16, 0)),
                    If(beat == beats - 1,
                        If(row == height - 1, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1), NextState('B_FETCH')),
                ).Elif(elapsed == response_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('ROW_INC',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(row, Mux(down, row - 1, row + 1)), NextValue(beat, 0),
                NextValue(row_start, nxt_row_start),
                NextValue(src_row_start, nxt_src_row_start), NextState('ROW_SETUP'))
        fsm.act('FINISH',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(self.done_err, err), NextValue(self.ack, ~self.ack), NextState('IDLE'))


class Gfx2D(LiteXModule):
    """APB register block (sys domain) + DDR engine (ddr domain)."""

    def __init__(self, ddr_size=1 << 30):
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.commit = Signal()
        self.selected = Signal()
        self.rdata = Signal(32)
        self.ok = Signal()
        self.irq = Signal()
        self.ui = DDR3UI()

        reg = amap.GFX2D_REGS
        # Descriptor registers (latched, writable only while idle).
        desc = {}
        for name in DESC_NAMES:
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

        self.engine = engine = ClockDomainsRenamer('ddr')(Gfx2DEngine(ddr_size=ddr_size))
        # Descriptor bus is stable from last APB write until ack, so the
        # ddr side may sample it directly after req synchronization.
        for i, name in enumerate(DESC_NAMES):
            self.comb += engine.desc[i].eq(desc[name])
        self.comb += [engine.ui.cmd_ready.eq(self.ui.cmd_ready),
                      engine.ui.wr_data_rdy.eq(self.ui.wr_data_rdy),
                      engine.ui.rd_data.eq(self.ui.rd_data),
                      engine.ui.rd_data_valid.eq(self.ui.rd_data_valid),
                      self.ui.cmd.eq(engine.ui.cmd), self.ui.cmd_en.eq(engine.ui.cmd_en),
                      self.ui.addr.eq(engine.ui.addr), self.ui.burst.eq(engine.ui.burst),
                      self.ui.wr_data.eq(engine.ui.wr_data), self.ui.wr_data_en.eq(engine.ui.wr_data_en),
                      self.ui.wr_data_end.eq(engine.ui.wr_data_end),
                      self.ui.wr_data_mask.eq(engine.ui.wr_data_mask)]
        self.req = Signal()
        self.comb += engine.req.eq(self.req)
        ack_sync = [Signal() for _ in range(2)]
        self.sync += [ack_sync[0].eq(engine.ack), ack_sync[1].eq(ack_sync[0])]
        ack_seen = Signal()
        complete = ack_sync[1] != ack_seen

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
        from migen import Case as _Case
        self.comb += [
            read_ok.eq(0),
            self.rdata.eq(0),
            _Case(self.off, {off: [read_ok.eq(1), self.rdata.eq(value)]
                            for off, value in reads.items()}),
        ]

        write_offsets = [reg['CONTROL'], reg['IRQ_ENABLE'], reg['IRQ_STATUS'],
                         reg['CLEAR_ERRORS']] + [reg[n] for n in desc]
        is_write_reg = Signal()
        is_control = Signal()
        self.comb += [
            is_write_reg.eq(0),
            is_control.eq(self.off == reg['CONTROL']),
            _Case(self.off, {off: is_write_reg.eq(1) for off in write_offsets}),
        ]
        write_ok = Signal()
        self.comb += [
            write_ok.eq(is_write_reg & (~is_control | (self.wdata == 1))),
            self.ok.eq(self.selected & Mux(self.write, write_ok, read_ok)),
        ]

        # Fabric commit is a single-cycle pulse with off/write/wdata held
        # stable, and ok is combinational, so qualify it combinationally.
        # (A registered admitted would miss the pulse entirely.)
        # Direct qualified pulse: the fabric asserts commit for exactly
        # one sys cycle with off/write/wdata held stable (same contract
        # as the video/audio peripherals).
        go = self.commit & self.selected & self.write & self.ok

        # Registered interrupt output: keeps the fabric IRQ Cat shallow.
        self.sync += self.irq.eq(self.irq_pending & self.irq_enable)

        # Completion from the ddr engine (ack toggle synchronized).
        self.sync += If(complete,
                        ack_seen.eq(ack_sync[1]), self.busy.eq(0), self.done.eq(1),
                        self.error.eq(engine.done_err != 0),
                        self.last_error.eq(engine.done_err),
                        If(engine.done_err != 0, self.errors.eq(self.errors + 1)),
                        self.irq_pending.eq(1))

        self.sync += If(go, _Case(self.off, {
            reg['CONTROL']: [
                If(self.busy, self.errors.eq(self.errors + 1),
                   self.last_error.eq(ERR_BUSY), self.error.eq(1))
                .Else(self.commands.eq(self.commands + 1), self.busy.eq(1),
                      self.done.eq(0), self.error.eq(0), self.last_error.eq(ERR_NONE),
                      self.req.eq(~self.req))],
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
