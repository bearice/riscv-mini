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
        limit = Signal(32)      # ddr_size - row_bytes, loaded in INIT
        self.sync += row_bytes.eq(width << 1)  # RGB565: 2 bytes/pixel
        # One adder per state (see FSM): ROW_INC adds stride, ROW_SETUP
        # derives geometry from the registered row_start.
        nxt_row_start = Signal(32)
        self.comb += nxt_row_start.eq(row_start + dst_stride)
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
        # Replicated pixels are block-aligned; shift by the row's byte
        # parity so odd dst/stride keep pixel phase (pattern period is
        # one pixel = 2 bytes, so parity shift is sufficient).
        wr_pattern = Signal(256)
        self.comb += wr_pattern.eq(Cat(*[fg[:16]] * 16) << Cat(Constant(0, 3), offset[0]))
        # Registered outputs: the arbiter mux to the DDR IP must be the
        # only logic after these registers (it fits timing for the
        # existing CPU/video ports; comb mask/data paths did not).
        out_addr, out_data, out_mask = Signal(29), Signal(256), Signal(32)
        self.comb += [
            self.ui.cmd.eq(0), self.ui.burst.eq(0),
            self.ui.addr.eq(out_addr),
            self.ui.wr_data.eq(out_data),
            self.ui.wr_data_mask.eq(out_mask),
            self.ui.wr_data_end.eq(0),
        ]
        load_out = Signal()
        self.sync += If(load_out, out_addr.eq(beat_byte[2:31] & ~Constant(7, 29)),
                        out_data.eq(wr_pattern), out_mask.eq(beat_mask))

        self.fsm = fsm = FSM(reset_state='IDLE')
        # PREP registers the current beat's addr/data/mask; SEND then
        # presents them for exactly one DDR accept cycle.
        self.comb += load_out.eq(fsm.ongoing('PREP'))
        self.comb += self.busy.eq(~fsm.ongoing('IDLE'))
        fsm.act('IDLE',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), self.ui.wr_data_mask.eq(0xffffffff),
                self.ui.addr.eq(dst_addr[2:31] & ~Constant(7, 29)),
                If(start, NextValue(err, 0), NextValue(elapsed, 0),
                   NextValue(row, 0), NextValue(beat, 0), NextValue(row_start, dst_addr),
                   NextState('INIT')))
        fsm.act('INIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(limit, ddr_size - row_bytes), NextState('ROW_SETUP'))
        # Range per row is a single registered compare against limit.
        fsm.act('ROW_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(op != OP_FILL_RECT, NextValue(err, ERR_UNSUPPORTED), NextState('FINISH'))
                .Elif((width == 0) | (height == 0), NextValue(err, ERR_PARAM), NextState('FINISH'))
                .Elif(row_start > limit, NextValue(err, ERR_ADDR), NextState('FINISH'))
                .Else(NextValue(beats, beats_w),
                      NextValue(beat_base, row_start & ~Constant(31, 32)),
                      NextValue(offset, row_start[0:5]),
                      NextValue(first_mask, Cat(*first_bits)),
                      NextValue(last_mask, Cat(*last_bits)), NextState('PREP')))
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
        fsm.act('ROW_INC',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(row, row + 1), NextValue(beat, 0),
                NextValue(row_start, nxt_row_start), NextState('ROW_SETUP'))
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
