"""Dirty eviction, full-word byte merge, framebuffer bypass and disable clean."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation,passive
from migen import ResetInserter
from litex.soc.interconnect import wishbone
from gateware.l2_writeback import WritebackL2

def main():
    m=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    s=wishbone.Interface(data_width=128,address_width=32,addressing='word')
    dut=ResetInserter()(WritebackL2(m,s,size=256))
    # In a SoC the CSR bank owns the CSRStorage device-write logic.
    dut._flush.finalize(32,'big');dut.submodules.flush_csr=dut._flush
    memory={};writes=[]
    def initial(a):return sum(((a*4+i)^0x12340000)<<(32*i) for i in range(4))
    @passive
    def backend():
        while True:
            yield
            if not ((yield s.cyc) and (yield s.stb)):continue
            a=(yield s.adr);wr=(yield s.we);data=(yield s.dat_w);sel=(yield s.sel)
            for _ in range(3):yield
            if wr:
                old=memory.get(a,initial(a))
                for b in range(16):
                    if sel & (1<<b):old=(old & ~(255<<(b*8))) | (data & (255<<(b*8)))
                memory[a]=old;writes.append((a,sel))
            yield s.dat_r.eq(memory.get(a,initial(a)));yield s.ack.eq(1)
            yield;yield s.ack.eq(0)
    def access(a,wr=0,data=0,sel=15):
        yield m.adr.eq(a);yield m.we.eq(wr);yield m.dat_w.eq(data);yield m.sel.eq(sel)
        yield m.cyc.eq(1);yield m.stb.eq(1)
        for _ in range(500):
            yield
            if (yield m.ack):break
        else:raise AssertionError('writeback stalled')
        value=(yield m.dat_r);yield m.cyc.eq(0);yield m.stb.eq(0);yield
        return value
    def idle_clean():
        for _ in range(2000):
            yield
            if not (yield dut._busy.status):return
        raise AssertionError(('clean stalled',(yield dut.fsm.state),(yield dut.cache.fsm.state),
                              (yield dut._flush.storage),(yield dut._flush.we),(yield s.cyc),(yield s.adr),
                              (yield dut.cache.master.cyc),(yield dut.cache.master.stb),(yield dut.cache.master.adr),(yield dut.cache.master.ack)))
    def checks():
        base=0x40000000>>2
        for _ in range(4):yield
        assert (yield from access(base+1))==(base+1)^0x12340000
        yield from access(base+1,1,0x11223344,5)
        expected=(((base+1)^0x12340000)&0xff00ff00) | 0x00220044
        assert (yield from access(base+1))==expected
        assert not writes,'dirty hit must not immediately write DDR'
        # Same cache index, different tag forces a full-line writeback.
        yield from access(base+64)
        assert ((memory[base//4]>>32)&0xffffffff)==expected
        assert writes[-1]==(base//4,65535)
        # Framebuffer writes bypass the writeback cache and retain byte enables.
        fb=0x47e00000>>2
        yield from access(fb+2,1,0xaabbccdd,15)
        assert writes[-1]==(fb//4,0x0f00)
        assert (yield from access(fb+2))==0xaabbccdd
        yield from access(base,1,0x98765432)
        yield dut._flush.storage.eq(1);yield
        yield from idle_clean()
        assert memory[base//4]&0xffffffff==0x98765432
        yield from access(base+3,1,0xdeadbeef)
        yield dut._enable.storage.eq(0);yield
        yield from idle_clean()
        assert (memory[base//4]>>96)&0xffffffff==0xdeadbeef
        assert (yield from access(base+3))==0xdeadbeef
        yield dut._enable.storage.eq(1);yield
        assert (yield from access(base+3))==0xdeadbeef
        memory[base//4]=(memory[base//4]&~0xffffffff)|0x12345678
        yield dut.reset.eq(1);yield;yield dut.reset.eq(0)
        for _ in range(20):yield
        assert (yield from access(base))==0x12345678,'reset must invalidate persistent RAM tags'
        print('LiteX writeback PASS: full-word merge, dirty eviction, framebuffer bypass, explicit/disable clean and reset')
    run_simulation(dut,[checks(),backend()])
if __name__=='__main__':main()
