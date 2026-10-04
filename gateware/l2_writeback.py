"""Experimental LiteX write-back L2 with full-word RAM updates and video bypass.

Flush evicts every index via a reserved read window. The window is not user
storage. Upstream transactions must retain ownership until ACK (LiteX Cache
does not implement cancellation). This backend is not the accepted default.
"""
from migen import Signal, Memory, Cat, Replicate, If, Mux, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStorage, CSRStatus


class WritebackL2(LiteXModule):
    def __init__(self, master, slave, size=4096, bypass_base=0x47e00000):
        self._enable=CSRStorage(1,reset=1,name='enable')
        self._stats=CSRStatus(32,name='stats')
        self._flush=CSRStorage(1,write_from_dev=True,name='flush')
        self._busy=CSRStatus(1,name='busy')
        cm=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        cs=wishbone.Interface(data_width=128,address_width=32,addressing='word')
        self.cache=cache=wishbone.Cache(size//4,cm,cs,reverse=False,
                                      with_bursting=True,with_refill_bypass=False)
        # Byte-enabled 128-bit RAM fragments into sixteen banks on Gowin.
        # The synchronous old word is valid in Cache.TEST_HIT; merge selected
        # bytes and write the entire word, keeping only four wide RAM banks.
        mem=next(s for s in cache._fragment.specials if isinstance(s,Memory) and s.width==128)
        old=mem.ports[0];mem.ports.remove(old);cache._fragment.specials.remove(old)
        port=mem.get_port(write_capable=True)
        cache.specials += port
        self.comb += [port.adr.eq(old.adr),old.dat_r.eq(port.dat_r),port.we.eq(old.we!=0),
                     port.dat_w.eq(Cat(*[Mux(old.we[i],old.dat_w[8*i:8*(i+1)],port.dat_r[8*i:8*(i+1)]) for i in range(16)]))]
        # BSRAM survives a soft reset. Main RAM tags are nonzero; sweeping
        # tags/dirty to zero prevents old cache contents surviving DDR reboot.
        tag_mem=next(s for s in cache._fragment.specials if isinstance(s,Memory) and s is not mem)
        tag_port=tag_mem.ports[0];sweep=Signal((size//16-1).bit_length())
        cache.fsm.reset_state='INIT'
        cache.fsm.act('INIT',tag_port.adr.eq(sweep),tag_port.dat_w.eq(0),tag_port.we.eq(1),
                      If(sweep==size//16-1,NextState('IDLE')).Else(NextValue(sweep,sweep+1)))
        active=Signal(reset=1);bypass=Signal();flushing=Signal()
        index=Signal((size//16-1).bit_length());hits=Signal(16);misses=Signal(16);refilled=Signal()
        self.fsm=fsm=FSM(reset_state='IDLE')
        self.comb += [flushing.eq(~fsm.ongoing('IDLE')),
                     self._busy.status.eq(flushing | self._flush.storage | (active & ~self._enable.storage)),
                     bypass.eq(~active | (master.adr >= (bypass_base>>2))),
                     self._stats.status.eq(Cat(hits,misses)),
                     cm.adr.eq(master.adr),cm.dat_w.eq(master.dat_w),cm.sel.eq(master.sel),cm.we.eq(master.we),
                     cm.cti.eq(master.cti),cm.bte.eq(master.bte),
                     cm.cyc.eq(master.cyc & ~bypass & ~flushing & ~self._busy.status),
                     cm.stb.eq(master.stb & ~bypass & ~flushing & ~self._busy.status),
                     slave.adr.eq(Mux(bypass & ~flushing,master.adr[2:],cs.adr)),
                     slave.we.eq(Mux(bypass & ~flushing,master.we,cs.we)),
                     slave.dat_w.eq(Mux(bypass & ~flushing,Replicate(master.dat_w,4),cs.dat_w)),
                     slave.sel.eq(Mux(bypass & ~flushing,master.sel << (master.adr[:2]*4),cs.sel)),
                     slave.cyc.eq(Mux(bypass & ~flushing,master.cyc & ~self._busy.status,cs.cyc)),
                     slave.stb.eq(Mux(bypass & ~flushing,master.stb & ~self._busy.status,cs.stb)),
                     cs.ack.eq(slave.ack & (~bypass | flushing)),cs.dat_r.eq(slave.dat_r),
                     master.ack.eq(Mux(bypass,slave.ack,cm.ack) & ~self._busy.status),
                     master.err.eq(slave.err & bypass & ~self._busy.status),
                     master.dat_r.eq(Mux(bypass,slave.dat_r >> (master.adr[:2]*32),cm.dat_r)),
                     self._flush.dat_w.eq(0)]
        self.sync += [If(~flushing & cache.fsm.ongoing('REFILL'),refilled.eq(1)),
                      If(master.cyc & master.stb & master.ack & ~master.we,
                         If(bypass | refilled,misses.eq(misses+1)).Else(hits.eq(hits+1)),refilled.eq(0))]
        fsm.act('IDLE',
            If(self._flush.storage | (active & ~self._enable.storage),
               NextValue(index,0),NextValue(refilled,0),NextState('FLUSH')),
            If(~active & self._enable.storage,NextValue(active,1)))
        fsm.act('FLUSH',cm.adr.eq((0x47d00000>>2)+index*4),cm.we.eq(0),cm.cyc.eq(1),cm.stb.eq(1),cm.cti.eq(0),
                If(cm.ack,If(index==size//16-1,NextState('DONE')).Else(NextValue(index,index+1),NextState('GAP'))))
        fsm.act('GAP',NextState('FLUSH'))
        fsm.act('DONE',self._flush.we.eq(1),NextValue(active,self._enable.storage),NextState('IDLE'))
