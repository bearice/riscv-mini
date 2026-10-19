"""AHB-Lite interconnect used by the AE350 data port.

One master, several slaves with disjoint windows.  The single definition of
"this address phase was accepted" is

    accept = master.trans[1] & hit & master.readyout

and it is used for *everything*: capturing the address, direction and size,
selecting the slave, and advancing the data phase.  A slave only sees HTRANS
asserted while its own accepted transfer is in progress, and the response mux
is driven by the slave captured for the transfer currently in its data phase.

The shared bus is held (HREADY low) while the selected slave works, so the
master cannot start a second transaction, and a valid next address presented in
the completion cycle is accepted there: no extra hold cycle is required.
"""
from functools import reduce
from operator import or_

from migen import *
from litex.gen import LiteXModule


class MemInterconnect(LiteXModule):
    def __init__(self, master, slaves, registered=False):
        """slaves: list of (base, size, bus) with disjoint windows."""
        self.master = master
        self.slaves = slaves
        nslaves = len(slaves)
        assert nslaves >= 1
        self.sel = Signal(nslaves)          # one-hot, valid while DATA
        self.accepted = Signal()            # pulses on every accepted address
        self.n_accepted = Signal(32)
        self.n_slave = [Signal(32) for _ in range(nslaves)]

        addr_lat = self.addr_lat = Signal(32)
        write_lat = self.write_lat = Signal()
        size_lat = self.size_lat = Signal(3)
        burst_lat = Signal(3)
        prot_lat = Signal(4)
        trans_lat = Signal(2)
        sel_lat = self.sel_lat = Signal(nslaves)

        # Priority decode: the first matching window owns the address, so a
        # catch-all error slave can be placed last.
        hit = Signal(nslaves)
        for i, (base, size, bus) in enumerate(slaves):
            self.comb += hit[i].eq((master.addr >= base) & (master.addr < base + size))
        sel_hit = self.sel_hit = Signal(nslaves)
        for i in range(nslaves):
            if i == 0:
                self.comb += sel_hit[i].eq(hit[i])
            else:
                previous = reduce(or_, [hit[j] for j in range(i)])
                self.comb += sel_hit[i].eq(hit[i] & ~previous)

        state = self.state = Signal()       # 0 = IDLE, 1 = DATA
        dispatched = Signal()
        dispatch = Signal()
        self.comb += dispatch.eq(state & ~dispatched)
        ready = Signal()
        self.comb += [
            # The data phase always belongs to the slave captured at acceptance;
            # the live address only affects the *next* acceptance.
            self.sel.eq(sel_lat),
            master.readyout.eq(Mux(state, ready & (dispatched if registered else 1), 1)),
        ]
        accept_now = Signal()
        self.comb += accept_now.eq(master.trans[1] & reduce(or_, [sel_hit[i] for i in range(nslaves)])
                                   & master.readyout)
        # ready of the selected slave, for the data phase
        self.comb += ready.eq(reduce(or_, [Mux(sel_lat[i], slaves[i][2].readyout, 0)
                                          for i in range(nslaves)]))
        self.comb += [
            master.rdata.eq(reduce(or_, [Mux(sel_lat[i], slaves[i][2].rdata, 0)
                                         for i in range(nslaves)])),
            master.resp.eq(reduce(or_, [Mux(sel_lat[i], slaves[i][2].resp, 0)
                                        for i in range(nslaves)])),
        ]
        # Slave-side wiring: the accepted address is held for the whole data
        # phase; during an acceptance cycle the live address is presented.
        slave_addr = Signal(32)
        self.comb += slave_addr.eq(addr_lat if registered else Mux(accept_now, master.addr, addr_lat))
        for i, (base, size, bus) in enumerate(slaves):
            self.comb += [
                bus.addr.eq(slave_addr),
                bus.write.eq(write_lat if registered else Mux(accept_now, master.write, write_lat)),
                bus.size.eq(size_lat if registered else Mux(accept_now, master.size, size_lat)),
                bus.burst.eq(burst_lat if registered else master.burst),
                bus.prot.eq(prot_lat if registered else master.prot),
                bus.wdata.eq(master.wdata),
                # HTRANS only carries meaning in the address phase: a slave
                # sees it for exactly the cycle in which its address is
                # accepted, never during its data phase.
                bus.trans.eq(Mux(dispatch & sel_lat[i], trans_lat, 0) if registered else
                             Mux(accept_now & sel_hit[i], master.trans, Constant(0, 2))),
                bus.sel.eq(Mux(master.readyout, sel_hit[i], sel_lat[i])),
            ]
        self.sync += [
            If(accept_now,
               addr_lat.eq(master.addr),
               write_lat.eq(master.write),
               size_lat.eq(master.size),
               burst_lat.eq(master.burst), prot_lat.eq(master.prot), trans_lat.eq(master.trans),
               sel_lat.eq(sel_hit),
               self.accepted.eq(1),
               self.n_accepted.eq(self.n_accepted + 1),
            ).Else(
               self.accepted.eq(0),
            ),
            If(state == 0,
               If(accept_now, state.eq(1)),
            ).Else(
               # Completion cycle: a valid next address keeps the interconnect
               # in DATA so that its own data phase starts immediately.
               If(accept_now,
                  state.eq(1),
               ).Elif(master.readyout,
                  state.eq(0),
               ),
            ),
        ]
        if registered:
            self.sync += If(accept_now, dispatched.eq(0)).Elif(dispatch, dispatched.eq(1))
        for i in range(nslaves):
            self.sync += If(accept_now & sel_hit[i], self.n_slave[i].eq(self.n_slave[i] + 1))


class ErrorSlave(LiteXModule):
    """Default slave: two-cycle ERROR response for addresses no window claims."""
    def __init__(self, bus):
        self.bus = bus
        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm
        fsm.act("IDLE",
            bus.readyout.eq(1),
            If(bus.trans[1], NextState("ERR1")),
        )
        fsm.act("ERR1",
            bus.readyout.eq(0),
            bus.resp.eq(1),
            NextState("ERR2"),
        )
        fsm.act("ERR2",
            bus.readyout.eq(1),
            bus.resp.eq(1),
            If(bus.trans[1], NextState("ERR1")).Else(NextState("IDLE")),
        )
