"""Bounded SD writer bursts: terminal commit, input starvation and disable drain."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module
from migen.sim import run_simulation,passive
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.sd_dma import SDWriter
from gateware.bus import WishbonePipeline
from gateware.l2 import ReadL2


def check(starve=False,abort=False):
    upstream=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    ram=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    native=LiteDRAMNativePort('both',23,128)
    dut=Module();dut.submodules.writer=w=SDWriter(upstream,burst_write=True)
    dut.submodules.pipe=WishbonePipeline(upstream,ram,burst_read=True,burst_write=True,early_launch=True)
    dut.submodules.cache=ReadL2(ram,native,size=256,native=True,base_address=0x40000000,
        bursting=True,refill_bypass=True,prefetch=True,fast_write=True,combine_writes=True)
    memory={};commits=[]
    @passive
    def backend():
        while True:
            yield
            if not (yield native.cmd.valid):continue
            assert (yield native.cmd.we)
            address=(yield native.cmd.addr)
            for _ in range(3):yield
            yield native.cmd.ready.eq(1);yield;yield native.cmd.ready.eq(0)
            while not (yield native.wdata.valid):yield
            data=(yield native.wdata.data);mask=(yield native.wdata.we)
            for _ in range(3):yield
            for i in range(4):
                if mask & (15<<(i*4)):memory[address*4+i]=(data>>(32*i))&0xffffffff
            commits.append((address,mask))
            yield native.wdata.ready.eq(1);yield;yield native.wdata.ready.eq(0)
    def bench():
        yield w._base.storage.eq(0x40000000);yield w._length.storage.eq(64)
        yield w._enable.storage.eq(1)
        sent=0;paused=0;released=False
        for cycles in range(5000):
            blocked=(starve or abort) and sent>=2 and paused<350
            yield w.sink.valid.eq(not blocked)
            yield w.sink.data.eq(0x01020300+sent)
            if abort and sent>=2:yield w._enable.storage.eq(0)
            yield
            if (yield w.sink.valid) and (yield w.sink.ready):sent+=1
            if blocked:
                paused+=1
                if paused>300 and not (yield upstream.cyc):released=True
            if abort and paused>=350:break
            if (yield w._done.status):break
        else:raise AssertionError('SD burst stalled')
        if abort:
            assert memory=={0:0x00030201,1:0x01030201},memory
            assert released and not (yield upstream.cyc)
        else:
            assert memory=={i:((i<<24)|0x00030201) for i in range(16)},memory
            if starve:assert released,'input starvation retained bus ownership'
            else:assert len(commits)==4 and all(mask==65535 for _,mask in commits),commits
        print('SD burst PASS',dict(starve=starve,abort=abort,native_writes=len(commits)))
    run_simulation(dut,[bench(),backend()])


check();check(starve=True);check(abort=True)
