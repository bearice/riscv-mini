"""Registered request/reply: stalled read/write, byte enables, error, cancellation."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from gateware.bus import WishbonePipeline

def main():
    up=wishbone.Interface();down=wishbone.Interface();dut=WishbonePipeline(up,down)
    requests=[]
    @passive
    def slave():
        while True:
            if (yield down.cyc) and (yield down.stb):
                item=[]
                for name in ('adr','we','dat_w','sel'):item.append((yield getattr(down,name)))
                requests.append(tuple(item))
                for _ in range(7):
                    for i,name in enumerate(('adr','we','dat_w','sel')):assert (yield getattr(down,name))==item[i]
                    assert (yield down.cyc) and (yield down.stb)
                    yield
                yield down.dat_r.eq(item[0]^0x12345678)
                yield down.err.eq(item[0]==0xee);yield down.ack.eq(item[0]!=0xee);yield
                yield down.ack.eq(0);yield down.err.eq(0);yield
            yield
    @passive
    def deadline():
        for _ in range(1000):yield
        raise AssertionError('Wishbone pipeline stalled')
    def bench():
        yield up.sel.eq(15)
        value=yield from up.read(0x10);assert value==0x12345668
        yield from up.write(0x20,0xaabbccdd,sel=5)
        assert requests[:2]==[(0x10,0,0,15),(0x20,1,0xaabbccdd,5)]
        yield up.adr.eq(0xee);yield up.cyc.eq(1);yield up.stb.eq(1);yield
        while not (yield up.err):
            assert not (yield up.ack);yield
        yield up.cyc.eq(0);yield up.stb.eq(0);yield;yield
        assert not (yield up.err)
        yield up.adr.eq(0x40);yield up.cyc.eq(1);yield up.stb.eq(1);yield;yield;yield
        yield up.cyc.eq(0);yield up.stb.eq(0);yield;yield
        yield up.adr.eq(0x44);yield up.cyc.eq(1);yield up.stb.eq(1)
        while not (yield up.ack):yield
        assert (yield up.dat_r)==(0x44^0x12345678)
        yield up.cyc.eq(0);yield up.stb.eq(0);yield
        assert [r[0] for r in requests]==[0x10,0x20,0xee,0x40,0x44]
    run_simulation(dut,[bench(),slave(),deadline()])
    print('Wishbone pipeline PASS: delayed transfers, byte enables, errors, abandoned reply')
if __name__=='__main__':main()
