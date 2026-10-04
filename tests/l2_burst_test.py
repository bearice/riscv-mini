"""Burst lane changes and line crossings must not reuse stale RAM outputs."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation,passive
from migen import Module
from litex.soc.interconnect import wishbone
from gateware.l2 import ReadL2
from gateware.bus import WishbonePipeline

def main():
    m=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    s=wishbone.Interface(data_width=128,address_width=32,addressing='word')
    if '--pipeline' in sys.argv:
        internal=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        dut=Module();dut.submodules.cache=ReadL2(internal,s,size=256,bursting=True,refill_bypass='--refill-bypass' in sys.argv)
        dut.submodules.pipeline=WishbonePipeline(m,internal,burst_read=True)
    else:dut=ReadL2(m,s,size=256,bursting=True,refill_bypass='--refill-bypass' in sys.argv)
    transactions=[]
    @passive
    def memory():
        while True:
            yield
            if not ((yield s.cyc) and (yield s.stb)):continue
            a=(yield s.adr);transactions.append(a)
            for _ in range(3):yield
            yield s.dat_r.eq(sum((0x12340000+a*4+i)<<(32*i) for i in range(4)))
            yield s.ack.eq(1);yield;yield s.ack.eq(0)
    def burst(first,count):
        yield m.cyc.eq(1);yield m.stb.eq(1);yield m.sel.eq(15)
        for i in range(count):
            yield m.adr.eq(first+i);yield m.cti.eq(7 if i==count-1 else 2)
            for _ in range(50):
                yield
                if (yield m.ack):break
            else:raise AssertionError('burst stalled')
            assert (yield m.dat_r)==0x12340000+first+i,(first+i,(yield m.dat_r))
        yield m.cyc.eq(0);yield m.stb.eq(0);yield
    def checks():
        for _ in range(20):yield
        yield from burst(0,8)
        assert transactions==[0,1],transactions
        yield from burst(1,6)
        assert transactions==[0,1],transactions
        yield from burst(64,8)
        yield from burst(0,8)
        assert transactions==[0,1,16,17,0,1],transactions
        assert (yield from m.read(5))==0x12340005
        assert (yield from m.read(2))==0x12340002
        print('L2 burst PASS: consecutive lanes, END, nonzero start, line crossing and conflict')
    run_simulation(dut,[checks(),memory()])
if __name__=='__main__':main()
