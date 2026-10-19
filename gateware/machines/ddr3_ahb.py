"""Gowin DDR3 user interface and AHB-Lite adapter for the AE350 data port.

The vendor IP exposes a 256-bit, 32-byte single-beat user interface clocked by
its own ``clk_out`` (100 MHz at 400 MHz CK).  The AE350 data-memory port is a
64-bit AHB-Lite master clocked by the configured fabric ``sys`` clock.  Two asynchronous
FIFOs cross between the domains; every AHB beat becomes exactly one 32-byte
DRAM transfer (with byte masks for sub-word writes), so reads always observe
DRAM and no invisible adapter cache can hide a maintenance bug.

Contracts (verified against the vendor reference):
  * ``ui.addr`` counts 4-byte words, so ``addr = (cpu_byte - base) >> 2``.
  * one command transfers 32 bytes; ``wr_data_mask`` bit = 1 masks a byte.
  * ``ui.cmd`` 0 = write, 1 = read.
  * request payload: write, size, byte address, wdata;
    response payload: rdata in bits [0:64], error in bit 64.
"""
from migen import *
from migen.genlib.fifo import AsyncFIFO
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule

REQ_WIDTH = 1 + 3 + 32 + 64
RESP_WIDTH = 64 + 1


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


class DDR3AHB(LiteXModule):
    """AHB-Lite slave that turns each 64-bit beat into one 32-byte DDR transfer.

    ``ready_i`` is the DDR calibration status; while low every access returns a
    two-cycle ERROR without touching the IP.  Out-of-range and unaligned accesses also return ERROR.  Every wait state is bounded by
    ``response_timeout`` (fabric cycles) so a stuck IP cannot hang the CPU.
    """
    def __init__(self, bus, ui, ready_i, base=0x2000, size=1 << 30,
                 response_timeout=200_000, ui_timeout=1 << 18, fifo_cls=None,
                 accepted_i=None):
        assert bus.data_width in (32, 64)
        self.bus = bus
        self.base = base
        self.size = size
        self.error_sticky = Signal()
        self.recovering = Signal()
        self.recovery_failed = Signal()
        self.ui_idle_ddr = Signal()
        self.ui_idle_sys = Signal()
        self.busy = Signal()
        # Diagnostics read through the ROM status window by the CPU JTAG.
        self.n_req = Signal(16)
        self.n_read = Signal(16)
        self.n_write = Signal(16)
        self.n_error = Signal(16)
        self.last_bad = Signal()
        self.last_in_range = Signal()
        self.last_aligned = Signal()
        self.last_write = Signal()
        self.last_size = Signal(3)
        self.last_addr = Signal(32)
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
        req = ClockDomainsRenamer({"write": "sys", "read": "ddr"})(fifo_cls(REQ_WIDTH, 8))
        resp = ClockDomainsRenamer({"write": "ddr", "read": "sys"})(fifo_cls(RESP_WIDTH, 8))
        self.submodules += req, resp

        # ----
        # AHB.
        # ----
        addr = Signal(32)
        is_write = Signal()
        tsize = Signal(3)
        lifetime = Signal(32)
        resp_data = Signal(64)
        resp_error = Signal()

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

        def accept():
            return [
                NextValue(self.n_req, self.n_req + 1),
                NextValue(self.n_write, self.n_write + bus.write),
                NextValue(self.n_read, self.n_read + ~bus.write),
                NextValue(self.last_bad, bad),
                NextValue(self.last_in_range, in_range),
                NextValue(self.last_aligned, aligned),
                NextValue(self.last_write, bus.write),
                NextValue(self.last_size, bus.size),
                NextValue(self.last_addr, bus.addr),
                NextValue(addr, bus.addr),
                NextValue(is_write, bus.write),
                NextValue(tsize, bus.size),
                NextValue(lifetime, 0),
                If(bad,
                    NextValue(self.n_error, self.n_error + 1),
                    NextState("REJECT1"),
                ).Else(
                    NextState("DATA"),
                ),
            ]

        fsm = FSM(reset_state="ADDRESS")
        self.fsm = fsm
        pending = Signal()          # a data phase was accepted but never served
        # A decoder can accept the first address while this slave is draining
        # in the background (the preceding transfer belonged to another slave).
        # Its acceptance pulse is authoritative; a directly connected master
        # instead uses this slave's own HREADY address handshake.
        incoming = accepted_i if accepted_i is not None else (bus.trans[1] & bus.readyout)
        fsm.act("ADDRESS",
            bus.readyout.eq(~pending),
            # A normal address phase, or the data phase of a transfer the
            # interconnect accepted at the error completion edge (``pending``):
            # both capture the held address, only the handshake differs.
            If(bus.trans[1] | pending,
               NextValue(pending, 0),
               *accept(),
            ),
        )
        fsm.act("DATA",
            bus.readyout.eq(0),
            NextState("CAPTURE"),
        )
        fsm.act("CAPTURE",
            bus.readyout.eq(0),
            req.din.eq(Cat(is_write, tsize, addr, bus.wdata)),
            req.we.eq(1),
            NextValue(lifetime, lifetime + 1),
            If(req.writable,
                NextValue(lifetime, 0),
                NextState("WAIT"),
            ).Elif(lifetime == response_timeout,
                NextValue(self.error_sticky, 1),
                NextValue(self.n_error, self.n_error + 1),
                NextState("ERROR1"),
            ),
        )
        fsm.act("WAIT",
            bus.readyout.eq(0),
            NextValue(lifetime, lifetime + 1),
            If(resp.readable,
                NextValue(resp_data, resp.dout[0:64]),
                NextValue(resp_error, resp.dout[64]),
                If(resp.dout[64],
                    resp.re.eq(1),
                    NextValue(self.error_sticky, 1),
                    NextValue(self.n_error, self.n_error + 1),
                    NextState("ERROR1"),
                ).Else(NextState("COMPLETE")),
            ).Elif(lifetime == response_timeout,
                NextValue(self.error_sticky, 1),
                NextValue(self.n_error, self.n_error + 1),
                NextState("ERROR1"),
            ),
        )
        # One completion cycle: HREADY high and data valid.  A valid address
        # presented in this cycle is accepted here (standard AHB pipelining);
        # no extra cycle is imposed on the master.
        fsm.act("COMPLETE",
            bus.readyout.eq(1),
            bus.rdata.eq(resp_data[:bus.data_width]),
            bus.resp.eq(resp_error),
            NextValue(self.error_sticky, self.error_sticky | resp_error),
            resp.re.eq(1),
            If(bus.trans[1], *accept()).Else(NextState("ADDRESS")),
        )
        fsm.act("REJECT1",
            bus.readyout.eq(0),
            bus.resp.eq(1),
            NextState("REJECT2"),
        )
        fsm.act("REJECT2",
            bus.readyout.eq(1),
            bus.resp.eq(1),
            If(bus.trans[1], *accept()).Else(NextState("ADDRESS")),
        )
        fsm.act("ERROR1",
            bus.readyout.eq(0),
            bus.resp.eq(1),
            NextState("ERROR2"),
        )
        # Recovery after an error: the DDR side may still be working on the
        # aborted command (the UI has no transaction tags), so no new DDR
        # transfer may start until the worst-case response latency has passed.
        # Every response that shows up in that window is consumed here, and
        # DDR accesses meanwhile return ERROR; SRAM traffic is unaffected.
        drain = Signal(32)
        fsm.act("ERROR2",
            bus.readyout.eq(1),
            bus.resp.eq(1),
            NextValue(drain, 0),
            NextValue(self.recovering, 1),
            # A valid address presented at this completion edge is accepted by
            # the interconnect; remember whether a data phase will be pending.
            NextValue(pending, bus.trans[1]),
            NextState("DRAIN"),
        )
        # HREADY is held low for the whole recovery: the master cannot
        # complete or present a new data phase, so no address is silently
        # dropped.  The window ends when the UI reports no outstanding DDR
        # transfer (synchronised into the fabric domain) and at least a few
        # cycles have elapsed; a bounded fail-safe declares the window dead.
        fsm.act("DRAIN",
            bus.readyout.eq(0),
            bus.resp.eq(0),
            resp.re.eq(resp.readable),
            NextValue(drain, drain + 1),
            If(incoming, NextValue(pending, 1)),
            If(drain >= 8,
                If(self.ui_idle_sys,
                    NextValue(self.recovering, 0),
                    NextState("ADDRESS"),
                ).Elif(drain >= 4 * ui_timeout,
                    NextValue(self.recovery_failed, 1),
                    If(pending | incoming, NextState("DEAD1")).Else(NextState("DEAD")),
                ),
            ),
        )
        # A permanently failed adapter must still let the CPU make progress:
        # every later transfer gets a bounded ERROR response, not a hang.
        fsm.act("DEAD",
            bus.readyout.eq(1),
            If(bus.trans[1], NextState("DEAD1")),
        )
        fsm.act("DEAD1",
            bus.readyout.eq(0), bus.resp.eq(1),
            NextState("DEAD2"),
        )
        fsm.act("DEAD2",
            bus.readyout.eq(1), bus.resp.eq(1),
            If(bus.trans[1], NextState("DEAD1")).Else(NextState("DEAD")),
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
                      idle_out=self.ui_idle_ddr, data_width=bus.data_width))
        self.specials += MultiReg(self.ui_idle_ddr, self.ui_idle_sys, "sys")


