"""Small shared read cache with write-through, write-invalidate semantics.

All DDR writers must use the upstream Wishbone bus. The native video port is
read-only. A write is acknowledged only after the DDR bridge accepts it;
there are no dirty lines or additional software cache-maintenance operations.
RAM writes are full 128-bit fills, avoiding Gowin byte-bank fragmentation.
"""
from migen import Signal, Memory, Cat, Replicate, If, Case, FSM, NextState, NextValue, Mux
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStorage, CSRStatus


class ReadL2(LiteXModule):
    def __init__(self, master, slave, size=4096, bypass_base=0x47e00000, bursting=False,prefetch=False,refill_bypass=False):
        if size < 256 or size & (size-1):
            raise ValueError('L2 size must be a power of two, at least 256 bytes')
        if len(master.dat_r)!=32 or len(slave.dat_r)!=128:
            raise ValueError('L2 requires 32-bit upstream and 128-bit downstream')
        if master.addressing!='word' or slave.addressing!='word':
            raise ValueError('L2 requires word-addressed Wishbone')
        self._enable=CSRStorage(1, reset=1, name='enable')
        self._stats=CSRStatus(32, name='stats')
        lines=size//16
        index_bits=(lines-1).bit_length()
        tag_bits=len(master.adr)-2-index_bits
        data=Memory(128, lines, name='l2_data')
        tags=Memory(tag_bits+1, lines, name='l2_tags')
        dp=data.get_port(write_capable=True)
        tp=tags.get_port(write_capable=True)
        self.specials += data,tags,dp,tp
        address=Signal.like(master.adr)
        write_data=Signal(32)
        select=Signal(4)
        write=Signal()
        cacheable=Signal()
        aborted=Signal()
        sweep=Signal(index_bits)
        hits=Signal(16)
        misses=Signal(16)
        lane=address[:2]
        if bursting:
            lane=Signal(2)
        self.comb += self._stats.status.eq(Cat(hits,misses))
        self.fsm=fsm=FSM(reset_state='INIT')
        finish=[If(bursting & cacheable & ~aborted & (master.cti==2),NextState('BURST')).Else(NextState('IDLE'))] if bursting else [NextState('IDLE')]
        early=[master.ack.eq(master.cyc & ~aborted),
               Case(address[:2], {i:master.dat_r.eq(slave.dat_r[32*i:32*(i+1)]) for i in range(4)}),
               If(bursting & cacheable & ~aborted & (master.cti==2),NextState('BURST_WAIT')).Else(NextState('IDLE')) if bursting else NextState('IDLE')]
        self.comb += [
            dp.adr.eq(Mux(fsm.ongoing('IDLE'),master.adr[2:2+index_bits],address[2:2+index_bits]) if prefetch else address[2:2+index_bits]),
            tp.adr.eq(Mux(fsm.ongoing('IDLE'),master.adr[2:2+index_bits],address[2:2+index_bits]) if prefetch else address[2:2+index_bits]),
            dp.dat_w.eq(slave.dat_r),
            tp.dat_w.eq(Cat(address[2+index_bits:],1)),
            slave.adr.eq(address[2:]), slave.we.eq(write),
            slave.dat_w.eq(Replicate(write_data,4)),
            Case(address[:2], {i: slave.sel.eq(select << (4*i)) for i in range(4)}),
            Case(lane, {i: master.dat_r.eq(dp.dat_r[32*i:32*(i+1)]) for i in range(4)}),
        ]
        if bursting:self.comb += lane.eq(Mux(fsm.ongoing('BURST'),master.adr[:2],address[:2]))
        self.sync += If(~master.cyc,aborted.eq(1))
        fsm.act('INIT',
            tp.adr.eq(sweep),tp.dat_w.eq(0),tp.we.eq(1),
            If(sweep==lines-1,NextState('IDLE')).Else(NextValue(sweep,sweep+1)))
        fsm.act('IDLE',
            If(master.cyc & master.stb,
                NextValue(address,master.adr),NextValue(write_data,master.dat_w),
                NextValue(select,master.sel),NextValue(write,master.we),NextValue(aborted,0),
                NextValue(cacheable,self._enable.storage & (master.adr < (bypass_base >> 2))),
                NextState('LOOKUP' if prefetch else 'RAM')))
        # Address was registered on the preceding edge. Give synchronous RAM
        # another edge before comparing its tag and selecting a word.
        fsm.act('RAM',NextState('LOOKUP'))
        fsm.act('LOOKUP',
            If(write,
                tp.dat_w.eq(0),tp.we.eq(1),NextState('MEMORY')
            ).Elif(cacheable & tp.dat_r[tag_bits] & (tp.dat_r[:tag_bits]==address[2+index_bits:]),
                master.ack.eq(master.cyc & ~aborted),
                NextValue(hits,hits+1),
                *([If(bursting & (master.cti==2),NextState('BURST')).Else(NextState('IDLE'))] if bursting else [NextState('IDLE')])
            ).Else(
                NextValue(misses,misses+1),NextState('MEMORY')))
        fsm.act('MEMORY',
            slave.cyc.eq(1),slave.stb.eq(1),
            If(~write,slave.sel.eq(0xffff)),
            If(slave.ack | slave.err,
                If(slave.err,
                    master.err.eq(master.cyc & ~aborted),NextState('IDLE')
                ).Elif(write,
                    master.ack.eq(master.cyc & ~aborted),NextState('IDLE')
                ).Else(
                    tp.we.eq(1),
                    If(~cacheable,tp.dat_w.eq(0)),
                    # Respond through the registered RAM on the next cycle,
                    # also for uncached reads; no wide combinational bypass.
                    dp.we.eq(1),
                    *([If(refill_bypass,*early).Else(NextState('REFILL_WAIT'))] if refill_bypass else [NextState('REFILL_WAIT')]))))
        fsm.act('REFILL_WAIT',NextState('RETURN'))
        fsm.act('RETURN',
            master.ack.eq(master.cyc & ~aborted),
            *finish)
        # After the early reply, let synchronous RAM expose the newly filled
        # word before accepting another lane (also safe without a pipeline).
        if bursting:
            if refill_bypass:fsm.act('BURST_WAIT',NextState('BURST'))
            fsm.act('BURST',
                If(~master.cyc,NextState('IDLE')).Elif(master.stb,
                  If(master.we | (master.adr[2:]!=address[2:]),NextState('IDLE')).Else(
                    master.ack.eq(1),NextValue(hits,hits+1),
                    If(master.cti!=2,NextState('IDLE')))))
