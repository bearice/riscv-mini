"""Unselected candidates must never become owned or corrupt a completion."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import Module
from migen.sim import run_simulation
from litex.soc.interconnect.ahb import AHBInterface
from gateware.machines.mega138k_ae350.cpu import AHBMemory
from gateware.machines.mega138k_ae350.interconnect import MemInterconnect,ErrorSlave
from test_ae350_interconnect import xact

m=Module()
master,a,b,error=[AHBInterface(data_width=32,address_width=32) for _ in range(4)]
m.submodules.a=AHBMemory(a,16,base=0x1000,init=[0x11111111,0x22222222])
m.submodules.b=AHBMemory(b,16,base=0x2000,init=[0x55555555,0x66666666])
m.submodules.error=ErrorSlave(error)
m.submodules.ic=ic=MemInterconnect(master,[(0x1000,16,a),(0x2000,16,b),(0,1<<32,error)],
                                  registered=True,require_sel=True)
def bench():
    yield
    yield [master.addr.eq(0x2000),master.trans.eq(2),master.sel.eq(0)]
    for _ in range(8):
        yield
        assert not (yield a.trans) and not (yield b.trans) and not (yield error.trans)
    assert (yield ic.n_accepted)==0
    yield [master.addr.eq(0x1000),master.trans.eq(2),master.sel.eq(1),master.size.eq(2)]
    yield
    yield [master.addr.eq(0x2000),master.sel.eq(0),master.trans.eq(2)]
    yield
    for _ in range(32):
        if (yield master.readyout):break
        yield
    else:raise AssertionError('completion timeout')
    assert (yield master.rdata)==0x11111111 and not (yield master.resp)
    yield
    yield master.trans.eq(0)
    for _ in range(4):yield
    assert (yield ic.n_accepted)==1
    assert not (yield b.trans) and not (yield error.trans)
    response,data=yield from xact(master,False,0x2004,size=2,sel=1)
    assert response==0 and data==0x66666666
    yield
    assert (yield ic.n_accepted)==2
    assert (yield ic.n_slave[0])==1 and (yield ic.n_slave[1])==1
    print('PASS unselected candidate ignored in idle/completion; response ownership and later selection preserved')
run_simulation(m,bench())
