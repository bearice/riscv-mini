"""Pre-DDR stack isolation, byte writes, dirty handoff and reset, using real L2."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module, Signal, ResetInserter
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.shared_l2 import SharedL2


def exercise(size):
    dut=Module();ready=Signal();wb=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    port=LiteDRAMNativePort('both',23,128)
    dut.submodules.cache=cache=ResetInserter()(SharedL2(wb,port,size=size,enabled=ready,boot_ram=True))
    ram={};traffic=[]
    @passive
    def memory():
        write=None;read=None
        while True:
            if (yield port.cmd.valid):
                assert (yield ready),'DDR request before training'
                a=(yield port.cmd.addr);traffic.append(a)
                if (yield port.cmd.we):write=a
                else:read=a
            if (yield port.wdata.valid):
                assert write is not None
                ram[write]=(yield port.wdata.data);write=None
            yield port.cmd.ready.eq(1);yield port.wdata.ready.eq(1)
            yield port.rdata.valid.eq(read is not None)
            if read is not None:
                yield port.rdata.data.eq(ram.get(read,0))
                if (yield port.rdata.ready):read=None
            yield
    def cpu(address,data=None,sel=15):
        yield wb.adr.eq(address>>2);yield wb.dat_w.eq(data or 0);yield wb.sel.eq(sel)
        yield wb.we.eq(data is not None);yield wb.cyc.eq(1);yield wb.stb.eq(1)
        for _ in range(2000):
            yield
            if (yield wb.ack):result=(yield wb.dat_r);break
        else:raise AssertionError(('CPU timeout',hex(address)))
        yield wb.cyc.eq(0);yield wb.stb.eq(0);yield;yield
        return result
    def drive():
        for generation in range(2):
            yield ready.eq(0)
            for _ in range(size//16+8):yield
            # DMA cannot steal pinned stack lines or reach DDR during boot.
            yield cache.video.cmd.valid.eq(1);yield cache.video.cmd.addr.eq(0)
            yield cache.dma.cmd.valid.eq(1);yield cache.dma.cmd.addr.eq(0)
            for i in range(64):
                a=0x407ff000+i*64
                assert (yield from cpu(a))==0
                yield from cpu(a,0x87654321+i)
                yield from cpu(a,0x0000aa00,2)
                assert (yield from cpu(a))==((0x87654321+i)&~0xff00)|0xaa00
                assert not (yield cache.video.cmd.ready) and not (yield cache.dma.cmd.ready)
            assert not traffic
            yield cache.video.cmd.valid.eq(0);yield cache.dma.cmd.valid.eq(0)
            # Out-of-window requests stall instead of allocating/refilling.
            yield wb.adr.eq(0x40800000>>2);yield wb.cyc.eq(1);yield wb.stb.eq(1)
            for _ in range(30):
                yield;assert not (yield wb.ack) and not (yield port.cmd.valid)
            yield wb.cyc.eq(0);yield wb.stb.eq(0);yield;yield
            yield ready.eq(1);yield;yield
            for i in range(64):
                a=0x407ff000+i*64;expected=((0x87654321+i)&~0xff00)|0xaa00
                assert (yield from cpu(a))==expected,'handoff lost stack'
                yield from cpu(a+size,0x12345678)
                assert ram[(a-0x40000000)//16]&0xffffffff==expected,'dirty boot line not preserved'
                assert (yield from cpu(a))==expected,'DDR readback after eviction failed'
            yield cache.reset.eq(1);yield ready.eq(0)
            for _ in range(4):yield
            yield cache.reset.eq(0);traffic.clear()
    run_simulation(dut,[drive(),memory()])

for size in (4096,8192):exercise(size)
print('Boot RAM isolation / byte lanes / dirty handoff / reset PASS (4/8 KiB)')
