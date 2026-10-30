"""MDIO management writes are owned by valid APB commits."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350.peripherals.ethernet import EthernetManagement
m=EthernetManagement(50000000)
def bench():
    assert (yield m.reset_n)==0
    yield m.selected.eq(1);yield m.write.eq(1);yield m.off.eq(4);yield m.wdata.eq(7)
    yield;yield
    assert (yield m.reset_n)==0
    yield m.commit.eq(1);yield;yield
    assert (yield m.reset_n)==1 and (yield m.mdc)==1 and (yield m.mdio_low)==1
    yield m.wdata.eq(8);yield;yield
    assert (yield m.ok)==0 and (yield m.reset_n)==1
    yield m.commit.eq(0);yield m.write.eq(0);yield m.off.eq(12);yield;yield
    assert (yield m.rdata)==25000000
    samples=[]
    for _ in range(8):samples.append((yield m.refclk));yield
    assert all(a!=b for a,b in zip(samples,samples[1:])),samples
run_simulation(m,bench())
print('PASS Ethernet reset, MDIO commit validation and 25MHz reference divider')
