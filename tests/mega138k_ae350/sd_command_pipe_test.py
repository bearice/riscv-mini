"""Real LiteSDCard PHY/Core: buffered CMD0 retains command bits and CRC7."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from unittest.mock import patch
from migen import *
from migen.sim import run_simulation,passive
from migen.fhdl.specials import Memory
from litesdcard.phy import SDPHY
from gateware.machines.mega138k_ae350.peripherals.sd import FabricSD
pads=Record([('clk',1),('cmd',1),('data',4),('cd',1),('cmd_t',1),('cmd_o',1),('cmd_i',1),('dat_t',4),('dat_o',4),('dat_i',4)])
with patch('gateware.machines.mega138k_ae350.peripherals.sd.SDPHY',side_effect=lambda ignored,device,hz,**kw:SDPHY(pads,device,hz,**kw)):
    d=FabricSD(pads,'GW5AST-138B',100000000,1<<30)
bits=[]
@passive
def watch():
    previous=0
    while True:
        clk=(yield pads.clk)
        if clk and not previous:bits.append((yield pads.cmd_i))
        previous=clk
        yield
def write(off,data):
    yield [d.off.eq(off),d.wdata.eq(data),d.write.eq(1),d.selected.eq(1)]
    yield
    assert (yield d.ok)
    yield d.commit.eq(1)
    yield
    yield d.commit.eq(0)
    yield
    yield d.selected.eq(0)
    yield
def bench():
    yield [pads.cmd_t.eq(1),pads.dat_t.eq(15)]
    yield from write(0x44,10)
    yield from write(0x84,0)
    yield from write(0x80,0)
    yield from write(0x88,1)
    for _ in range(1000):yield
    packet=''.join(str(b) for b in bits)
    expected=f'{0x400000000095:048b}'
    assert expected in packet,(packet,expected)
    assert len(bits)>=56,len(bits)
    bits.clear()
    yield from write(0x80,0x1aa)
    yield from write(0x84,(8<<8)|5)
    yield from write(0x88,1)
    for _ in range(1000):yield
    assert f'{0x48000001aa87:048b}' in ''.join(str(b) for b in bits)
fragment=d.get_fragment()
for memory in fragment.specials:
    if isinstance(memory,Memory):
        for port in memory.ports:
            if port.dat_r is None:port.dat_r=Signal(memory.width)
run_simulation(fragment,[bench(),watch()])
print('PASS buffered command reaches real native PHY as CMD0/CMD8 with CRC7=0x95/0x87 and trailing clocks')
