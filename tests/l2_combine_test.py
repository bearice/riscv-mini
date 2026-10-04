"""Native burst write masks, commit-before-final-ACK, ordering and abandonment."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module, Signal
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.l2 import ReadL2
from gateware.bus import WishbonePipeline


def main():
    fast='--fast' in sys.argv
    posted='--posted' in sys.argv
    atomic=Signal()
    m=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    p=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    native=LiteDRAMNativePort('both',23,128)
    dut=Module()
    dut.submodules.cache=ReadL2(p,native,size=256,native=True,base_address=0,
        bursting=True,refill_bypass=True,prefetch=fast,fast_write=fast,combine_writes=True,
        posted_writes=posted,atomic=atomic)
    dut.submodules.pipe=WishbonePipeline(m,p,burst_read=True,burst_write=True,early_launch=fast)
    words={i:0xa5a5a5a5 for i in range(128)}
    commits=[]
    @passive
    def backend():
        while True:
            yield
            if not (yield native.cmd.valid):continue
            address=(yield native.cmd.addr);write=(yield native.cmd.we)
            for _ in range(4):
                yield
                assert (yield native.cmd.valid) and (yield native.cmd.addr)==address
            yield native.cmd.ready.eq(1);yield;yield native.cmd.ready.eq(0)
            if write:
                while not (yield native.wdata.valid):yield
                value=(yield native.wdata.data);mask=(yield native.wdata.we)
                for _ in range(5):
                    yield
                    assert (yield native.wdata.data)==value and (yield native.wdata.we)==mask
                for byte in range(16):
                    if mask & (1<<byte):
                        i=address*4+byte//4;shift=(byte%4)*8
                        words[i]=(words[i]&~(255<<shift))|(((value>>(byte*8))&255)<<shift)
                commits.append((address,mask))
                yield native.wdata.ready.eq(1);yield;yield native.wdata.ready.eq(0)
            else:
                yield native.rdata.data.eq(sum(words[address*4+i]<<(32*i) for i in range(4)))
                yield native.rdata.valid.eq(1)
                while not (yield native.rdata.ready):yield
                yield;yield native.rdata.valid.eq(0)
    def beat(address,value,mask,cti):
        yield m.cyc.eq(1);yield m.stb.eq(1);yield m.we.eq(1)
        yield m.adr.eq(address);yield m.dat_w.eq(value);yield m.sel.eq(mask);yield m.cti.eq(cti)
        for _ in range(150):
            yield
            if (yield m.ack):break
        else:raise AssertionError('write timeout')
    def idle():
        yield m.cyc.eq(0);yield m.stb.eq(0);yield m.we.eq(0);yield m.cti.eq(0)
        for _ in range(6):yield
    def checks():
        for _ in range(22):yield
        if posted:
            for i in range(4):
                yield from beat(i,0x11223300+i,15,0)
                yield from idle()
            assert not commits, 'independent stores were not combined'
            yield dut.cache.drain.eq(1)
            for _ in range(150):
                yield
                if (yield dut.cache.idle):break
            else:raise AssertionError('fence drain timeout')
            assert commits==[(0,65535)],commits
            assert words[3]==0x11223303, 'fence completed before visibility'
            yield dut.cache.drain.eq(0)
            yield from beat(0,0x000000ee,1,0)
            yield from idle()
            # A read drains even if this L2 index would otherwise hit.
            yield m.cyc.eq(1);yield m.stb.eq(1);yield m.we.eq(0);yield m.adr.eq(0)
            for _ in range(150):
                yield
                if (yield m.ack):break
            else:raise AssertionError('ordered read timeout')
            assert (yield m.dat_r)==0x112233ee
            yield from idle()
            # Atomic store must be committed before its bus ACK.
            yield atomic.eq(1)
            yield from beat(8,0xabcdef01,15,0)
            assert words[8]==0xabcdef01
            yield atomic.eq(0);yield from idle()
            # Autonomous drain guarantees a lone posted write eventually reaches DDR.
            yield from beat(12,0xfedcba98,15,0)
            yield from idle()
            for _ in range(80):yield
            assert words[12]==0xfedcba98
            assert commits==[(0,65535),(0,1),(2,15),(3,15)],commits
            print('Posted stores PASS: independent merge, fence visibility, read ordering, non-posted atomic, bounded idle drain')
            return
        for i in range(8):
            yield from beat(i,0x12340000+i,15,7 if i==7 else 2)
        assert words[7]==0x12340007, 'final ACK preceded commit'
        assert commits==[(0,65535),(1,65535)],commits
        yield from idle()
        yield from beat(0,0x000000ee,1,2)
        yield from beat(0,0x0000dd00,2,7)
        assert words[0]==0x1234ddee
        yield from idle()
        # Classic writes have no delayed visibility after their ACK.
        yield from beat(9,0x11223344,15,0)
        assert words[9]==0x11223344
        yield from idle()
        # Abandon a non-final burst. Its accepted prefix must drain exactly once.
        yield from beat(16,0xfedcba98,15,2)
        yield from idle()
        for _ in range(40):yield
        assert words[16]==0xfedcba98
        # A read in place of a final write drains the preceding partial word.
        yield from beat(20,0xfacebeef,15,2)
        yield m.we.eq(0);yield m.adr.eq(20);yield m.cti.eq(7)
        for _ in range(150):
            yield
            if (yield m.ack):break
        else:raise AssertionError('read timeout')
        assert (yield m.dat_r)==0xfacebeef
        yield from idle()
        assert commits==[(0,65535),(1,65535),(0,3),(2,240),(4,15),(5,15)],commits
        print('L2 combining PASS: full/partial masks, overwrite, crossing, classic completion, cancellation and read ordering')
    run_simulation(dut,[checks(),backend()])


if __name__=='__main__':main()
