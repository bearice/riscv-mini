"""Small shared read cache with write-through, write-invalidate semantics.

All DDR writers must use the upstream Wishbone bus. The native video port is
read-only. A write is acknowledged only after the DDR bridge accepts it;
there are no dirty lines or additional software cache-maintenance operations.
RAM writes are full 128-bit fills, avoiding Gowin byte-bank fragmentation.
The optional native backend incorporates the DDR command/data handshake into
the cache FSM; the default backend uses a 128-bit Wishbone bridge.
"""
from migen import Signal, Memory, Cat, Replicate, If, Case, FSM, NextState, NextValue, Mux
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus


class ReadL2(LiteXModule):
    def __init__(self, master, slave, size=4096, bypass_base=0x47e00000, bursting=False,prefetch=False,refill_bypass=False,maintenance=False,native=False,base_address=0x40000000,fast_write=False,combine_writes=False,posted_writes=False,atomic=0):
        self.drain = Signal()
        self.idle = Signal()
        if combine_writes or posted_writes:
            if not native:raise ValueError('Write combining requires native L2')
            from gateware.write_combine import NativeWriteCombiner
            post = posted_writes & ~atomic & (master.cti == 0) & self._postable(master, bypass_base)
            self.write_combiner=NativeWriteCombiner(slave,(master.cti==2) | post,~master.cyc,
                                                   posted=posted_writes,burst_active=master.cti==2)
            slave=self.write_combiner.port
        if size < 256 or size & (size-1):
            raise ValueError('L2 size must be a power of two, at least 256 bytes')
        read_data=slave.rdata.data if native else slave.dat_r
        if len(master.dat_r)!=32 or len(read_data)!=128:
            raise ValueError('L2 requires 32-bit upstream and 128-bit downstream')
        if master.addressing!='word' or (not native and slave.addressing!='word'):
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
        if combine_writes or posted_writes:
            combiner=self.write_combiner
            self.comb += [combiner.drain.eq(self.drain | (master.cyc & master.stb & ~master.we)),
                          self.idle.eq(combiner.idle & fsm.ongoing('IDLE'))]
            wait_drain=combiner.pending & master.cyc & master.stb & ~master.we
        else:
            self.comb += self.idle.eq(fsm.ongoing('IDLE'))
            wait_drain=0
        invalidate=Signal()
        if maintenance:
            self._invalidate=CSR(name='invalidate')
            self._busy=CSRStatus(name='busy')
            self.sync += If(self._invalidate.re,invalidate.eq(1))
            self.comb += self._busy.status.eq(invalidate | fsm.ongoing('INIT'))
        finish=[If(bursting & cacheable & ~aborted & (master.cti==2),NextState('BURST')).Else(NextState('IDLE'))] if bursting else [NextState('IDLE')]
        early=[master.ack.eq(master.cyc & ~aborted),
               Case(address[:2], {i:master.dat_r.eq(read_data[32*i:32*(i+1)]) for i in range(4)}),
               If(bursting & cacheable & ~aborted & (master.cti==2),NextState('BURST_WAIT')).Else(NextState('IDLE')) if bursting else NextState('IDLE')]
        self.comb += [
            dp.adr.eq(Mux(fsm.ongoing('IDLE'),master.adr[2:2+index_bits],address[2:2+index_bits]) if prefetch else address[2:2+index_bits]),
            tp.adr.eq(Mux(fsm.ongoing('IDLE'),master.adr[2:2+index_bits],address[2:2+index_bits]) if prefetch else address[2:2+index_bits]),
            dp.dat_w.eq(read_data),
            tp.dat_w.eq(Cat(address[2+index_bits:],1)),
            Case(lane, {i: master.dat_r.eq(dp.dat_r[32*i:32*(i+1)]) for i in range(4)}),
        ]
        if native:
            self.comb += [
                slave.cmd.addr.eq(address[2:]-(base_address>>4)),
                slave.cmd.we.eq(write),slave.cmd.last.eq(~write),
                slave.flush.eq(~master.cyc),
                slave.wdata.data.eq(Replicate(write_data,4)),
                Case(address[:2],{i:slave.wdata.we.eq(select << (4*i)) for i in range(4)}),
            ]
        else:
            self.comb += [slave.adr.eq(address[2:]),slave.we.eq(write),
                slave.dat_w.eq(Replicate(write_data,4)),
                Case(address[:2],{i:slave.sel.eq(select << (4*i)) for i in range(4)})]
        if bursting:self.comb += lane.eq(Mux(fsm.ongoing('BURST'),master.adr[:2],address[:2]))
        self.sync += If(~master.cyc,aborted.eq(1))
        fsm.act('INIT',
            tp.adr.eq(sweep),tp.dat_w.eq(0),tp.we.eq(1),
            If(sweep==lines-1,NextState('IDLE')).Else(NextValue(sweep,sweep+1)))
        fsm.act('IDLE',
            If(invalidate,NextValue(sweep,0),NextValue(invalidate,0),NextState('INIT')).Elif(master.cyc & master.stb & ~wait_drain,
                NextValue(address,master.adr),NextValue(write_data,master.dat_w),
                NextValue(select,master.sel),NextValue(write,master.we),NextValue(aborted,0),
                NextValue(cacheable,self._enable.storage & (master.adr < (bypass_base >> 2))),
                If(master.we,NextState('WRITE_INVALIDATE')).Else(NextState('LOOKUP' if prefetch else 'RAM')) if fast_write else NextState('LOOKUP' if prefetch else 'RAM')))
        if fast_write:
            # Address now drives synchronous tag RAM. Stores need no tag read.
            fsm.act('WRITE_INVALIDATE',If(write,
                tp.dat_w.eq(0),tp.we.eq(1),NextState('MEMORY'))
                .Else(NextState('LOOKUP' if prefetch else 'RAM')))
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
        fill=[tp.we.eq(1),If(~cacheable,tp.dat_w.eq(0)),dp.we.eq(1),
              *([If(refill_bypass,*early).Else(NextState('REFILL_WAIT'))] if refill_bypass else [NextState('REFILL_WAIT')])]
        if native:
            # Keep ownership after acceptance, even if the upstream master
            # cancels. Drain the response without acknowledging a new request.
            fsm.act('MEMORY',slave.cmd.valid.eq(1),
                If(slave.cmd.ready,If(write,NextState('NATIVE_WRITE')).Else(NextState('NATIVE_READ'))))
            fsm.act('NATIVE_WRITE',slave.wdata.valid.eq(1),
                If(slave.wdata.ready,master.ack.eq(master.cyc & ~aborted),NextState('IDLE')))
            fsm.act('NATIVE_READ',slave.rdata.ready.eq(1),If(slave.rdata.valid,*fill))
        else:
            fsm.act('MEMORY',
                slave.cyc.eq(1),slave.stb.eq(1),
                If(~write,slave.sel.eq(0xffff)),
                If(slave.ack | slave.err,
                    If(slave.err,
                        master.err.eq(master.cyc & ~aborted),NextState('IDLE')
                    ).Elif(write,
                        master.ack.eq(master.cyc & ~aborted),NextState('IDLE')
                    ).Else(*fill)))
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

    @staticmethod
    def _postable(master, bypass_base):
        return master.adr < (bypass_base >> 2)
