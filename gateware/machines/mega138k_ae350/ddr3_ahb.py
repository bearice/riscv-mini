"""Gowin DDR3 user interface and AHB-Lite adapter for the AE350 data port.

The vendor IP exposes a 256-bit, 32-byte single-beat user interface clocked by
its own ``clk_out`` (100 MHz at 400 MHz CK).  The AE350 data-memory port is a
64-bit AHB-Lite master clocked by the configured fabric ``sys`` clock.  Two
asynchronous FIFOs cross between the domains. The UI returns one 32-byte
block. Later beats of that same validated AHB read burst are served from a
line buffer in the memory-port clock domain, so they do not cross back to
the UI. Independent NONSEQ reads refetch DRAM; there is no persistent cache.

Write aggregation
-----------------
Measured cache write-back is WRAP4, HSIZE=64-bit, HPROT=0xf: four beats, one
32-byte line. The master presents the next address only after the current beat
is acknowledged, and the registered decoder hides that address from the slave
until HREADY, so a beat cannot wait to *collect* later beats.

* A write beat whose already-visible successor is the next sequential beat of
  the same burst, with the same size and protection and inside the same
  32-byte line, is acknowledged OKAY as soon as its bytes enter the adapter
  buffer. Those bytes belong to the adapter, not to DRAM. HREADY is not held
  for a later beat or for the UI.
* BUSY keeps that buffer and does not issue a command. The current beat is
  still acknowledged once its bytes are buffered.
* The beat that closes the line (IDLE, NONSEQ, a read, a different block, or
  a different burst attribute) is acknowledged only after one masked UI write
  of the whole buffer has been accepted or has failed. Its HRESP is that
  result. Waiting here is for DRAM, not for a future beat: the successor is
  already visible, and the master holds it while HREADY is low.
* An idle flush (BUSY, then IDLE, with no further data beat) commits the
  buffer before any new transfer. Those beats were already acknowledged, so a
  flush failure cannot rewrite their HRESP. ``error_sticky`` records it and
  ``write_fault`` makes the next DDR access return ERROR once.
* A transfer the decoder has already accepted stays owned through that flush:
  its address, control and write data are captured on the one-cycle dispatch
  and are not completed by raising HREADY. The flush runs first. The owned
  transfer is then performed, or is the access that reports a flush failure.
* A later read is not forwarded from the buffer. The buffer is committed
  first, the read-block cache is invalidated, and the read is then a new UI
  command. One outstanding command preserves order.
* Independent NONSEQ writes do not merge, even in the same line. A reset
  clears the buffer; bytes acknowledged but not yet accepted by the UI are
  discarded. Calibration loss or a timeout at commit issues no further
  command, drops the buffer, and reports the error on the closing beat or,
  for an idle flush, on the next access. A late UI response is consumed by
  the existing recovery drain and cannot complete a later request.
* ``wr_data_mask`` bit 1 leaves that byte unchanged. Partial lines and tails
  set strobes only for bytes the master actually wrote.

Contracts (verified against the vendor reference):
  * ``ui.addr`` counts 4-byte words, so ``addr = (cpu_byte - base) >> 2``.
  * one command transfers 32 bytes; ``wr_data_mask`` bit = 1 masks a byte.
  * ``ui.cmd`` 0 = write, 1 = read.
  * request: write, size, addr, wdata[64], unused, line, line_data[256], line_mask[32].
    Reads use the low fields and leave ``line`` clear. A write commit sets
    ``line`` and carries the assembled 32-byte buffer. The unused bit used to
    request a UI-side read hit; the memory port now owns that decision.
    Response: rdata[256], error. A write response drives the data bits to 0.
"""
from migen import *
from migen.genlib.fifo import AsyncFIFO
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule


def _or_tree(nodes):
    """Balanced OR. A flat reduce becomes a carry chain on this FPGA."""
    nodes = list(nodes)
    if not nodes:
        return Constant(0)
    while len(nodes) > 1:
        reduced = []
        pending = iter(nodes)
        for left in pending:
            right = next(pending, None)
            reduced.append(left if right is None else (left | right))
        nodes = reduced
    return nodes[0]


def _equal(left, right):
    """Equality via a XOR tree. A 32-bit ``==`` was a carry chain on HREADY."""
    width = len(left)
    return ~_or_tree([left[index] ^ right[index] for index in range(width)])

# write, size, addr, wdata, unused, line, line_data, line_mask
REQ_WIDTH = 1 + 3 + 32 + 64 + 1 + 1 + 256 + 32
RESP_WIDTH = 256 + 1
_REQ_LINE = 101
_REQ_LINE_DATA = slice(102, 358)
_REQ_LINE_MASK = slice(358, 390)


class DDR3UI:
    """Bundle of the vendor IP user-interface signals (``ddr`` clock domain)."""
    def __init__(self):
        self.cmd_ready      = Signal()
        self.cmd            = Signal(3)
        self.cmd_en         = Signal()
        self.addr           = Signal(29)
        self.wr_data_rdy    = Signal()
        self.wr_data        = Signal(256)
        self.wr_data_en     = Signal()
        self.wr_data_end    = Signal()
        self.wr_data_mask   = Signal(32)
        self.rd_data        = Signal(256)
        self.rd_data_valid  = Signal()
        self.burst          = Signal()