class _UIEngine(LiteXModule):
    """User-interface side of the adapter; must run in the IP ``clk_out`` domain.

    Sampling ``cmd_ready``/``rd_data_valid`` from the fabric clock domain loses
    single-cycle DDR pulses, so this engine is instantiated inside a
    ``ClockDomainsRenamer`` that puts it in the ``ddr`` domain.
    """
    def __init__(self, ui, req, resp, base, ui_timeout, recorder=None, idle_out=None, data_width=64):
        is_write = Signal()
        uisize = Signal(3)
        uiaddr = Signal(32)
        uidata = Signal(64)
        rdata = Signal(64)
        counter = Signal(24)
        self.state = uistate = FSM(reset_state="IDLE")
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
            wr_bytes.eq(ones << offset[0:5]),
            ui.burst.eq(0),
            ui.cmd.eq(Mux(is_write, 0, 1)),
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
                NextValue(counter, 0),
                NextState("SEND"),
            ),
        )
        cmd_fire = Signal()
        self.comb += cmd_fire.eq(ui.cmd_en & ui.cmd_ready)
        self.sync += If(cmd_fire, outstanding.eq(outstanding + 1))
        if idle_out is not None:
            self.comb += idle_out.eq(uistate.ongoing("IDLE") & ~req.readable & (outstanding == 0))
        uistate.act("SEND",
            ui.cmd_en.eq(Mux(is_write, write_accept, 1)),
            ui.wr_data.eq(uidata << lane_shift),
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
            resp.din.eq(Cat(Constant(0, 64), Constant(0, 1))),
            resp.we.eq(1),
            If(resp.writable,
                NextValue(outstanding, outstanding - 1),
                NextState("DONE"),
            ),
        )
        uistate.act("WAITDATA",
            If(ui.rd_data_valid,
                NextValue(rdata, (ui.rd_data >> lane_shift)[0:64]),
                NextState("PUSH"),
            ).Elif(counter == ui_timeout,
                NextState("FAIL"),
            ).Else(
                NextValue(counter, counter + 1),
            ),
        )
        uistate.act("PUSH",
            resp.din.eq(Cat(rdata, Constant(0, 1))),
            resp.we.eq(1),
            If(resp.writable,
                NextValue(outstanding, outstanding - 1),
                NextState("DONE"),
            ),
        )
        uistate.act("FAIL",
            resp.din.eq(Cat(Constant(0, 64), Constant(1, 1))),
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
