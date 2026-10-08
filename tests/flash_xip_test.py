import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module, Record, Signal
from migen.sim import run_simulation, passive
from litex.soc.cores.spi import SPIMaster
from gateware.flash_xip import FlashXIP, XIP_OFFSET

dut=Module()
pads=Record(SPIMaster.pads_layout)
sw=Record(SPIMaster.pads_layout)
dut.submodules.xip=xip=FlashXIP(pads,sw,wake_cycles=8)
wb=xip.bus
transactions=[]
@passive
def flash():
    prev_clk=0;prev_cs=1;bits=[];response=0
    while True:
        cs=(yield pads.cs_n);clk=(yield pads.clk)
        if prev_cs and not cs:bits=[];response=0
        if not cs and not prev_clk and clk:
            bits.append((yield pads.mosi))
            if len(bits)==32:
                command=0
                for b in bits:command=(command<<1)|b
                if command>>24==3:
                    address=command&0xffffff
                    assert address%4==0
                    response=int.from_bytes(bytes(((address+i)*7+3)&255 for i in range(4)),'big')
        if not cs and prev_clk and not clk:
            count=len(bits)
            if 32<=count<64:yield pads.miso.eq((response>>(63-count))&1)
        if not prev_cs and cs:transactions.append(bits)
        prev_clk=clk;prev_cs=cs
        yield

def bench():
    yield sw.cs_n.eq(1)
    for _ in range(100):yield
    for address in (XIP_OFFSET,XIP_OFFSET+4,XIP_OFFSET+8,0x200030,0x3ffffc,0):
        yield wb.adr.eq(address//4);yield wb.cyc.eq(1);yield wb.stb.eq(1)
        for _ in range(1000):
            yield
            if (yield wb.ack):break
        else:raise AssertionError('no ack')
        expected=int.from_bytes(bytes(((address+i)*7+3)&255 for i in range(4)),'little')
        assert (yield wb.dat_r)==expected,(address,hex((yield wb.dat_r)),hex(expected))
        yield wb.stb.eq(0);yield wb.cyc.eq(0);yield;yield
    assert len(transactions[0])==8 and int(''.join(map(str,transactions[0])),2)==0xab
    assert all(len(t)==64 for t in transactions[1:])
    # Writes never reach the NOR.
    count=len(transactions)
    yield wb.we.eq(1);yield wb.cyc.eq(1);yield wb.stb.eq(1);yield;yield
    assert (yield wb.err) and not (yield wb.ack)
    yield wb.stb.eq(0);yield wb.cyc.eq(0);yield wb.we.eq(0);yield
    assert len(transactions)==count
    # Disable is a bus error, never a stalled bus or a NOR transaction.
    yield xip._enable.storage.eq(0)
    yield wb.cyc.eq(1);yield wb.stb.eq(1);yield;yield
    assert (yield wb.err) and not (yield wb.ack)
    assert len(transactions)==count
    yield wb.cyc.eq(0);yield wb.stb.eq(0)
    yield xip._enable.storage.eq(1);yield
    # An existing manual software-CS transaction keeps the pads until release.
    yield sw.cs_n.eq(0);yield sw.mosi.eq(1);yield
    yield wb.cyc.eq(1);yield wb.stb.eq(1)
    for _ in range(20):
        yield
        assert not (yield wb.ack) and (yield pads.mosi)==1
    yield sw.cs_n.eq(1)
    for _ in range(1000):
        yield
        if (yield wb.ack):break
    else:raise AssertionError('no ack after software CS release')
    yield wb.stb.eq(0);yield wb.cyc.eq(0);yield
    print('Flash XIP SPI waveform, endian, writes, disable and ownership PASS')
run_simulation(dut,[bench(),flash()])