class _ReadBufferedFIFO(LiteXModule):
    """Two read slots isolate raw Gray flags and consumer backpressure.

    A raw pop only depends on registered free space; consumer retirement
    changes the two-bit count and head, never a wide data-register enable.
    """
    def __init__(self, raw):
        self.submodules.raw = raw
        self.din, self.we, self.writable = raw.din, raw.we, raw.writable
        self.dout = Signal(len(raw.dout))
        self.re = Signal()
        self.readable = Signal()
        self.queued = Signal()
        count=Signal(2)
        head,tail=Signal(),Signal()
        head.attr.add(('syn_maxfan',8))
        words=[Signal(len(raw.dout)),Signal(len(raw.dout))]
        push,pop=Signal(),Signal()
        self.comb += [self.readable.eq(count!=0),self.queued.eq((count!=0)|raw.readable),
            push.eq(raw.readable & (count<2)),pop.eq(self.re & self.readable),
            raw.re.eq(push),self.dout.eq(Mux(head,words[1],words[0]))]
        self.sync.readbuffer += [
            If(push,tail.eq(~tail),If(tail,words[1].eq(raw.dout)).Else(words[0].eq(raw.dout))),
            If(pop,head.eq(~head)),
            If(push & ~pop,count.eq(count+1)).Elif(pop & ~push,count.eq(count-1))]


class DDR3AHB(LiteXModule):
    """AHB-Lite slave with transaction-scoped 256-bit read width conversion.

    ``ready_i`` is the DDR calibration status; while low every access returns a
    two-cycle ERROR without touching the IP.  Out-of-range and unaligned accesses also return ERROR.  Every wait state is bounded by
    ``response_timeout`` (fabric cycles) so a stuck IP cannot hang the CPU.
    """
    def __init__(self, bus, ui, ready_i, base=0x2000, size=1 << 30,
                 response_timeout=200_000, ui_timeout=1 << 18, fifo_cls=None,
                 accepted_i=None, end_burst_i=None, lookahead=None):
        assert bus.data_width in (32, 64)
        self.bus = bus
        self.base = base
        self.size = size
        self.error_sticky = Signal()
        self.write_fault = Signal()
        self.wbuf_valid = Signal()
        self.recovering = Signal()
        self.recovery_failed = Signal()
        self.ui_idle_ddr = Signal()
        self.ui_pending_ddr = Signal()
        self.ui_idle_sys = Signal()
        self.busy = Signal()
        # Diagnostics read through the ROM status window by the CPU JTAG.
        self.n_req = Signal(16)
        self.n_read = Signal(16)
        self.n_write = Signal(16)
        self.n_error = Signal(16)
        # Mem-domain line hits, and cycles this adapter presents HREADY low.
        # Both wrap. They are exported beside the image ABI, not inside it.
        self.n_rline_hit = Signal(32)
        self.n_wait_cycles = Signal(32)
        self.last_bad = Signal()
        self.last_in_range = Signal()
        self.last_aligned = Signal()
        self.last_write = Signal()
        self.last_size = Signal(3)
        self.last_addr = Signal(32)
        self.last_control = Signal(16)  # accepted HTRANS/HBURST/HSIZE/HPROT/HWRITE
        self.write_control = Signal(16)
        self.read_control = Signal(16)
        self.debug = Signal(32)
        self.debug_hi = Signal(32)
        # Per-transaction recorder (ddr domain): last accepted command and the
        # last read-data beat, so one store/load can be inspected directly.
        self.rec_w = Signal(32)      # write handshake: cmd, addr[28:0]
        self.rec_w_mask = Signal(32)
        self.rec_w_data = Signal(64)
        self.rec_w_n = Signal(16)     # 16-bit so a burst of fills cannot wrap
        self.rec_r = Signal(32)      # read handshake: cmd, addr[28:0]
        self.rec_r_blk = Signal(256) # full 256-bit block returned for that read
        self.rec_r_sel = Signal(64)  # 64-bit beat selected for that read
        self.rec_r_n = Signal(16)

        fifo_cls = fifo_cls or AsyncFIFO
        # This engine has one transaction in flight. Two slots allow the
        # Gray-pointer round trip without a fictitious eight-entry pipeline.
        req_raw = ClockDomainsRenamer({"write": "sys", "read": "ddr"})(fifo_cls(REQ_WIDTH, 2))
        resp_raw = ClockDomainsRenamer({"write": "ddr", "read": "sys"})(fifo_cls(RESP_WIDTH, 2))
        req = ClockDomainsRenamer({"readbuffer":"ddr"})(_ReadBufferedFIFO(req_raw))
        resp = ClockDomainsRenamer({"readbuffer":"sys"})(_ReadBufferedFIFO(resp_raw))
        self.submodules.req = req
        self.submodules.resp = resp

        # ----
        # AHB.
        # ----
        addr = Signal(32)
        is_write = Signal()
        tsize = Signal(3)
        lifetime = Signal(max=response_timeout + 2)
        resp_data = Signal(64)
        resp_error = Signal()
        resp_hold = Signal()
        chain = Signal()
        # One 32-byte read line, valid only inside the burst that fetched it.
        self.rline_valid = rline_valid = Signal()
        self.rline_block = rline_block = Signal(27)
        self.rline_data = rline_data = Signal(256)
        rline_hit = Signal()
        expected = Signal(32)
        burst_type = Signal(3)
        burst_size = Signal(3)
        burst_prot = Signal(4)
        burst_write = Signal()
        beats_left = Signal(5)
        step = Signal(32)
        wrap_mask = Signal(32)
        next_address = Signal(32)
        sequential = Signal()
        burst_continue = Signal()
        owned = Signal()
        owned_addr = Signal(32)
        owned_write = Signal()
        owned_size = Signal(3)
        owned_burst = Signal(3)
        owned_prot = Signal(4)
        owned_trans = Signal(2)
        owned_wdata = Signal(bus.data_width)
        saved_wdata = Signal()
        data_word = Signal(bus.data_width)
        # Owned-phase comparisons stay off the live HREADY equation. Mixing
        # them through must_flush pulled a carry chain into DDR_HREADY.
        o_step = Signal(32)
        o_wrap = Signal(32)
        o_next = Signal(32)
        o_continue = Signal()
        o_sequential = Signal()
        o_aligned = Signal()
        o_in_range = Signal()
        o_bad = Signal()
        self.comb += [
            data_word.eq(Mux(saved_wdata, owned_wdata, bus.wdata)),
            step.eq(Constant(1, 32) << bus.size),
            wrap_mask.eq(Mux(bus.burst == 2, (step << 2) - 1,
                         Mux(bus.burst == 4, (step << 3) - 1, (step << 4) - 1))),
            next_address.eq(Mux((bus.burst != 0) & ~bus.burst[0],
                (bus.addr & ~wrap_mask) | ((bus.addr + step) & wrap_mask), bus.addr + step)),
            burst_continue.eq(chain & (bus.trans == 3) & _equal(bus.addr, expected)
                & (bus.burst == burst_type) & (bus.size == burst_size)
                & (bus.prot == burst_prot) & (bus.write == burst_write)),
            # Read-line hits use this same chain. Writes merge through the buffer below.
            sequential.eq(burst_continue & ~bus.write),
            o_step.eq(Constant(1, 32) << owned_size),
            o_wrap.eq(Mux(owned_burst == 2, (o_step << 2) - 1,
                      Mux(owned_burst == 4, (o_step << 3) - 1, (o_step << 4) - 1))),
            o_next.eq(Mux((owned_burst != 0) & ~owned_burst[0],
                (owned_addr & ~o_wrap) | ((owned_addr + o_step) & o_wrap), owned_addr + o_step)),
            o_continue.eq(chain & (owned_trans == 3) & _equal(owned_addr, expected)
                & (owned_burst == burst_type) & (owned_size == burst_size)
                & (owned_prot == burst_prot) & (owned_write == burst_write)),
            o_sequential.eq(o_continue & ~owned_write),
            o_aligned.eq((owned_addr & ((1 << owned_size) - 1)) == 0),
            o_in_range.eq((owned_addr >= self.base) & (owned_addr < self.base + self.size)),
            o_bad.eq(~o_in_range | ~o_aligned | (owned_size > (bus.data_width // 8).bit_length() - 1)
                     | ~ready_i | self.recovering | self.recovery_failed),
        ]
        # The registered decoder presents HTRANS only on the acceptance cycle.
        # Sample the master's live controls so a data phase can see its
        # successor without a combinational path from the hard macro.
        peek_trans = Signal(2)
        peek_addr = Signal(32)
        peek_write = Signal()
        peek_burst = Signal(3)
        peek_size = Signal(3)
        peek_prot = Signal(4)
        if lookahead is None:
            self.comb += [peek_trans.eq(bus.trans), peek_addr.eq(bus.addr),
                          peek_write.eq(bus.write), peek_burst.eq(bus.burst),
                          peek_size.eq(bus.size), peek_prot.eq(bus.prot)]
        else:
            self.sync += [peek_trans.eq(lookahead.trans), peek_addr.eq(lookahead.addr),
                          peek_write.eq(lookahead.write), peek_burst.eq(lookahead.burst),
                          peek_size.eq(lookahead.size), peek_prot.eq(lookahead.prot)]
        peek_expected = Signal()
        peek_same_line = Signal()
        peek_same_buffer = Signal()
        for predicate in (peek_expected,peek_same_line,peek_same_buffer):
            predicate.attr.update({('syn_keep',1),('syn_preserve',1)})
        wbuf_data = Signal(256)
        wbuf_strb = Signal(32)
        wbuf_block = Signal(27)
        rel = Signal(32)
        peek_rel = Signal(32)
        beat_lane = Signal(3)
        same_line = Signal()
        write_merge = Signal()
        write_busy = Signal()
        must_flush = Signal()
        retire = Signal()
        commit_addr = Signal(32)
        same_control = Signal()
        same_control.attr.add(('syn_keep', 1))
        if lookahead is None:
            self.comb += same_control.eq(_equal(Cat(peek_burst, peek_size, peek_prot),
                                               Cat(burst_type, burst_size, burst_prot)))
        else:
            # Compare in parallel with the lookahead capture, rather than
            # after it on the HREADY/state/owned-address enable chain. Burst
            # context changes in DECODE; these predicates are not consumed
            # there or in DATA, so they are refreshed before WBUF/ADDRESS.
            self.sync += same_control.eq(_equal(
                Cat(lookahead.burst, lookahead.size, lookahead.prot),
                Cat(burst_type, burst_size, burst_prot)))
        if lookahead is None:
            self.comb += [peek_expected.eq(_equal(peek_addr,expected)),
                peek_same_line.eq(_equal(peek_rel[5:32],rel[5:32])),
                peek_same_buffer.eq(_equal(peek_rel[5:32],wbuf_block))]
        else:
            live_rel=Signal(32)
            self.comb += live_rel.eq(lookahead.addr-self.base)
            self.sync += [peek_expected.eq(_equal(lookahead.addr,expected)),
                peek_same_line.eq(_equal(live_rel[5:32],rel[5:32])),
                peek_same_buffer.eq(_equal(live_rel[5:32],wbuf_block))]
        # An aligned beat fits in one data-bus lane of the 32-byte line.
        # Shift only inside that lane; a 32-bit barrel fans out across the buffer.
        lane_bytes = bus.data_width // 8
        nlanes = 32 // lane_bytes
        lane_shift = (lane_bytes - 1).bit_length()
        lane_ones = Signal(lane_bytes)
        lane_strb = Signal(lane_bytes)
        self.comb += [
            rel.eq(addr - self.base),
            peek_rel.eq(peek_addr - self.base),
            lane_ones.eq(Mux(tsize == 0, 0x01,
                        Mux(tsize == 1, 0x03,
                        Mux(tsize == 2, 0x0f, (1 << lane_bytes) - 1)))),
            lane_strb.eq(lane_ones << rel[:lane_shift]),
            beat_lane.eq(rel[lane_shift:lane_shift + (nlanes - 1).bit_length()]),
            same_line.eq(self.wbuf_valid & _equal(wbuf_block, rel[5:32])),
            write_merge.eq(is_write & chain & (peek_trans == 3) & peek_write
                & peek_expected & same_control
                & peek_same_line),
            write_busy.eq(is_write & chain & (peek_trans == 1)),
            must_flush.eq(self.wbuf_valid
                & ~((peek_trans == 3) & peek_write & chain & peek_expected
                    & same_control & peek_same_buffer)
                & ~((peek_trans == 1) & chain)),
            commit_addr.eq(self.base + Cat(Constant(0, 5), wbuf_block)),
        ]
        # DATA samples the beat. CAPTURE then writes one lane of the buffer
        # from these registers, so the 256-bit merge is not a barrel shift.
        hold_data = Signal(bus.data_width)
        hold_strb = Signal(lane_bytes)
        hold_lane = Signal(3)
        hold_same = Signal()
        next_bytes = []
        next_bits = []
        for lane in range(nlanes):
            hit = Signal(name=f"wbuf_lane_hit{lane}")
            same_l = Signal(name=f"wbuf_same{lane}")
            hit.attr.add(("syn_keep", 1))
            hit.attr.add(("syn_maxfan", 8))
            same_l.attr.add(("syn_keep", 1))
            same_l.attr.add(("syn_maxfan", 8))
            self.comb += [hit.eq(hold_lane == lane), same_l.eq(hold_same)]
            for byte in range(lane_bytes):
                index = lane * lane_bytes + byte
                fresh = Mux(hit, hold_data[8 * byte:8 * byte + 8], 0)
                take = hold_strb[byte] & hit
                kept = Mux(take, fresh, wbuf_data[8 * index:8 * index + 8])
                # Nested muxes avoid inverting same_l: a one-bit ``~`` is not
                # a one-bit select. A new line clears every other lane.
                next_bytes.append(Mux(same_l, kept, fresh))
                next_bits.append(Mux(same_l, wbuf_strb[index] | take, take))
        # Fixed lane mux. A 256-bit barrel shift was the long path this avoids.
        lane_hit = []
        for lane in range(nlanes):
            hit = Signal(name=f"rline_lane_hit{lane}")
            hit.attr.add(("syn_keep", 1))
            hit.attr.add(("syn_maxfan", 8))
            self.comb += hit.eq(beat_lane == lane)
            lane_hit.append(hit)

        def lane_word(source):
            width = bus.data_width
            chosen = source[(nlanes - 1) * width:nlanes * width]
            for index in reversed(range(nlanes - 1)):
                sl = source[index * width:(index + 1) * width]
                chosen = Mux(lane_hit[index], sl, chosen)
            if width < 64:
                chosen = Cat(chosen, Constant(0, 64 - width))
            return chosen

        # HREADY is this register, so the hard-macro pin is a flip-flop output.
        # ready_d is the level the *next* state will present. Loading it now
        # makes the pin match that state on arrival: DATA is already low, and
        # EARLY/COMPLETE are already high. Using the current level instead
        # would leave the pin high through DATA and complete the beat early.
        ready_d = Signal()
        ready_q = Signal(reset=1)
        self.sync += ready_q.eq(ready_d)
        # IDLE ends a burst; BUSY is allowed to retain its context. A decoder
        # passes the master's real IDLE indication because slave HTRANS is gated.
        end_burst = end_burst_i if end_burst_i is not None else ((bus.trans == 0) & bus.readyout)
        # BUSY (HTRANS == 1) matches neither term, so chain and the line stay.
        self.sync += [
            If(end_burst, chain.eq(0)),
            If(end_burst | ~ready_i, rline_valid.eq(0)),
            If(~ready_q, self.n_wait_cycles.eq(self.n_wait_cycles + 1)),
        ]

        aligned = Signal()
        in_range = Signal()
        bad = Signal()

        self.comb += [
            aligned.eq((bus.addr & ((1 << bus.size) - 1)) == 0),
            in_range.eq((bus.addr >= self.base) & (bus.addr < self.base + self.size)),
            # sticky records the first error for diagnosis but must not wedge
            # the window: every failed transfer is individually bounded and
            # the next one starts clean.
            bad.eq(~in_range | ~aligned | (bus.size > (bus.data_width // 8).bit_length() - 1)
                   | ~ready_i | self.recovering | self.recovery_failed),
        ]

        hit_event,hit_count_event=Signal(),Signal()
        self.sync += [hit_count_event.eq(hit_event),
                      If(hit_count_event,self.n_rline_hit.eq(self.n_rline_hit+1))]

        def accept(src_trans, src_addr, src_write, src_size, src_burst, src_prot,
                   src_bad, src_in_range, src_aligned, src_sequential, src_next,
                   src_continue, save_data):
            # The only legal hit: this beat continues the open read burst and
            # names the 32-byte block already held. Anything else drops the line.
            read_hit = (~src_write & ~src_bad & ~self.write_fault & src_sequential
                        & rline_valid & _equal(rline_block, (src_addr - self.base)[5:32]))
            return [
                NextValue(self.n_req, self.n_req + 1),
                NextValue(self.n_write, self.n_write + src_write),
                NextValue(self.n_read, self.n_read + ~src_write),
                NextValue(self.last_bad, src_bad),
                NextValue(self.last_in_range, src_in_range),
                NextValue(self.last_aligned, src_aligned),
                NextValue(self.last_write, src_write),
                NextValue(self.last_size, src_size),
                NextValue(self.last_addr, src_addr),
                NextValue(addr, src_addr),
                NextValue(self.last_control, Cat(src_trans, src_burst, src_size, src_prot, src_write)),
                If(src_write,
                    NextValue(self.write_control, Cat(src_trans, src_burst, src_size, src_prot, src_write)),
                ).Else(
                    NextValue(self.read_control, Cat(src_trans, src_burst, src_size, src_prot, src_write)),
                ),
                NextValue(rline_hit, read_hit),
                If(read_hit,
                    hit_event.eq(1),
                ).Else(
                    NextValue(rline_valid, 0),
                ),
                NextValue(expected, src_next),
                NextValue(burst_type, src_burst),
                NextValue(burst_size, src_size),
                NextValue(burst_prot, src_prot),
                NextValue(burst_write, src_write),
                If(src_continue,
                    NextValue(beats_left, beats_left - 1),
                    NextValue(chain, ~src_bad & ((src_burst == 1) | (beats_left > 1))),
                ).Else(
                    NextValue(beats_left, Mux(src_burst <= 3, 3, Mux(src_burst <= 5, 7, 15))),
                    NextValue(chain, ~src_bad & (src_trans == 2) & (src_burst != 0)),
                ),
                NextValue(is_write, src_write),
                NextValue(tsize, src_size),
                NextValue(lifetime, 0),
                If(self.write_fault & ~src_bad,
                    NextValue(self.write_fault, 0),
                    NextValue(saved_wdata, 0),
                    NextState("REJECT1"),
                ).Elif(src_bad,
                    NextValue(self.write_fault, 0),
                    NextValue(self.n_error, self.n_error + 1),
                    NextValue(saved_wdata, 0),
                    NextState("REJECT1"),
                ).Else(
                    # Owned write data is consumed two cycles later, in CAPTURE.
                    NextValue(saved_wdata, save_data),
                    NextState("DATA"),
                ),
            ]

        decode_saved = Signal()

        def accept_bus():
            # Separate phase ownership from validation and burst arithmetic.
            # This also keeps flush/response decisions off the metadata enables.
            return [
                NextValue(owned_trans, bus.trans),
                NextValue(owned_addr, bus.addr),
                NextValue(owned_write, bus.write),
                NextValue(owned_size, bus.size),
                NextValue(owned_burst, bus.burst),
                NextValue(owned_prot, bus.prot),
                NextValue(decode_saved, 0),
                NextState("DECODE"),
            ]

        def accept_owned():
            return [NextValue(decode_saved, owned_write), NextState("DECODE")]

        def capture():
            # The registered decoder presents an accepted transfer for one cycle.
            # Keep its address, control and write data until this adapter can
            # run it; the live bus will not still be offering them.
            return [
                NextValue(owned, 1),
                NextValue(owned_addr, bus.addr),
                NextValue(owned_write, bus.write),
                NextValue(owned_size, bus.size),
                NextValue(owned_burst, bus.burst),
                NextValue(owned_prot, bus.prot),
                NextValue(owned_trans, bus.trans),
                NextValue(owned_wdata, bus.wdata),
            ]

        fsm = FSM(reset_state="ADDRESS")
        self.fsm = fsm
        fsm.act("DECODE",
            *accept(owned_trans, owned_addr, owned_write, owned_size, owned_burst,
                    owned_prot, o_bad, o_in_range, o_aligned, o_sequential, o_next,
                    o_continue, decode_saved),
            ready_d.eq(0),
        )
        pending = Signal()          # a data phase was accepted but never served
        # A decoder can accept the first address while this slave is draining
        # in the background (the preceding transfer belonged to another slave).
        # Its acceptance pulse is authoritative; a directly connected master
        # instead uses this slave's own HREADY address handshake.
        incoming = accepted_i if accepted_i is not None else (bus.trans[1] & bus.readyout)
        wdata64 = Signal(64)
        self.comb += wdata64.eq(bus.wdata)
        drain = Signal(max=max(10, 4 * ui_timeout + 2))
        # A closing beat reports the failure as its own HRESP. An idle flush
        # has already acknowledged its beats, so the next access reports it.
        def note_fault():
            return [
                NextValue(self.wbuf_valid, 0),
                NextValue(chain, 0),
                NextValue(rline_valid, 0),
                NextValue(self.error_sticky, 1),
                NextValue(self.n_error, self.n_error + 1),
                If(retire,
                    NextState("ERROR1"),
                ).Else(
                    NextValue(self.write_fault, 1),
                    NextValue(self.recovering, 1),
                    NextValue(drain, 0),
                    NextState("DRAIN"),
                ),
            ]
        # Pin is ready_q. Each state loads the level the next state will show.
        self.comb += bus.readyout.eq(ready_q)
        # ADDRESS is ready only when it will actually accept. flush of the line
        # decision is the compare from the previous cycle, which is must_flush
        # while that previous state is still running.
        def address_level(owned_next, pending_next):
            return ~pending_next & ~must_flush & ~owned_next
        fsm.act("ADDRESS",
            # A dirty line must reach DRAM before a new transfer is accepted.
            # BUSY and an in-block sequential write keep the buffer open.
            # An owned transfer waits here with HREADY low, then runs from its
            # captured phase rather than the live bus.
            If(must_flush,
               If(bus.trans[1] & ~owned, *capture()),
               NextValue(retire, 0),
               NextState("COMMIT"),
               ready_d.eq(0),
            ).Elif(owned,
               NextValue(owned, 0),
               NextValue(pending, 0),
               *accept_owned(),
               ready_d.eq(0),
            ).Elif(bus.trans[1] | pending,
               NextValue(pending, 0),
               *accept_bus(),
               ready_d.eq(0),
            ).Else(
               ready_d.eq(1),
            ),
        )
        fsm.act("DATA",
            # Address and size are stable. The master presents this beat's
            # HWDATA only after the predecessor acknowledge, so CAPTURE
            # samples the data and WBUF merges it. A read hit already has
            # the line: select its lane and complete on the next cycle.
            NextValue(hold_strb, lane_strb),
            NextValue(hold_lane, beat_lane),
            NextValue(hold_same, same_line),
            If(~is_write & rline_hit,
                NextValue(resp_data, lane_word(rline_data)),
                NextValue(resp_error, 0),
                NextValue(resp_hold, 0),
                NextState("COMPLETE"),
                ready_d.eq(1),
            ).Else(
                NextState("CAPTURE"),
                ready_d.eq(0),
            ),
        )
        fsm.act("CAPTURE",
            NextValue(saved_wdata, 0),
            If(is_write,
                NextValue(hold_data, data_word),
                NextState("WBUF"),
                ready_d.eq(0),
            ).Else(
                req.din.eq(Cat(is_write, tsize, addr, wdata64, Constant(0, 1),
                               Constant(0, 1 + 256 + 32))),
                req.we.eq(1),
                NextValue(retire, 1),
                NextValue(lifetime, lifetime + 1),
                If(req.writable,
                    NextValue(lifetime, 0),
                    NextState("WAIT"),
                    ready_d.eq(0),
                ).Elif(lifetime == response_timeout,
                    NextValue(self.error_sticky, 1),
                    NextValue(self.n_error, self.n_error + 1),
                    NextState("ERROR1"),
                    ready_d.eq(0),
                ).Else(
                    ready_d.eq(0),
                ),
            ),
        )
        # Lane, strobe and write data are all registers. The buffer update is
        # one mux from those registers, then EARLY or COMMIT.
        fsm.act("WBUF",
            NextValue(wbuf_data, Cat(*next_bytes)),
            NextValue(wbuf_strb, Cat(*next_bits)),
            NextValue(wbuf_block, rel[5:32]),
            NextValue(self.wbuf_valid, 1),
            If(write_merge | write_busy,
                NextState("EARLY"),
                ready_d.eq(1),
            ).Else(
                NextValue(retire, 1),
                NextState("COMMIT"),
                ready_d.eq(0),
            ),
        )
        # The line is already complete. One UI write, then retire the beat
        # that closed it, or return to ADDRESS if the beats were early-acked.
        fsm.act("COMMIT",
            # A closing beat (retire) reaches COMPLETE and accepts the next
            # address there. Only an idle flush has no such acceptance point.
            # Every exit is a not-ready state, so the pin stays low.
            # The open read line dies with the write buffer: the next read
            # has to observe DRAM, not the block fetched before this write.
            NextValue(rline_valid, 0),
            ready_d.eq(0),
            If(bus.trans[1] & ~owned & ~retire, *capture()),
            req.din.eq(Cat(Constant(1, 1), Constant(0, 3), commit_addr, Constant(0, 64),
                           Constant(0, 1), Constant(1, 1), wbuf_data, wbuf_strb)),
            req.we.eq(ready_i),
            If(~ready_i,
                *note_fault(),
            ).Elif(req.writable,
                NextValue(self.wbuf_valid, 0),
                NextValue(lifetime, 0),
                NextState("WAIT"),
            ).Else(
                NextValue(lifetime, lifetime + 1),
                If(lifetime == response_timeout, *note_fault()),
            ),
        )
        fsm.act("EARLY",
            bus.resp.eq(0),
            bus.rdata.eq(0),
            If(bus.trans[1],
                *accept_bus(),
                ready_d.eq(0),
            ).Else(
                NextState("ADDRESS"),
                ready_d.eq(address_level(owned, pending)),
            ),
        )
        fsm.act("WAIT",
            If(bus.trans[1] & ~owned & ~retire, *capture()),
            NextValue(lifetime, lifetime + 1),
            If(resp.readable,
                If(resp.dout[256],
                    resp.re.eq(1),
                    *note_fault(),
                    ready_d.eq(0),
                ).Elif(is_write,
                    NextValue(resp_data, 0),
                    NextValue(resp_error, 0),
                    If(retire,
                        NextValue(resp_hold, 1),
                        NextState("COMPLETE"),
                        ready_d.eq(1),
                    ).Else(
                        resp.re.eq(1),
                        NextState("ADDRESS"),
                        # retire is 0 in this branch, so a visible transfer is owned.
                        ready_d.eq(address_level(owned | bus.trans[1], pending)),
                    ),
                ).Else(
                    # Data can be captured even when the line will be invalid.
                    # Keep live HTRANS/end_burst off the 256-bit register enable;
                    # only the validity bit decides whether later beats use it.
                    NextValue(rline_data, resp.dout[:256]),
                    NextValue(rline_block, rel[5:32]),
                    If(ready_i & ~end_burst,
                        NextValue(rline_valid, 1),
                    ).Else(
                        NextValue(rline_valid, 0),
                    ),
                    NextValue(resp_data, lane_word(resp.dout[:256])),
                    NextValue(resp_error, 0),
                    If(retire,
                        NextValue(resp_hold, 1),
                        NextState("COMPLETE"),
                        ready_d.eq(1),
                    ).Else(
                        resp.re.eq(1),
                        NextValue(rline_valid, 0),
                        NextState("ADDRESS"),
                        ready_d.eq(address_level(owned | bus.trans[1], pending)),
                    ),
                ),
            ).Elif(lifetime == response_timeout,
                *note_fault(),
                ready_d.eq(0),
            ).Else(
                ready_d.eq(0),
            ),
        )
        # One completion cycle: HREADY high and data valid. A valid address
        # presented in this cycle is accepted here.
        fsm.act("COMPLETE",
            bus.rdata.eq(resp_data[:bus.data_width]),
            bus.resp.eq(resp_error),
            NextValue(self.error_sticky, self.error_sticky | resp_error),
            # A line hit never pushed a response. Popping here would steal
            # the next miss.
            If(resp_hold, resp.re.eq(1)),
            If(bus.trans[1],
                *accept_bus(),
                ready_d.eq(0),
            ).Else(
                NextState("ADDRESS"),
                ready_d.eq(address_level(owned, pending)),
            ),
        )
        fsm.act("REJECT1",
            bus.resp.eq(1),
            NextValue(rline_valid, 0),
            NextState("REJECT2"),
            ready_d.eq(1),
        )
        fsm.act("REJECT2",
            bus.resp.eq(1),
            If(bus.trans[1],
                *accept_bus(),
                ready_d.eq(0),
            ).Else(
                NextState("ADDRESS"),
                ready_d.eq(address_level(owned, pending)),
            ),
        )
        fsm.act("ERROR1",
            NextValue(chain, 0),
            NextValue(self.wbuf_valid, 0),
            NextValue(rline_valid, 0),
            bus.resp.eq(1),
            NextState("ERROR2"),
            ready_d.eq(1),
        )
        # Recovery after an error: the DDR side may still be working on the
        # aborted command (the UI has no transaction tags), so no new DDR
        # transfer may start until the worst-case response latency has passed.
        # Every response that shows up in that window is consumed here, and
        # DDR accesses meanwhile return ERROR; SRAM traffic is unaffected.
        fsm.act("ERROR2",
            bus.resp.eq(1),
            NextValue(drain, 0),
            NextValue(self.recovering, 1),
            # A valid address presented at this completion edge is accepted by
            # the interconnect; remember whether a data phase will be pending.
            NextValue(pending, bus.trans[1]),
            NextState("DRAIN"),
            ready_d.eq(0),
        )
        # HREADY is held low for the whole recovery: the master cannot
        # complete or present a new data phase, so no address is silently
        # dropped.  The window ends when the UI reports no outstanding DDR
        # transfer (synchronised into the fabric domain) and at least a few
        # cycles have elapsed; a bounded fail-safe declares the window dead.
        fsm.act("DRAIN",
            bus.resp.eq(0),
            NextValue(rline_valid, 0),
            resp.re.eq(resp.readable),
            NextValue(drain, drain + 1),
            If(bus.trans[1] & ~owned, *capture()),
            If(incoming, NextValue(pending, 1)),
            If(drain >= 8,
                If(self.ui_idle_sys,
                    NextValue(self.recovering, 0),
                    NextState("ADDRESS"),
                    ready_d.eq(address_level(owned | bus.trans[1], pending | incoming)),
                ).Elif(drain >= 4 * ui_timeout,
                    NextValue(self.recovery_failed, 1),
                    If(pending | incoming,
                        NextState("DEAD1"),
                        ready_d.eq(0),
                    ).Else(
                        NextState("DEAD"),
                        ready_d.eq(1),
                    ),
                ).Else(
                    ready_d.eq(0),
                ),
            ).Else(
                ready_d.eq(0),
            ),
        )
        # A permanently failed adapter must still let the CPU make progress:
        # every later transfer gets a bounded ERROR response, not a hang.
        fsm.act("DEAD",
            If(bus.trans[1],
                NextState("DEAD1"),
                ready_d.eq(0),
            ).Else(
                ready_d.eq(1),
            ),
        )
        fsm.act("DEAD1",
            bus.resp.eq(1),
            NextState("DEAD2"),
            ready_d.eq(1),
        )
        fsm.act("DEAD2",
            bus.resp.eq(1),
            If(bus.trans[1],
                NextState("DEAD1"),
                ready_d.eq(0),
            ).Else(
                NextState("DEAD"),
                ready_d.eq(1),
            ),
        )

        self.comb += [
            self.debug.eq(Cat(self.n_req[0:12], self.n_read[0:10], self.n_write[0:10])),
            self.debug_hi.eq(Cat(self.n_error[0:8], self.last_bad, self.last_in_range,
                                 self.last_aligned, self.last_write, self.last_size,
                                 self.error_sticky, ready_i)),
        ]

        # UI engine lives in the DDR user clock domain.
        self.submodules.ui = ClockDomainsRenamer({"sys": "ddr"})(
            _UIEngine(ui, req, resp, self.base, ui_timeout,
                      recorder=(self.rec_w, self.rec_w_mask, self.rec_w_data,
                                self.rec_w_n, self.rec_r, self.rec_r_blk,
                                self.rec_r_sel, self.rec_r_n),
                      idle_out=self.ui_idle_ddr, data_width=bus.data_width, pending_out=self.ui_pending_ddr))
        self.specials += MultiReg(self.ui_idle_ddr, self.ui_idle_sys, "sys")


class _UIEngine(LiteXModule):
    """User-interface side of the adapter; must run in the IP ``clk_out`` domain.

    Sampling ``cmd_ready``/``rd_data_valid`` from the fabric clock domain loses
    single-cycle DDR pulses, so this engine is instantiated inside a
    ``ClockDomainsRenamer`` that puts it in the ``ddr`` domain.
    """
    def __init__(self, ui, req, resp, base, ui_timeout, recorder=None, idle_out=None, data_width=64, pending_out=None):
        is_write = Signal()
        uisize = Signal(3)
        uiaddr = Signal(32)
        uidata = Signal(64)
        line = Signal()
        line_data = Signal(256)
        line_mask = Signal(32)
        rd_block = Signal(256)
        counter = Signal(max=ui_timeout + 2)
        self.state = uistate = FSM(reset_state="IDLE")
        if pending_out is not None:
            self.comb += pending_out.eq(uistate.ongoing('SEND'))
        wr_bytes = Signal(32)
        offset = Signal(32)
        # beat index inside the 32-byte transfer, and its bit shift (0/64/128/192)
        lane = Signal(2 if data_width == 64 else 3)
        lane_shift = Signal(9)
        self.comb += [lane.eq(offset[3:5] if data_width == 64 else offset[2:5]),
                      lane_shift.eq(lane * Constant(data_width, 9))]
        # Byte enable within the 32-byte beat: HSIZE selects how many bytes,
        # the low address bits select where.  A Mux lookup avoids a dynamic
        # shift of a dynamic width, which synthesises into a 32-bit truncation.
        ones = Signal(8)
        self.comb += [
            offset.eq(uiaddr - base),
            ones.eq(Mux(uisize == 0, 0x01,
                  Mux(uisize == 1, 0x03,
                  Mux(uisize == 2, 0x0f, 0xff)))),
            # A line write already carries the assembled bytes. A read returns
            # the whole 32-byte block; the memory port selects the beat.
            wr_bytes.eq(Mux(line, line_mask, ones << offset[0:5])),
            ui.burst.eq(0),
            ui.cmd.eq(Mux(is_write, 0, 1)),
            ui.wr_data.eq(Mux(line, line_data, uidata << lane_shift)),
            # BL8 burst: the IP command address selects the 32-byte block,
            # the low word bits select the lane inside the returned block.
            ui.addr.eq(offset[2:31] & ~Constant(7, 29)),
        ]
        (rec_w, rec_w_mask, rec_w_data, rec_w_n, rec_r,
         rec_r_blk, rec_r_sel, rec_r_n) = recorder or (None,) * 8
        if rec_w is not None:
            # Capture the *handshake* values from the registered request fields
            # (ui.wr_data_mask is only valid while SEND is active, so derive it
            # from the registered byte address and size instead).
            self.sync += [
                If(ui.cmd_en & ui.cmd_ready & (ui.cmd == 0),
                   rec_w.eq(Cat(ui.cmd, ui.addr)),
                   rec_w_mask.eq(~wr_bytes),
                   rec_w_data.eq(ui.wr_data[0:64]),
                   rec_w_n.eq(rec_w_n + 1),
                ),
                If(ui.cmd_en & ui.cmd_ready & (ui.cmd != 0),
                   rec_r.eq(Cat(ui.cmd, ui.addr)),
                ),
                If(ui.rd_data_valid,
                   rec_r_blk.eq(ui.rd_data),
                   # First-beat lane only. The external status window publishes
                   # the low 32 bits of this beat at the existing offset.
                   rec_r_sel.eq((ui.rd_data >> lane_shift)[0:64]),
                   rec_r_n.eq(rec_r_n + 1),
                ),
            ]
        write_accept = Signal()
        self.comb += write_accept.eq(is_write & ui.cmd_ready & ui.wr_data_rdy
                                     & uistate.ongoing("SEND"))

        self.outstanding = outstanding = Signal(4)
        uistate.act("IDLE",
            If(req.readable & (outstanding == 0),
                NextValue(is_write, req.dout[0]),
                NextValue(uisize, req.dout[1:4]),
                NextValue(uiaddr, req.dout[4:36]),
                NextValue(uidata, req.dout[36:100]),
                NextValue(line, req.dout[_REQ_LINE]),
                NextValue(line_data, req.dout[_REQ_LINE_DATA]),
                NextValue(line_mask, req.dout[_REQ_LINE_MASK]),
                NextValue(counter, 0),
                NextState("SEND"),
            ),
        )
        cmd_fire = Signal()
        self.comb += cmd_fire.eq(ui.cmd_en & ui.cmd_ready)
        self.sync += If(cmd_fire, outstanding.eq(outstanding + 1))
        if idle_out is not None:
            self.comb += idle_out.eq(uistate.ongoing("IDLE") & ~getattr(req,"queued",req.readable) & (outstanding == 0))
        uistate.act("SEND",
            ui.cmd_en.eq(Mux(is_write, write_accept, 1)),
            ui.wr_data_mask.eq(~wr_bytes),
            ui.wr_data_en.eq(write_accept),
            ui.wr_data_end.eq(write_accept),
            If(is_write,
                If(~write_accept,
                    If(counter == ui_timeout, NextState("FAIL")).Else(NextValue(counter, counter + 1)),
                ).Else(NextState("WPUSH")),
            ).Else(
                If(~ui.cmd_ready,
                    If(counter == ui_timeout, NextState("FAIL")).Else(NextValue(counter, counter + 1)),
                ).Else(
                    NextValue(counter, 0),
                    NextState("WAITDATA"),
                ),
            ),
        )
        uistate.act("WPUSH",
            resp.din.eq(Cat(Constant(0, 256), Constant(0, 1))),
            resp.we.eq(1),
            If(resp.writable,
                NextValue(outstanding, outstanding - 1),
                NextState("DONE"),
            ),
        )
        uistate.act("WAITDATA",
            If(ui.rd_data_valid,
                NextValue(rd_block, ui.rd_data),
                NextState("PUSH"),
            ).Elif(counter == ui_timeout,
                NextState("FAIL"),
            ).Else(
                NextValue(counter, counter + 1),
            ),
        )
        uistate.act("PUSH",
            resp.din.eq(Cat(rd_block, Constant(0, 1))),
            resp.we.eq(1),
            If(resp.writable,
                NextValue(outstanding, outstanding - 1),
                NextState("DONE"),
            ),
        )
        uistate.act("FAIL",
            resp.din.eq(Cat(Constant(0, 256), Constant(1, 1))),
            resp.we.eq(1),
            If(resp.writable,
                # Saturating: a timeout that happened before the command
                # handshake must not wrap the counter (that wedged recovery).
                # Once a read command has been accepted, the untagged IP may
                # return it at any later time. Keep ownership until that data
                # arrives; never let it become a subsequent request's response.
                If(outstanding != 0,
                    NextState("QUARANTINE"),
                ).Else(NextState("DONE")),
            ),
        )
        uistate.act("QUARANTINE",
            If(ui.rd_data_valid,
                NextValue(outstanding, 0),
                NextState("DONE"),
            ),
        )
        uistate.act("DONE",
            req.re.eq(1),
            NextState("IDLE"),
        )
