"""SDK Flash FR bridge: address translation, waits, exactly-once and rejection."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation,passive
from gateware.machines.mega138k_ae350.flash_apb import FlashRegisterAdapter

p=Record([('addr',32),('wdata',32),('rdata',32),('select',1),('enable',1),('write',1),('ready',1)])
d=FlashRegisterAdapter(p)
events=[]
@passive
def native():
    count=0
    last=None
    while True:
        if (yield p.select):
            values=((yield p.addr),(yield p.wdata),(yield p.write))
            if last is not None:assert values==last,'APB fields changed while waiting'
            last=values
            if (yield p.enable):
                if (yield p.ready):events.append(values)
                count+=1
            yield p.rdata.eq(0xa5000000 | ((yield p.addr)&0xff))
            yield p.ready.eq(count>=3)
        else:
            count=0;last=None
            yield p.ready.eq(0)
        yield
def transfer(off,data=None,error=0):
    yield [d.off.eq(off),d.write.eq(data is not None),d.wdata.eq(data or 0),d.selected.eq(1),d.commit.eq(1)]
    yield
    for _ in range(32):
        if (yield d.complete):break
        yield
    else:raise AssertionError('FR completion timeout')
    assert (yield d.error)==error
    result=(yield d.rdata)
    yield [d.selected.eq(0),d.commit.eq(0)]
    for _ in range(3):yield
    return result
def bench():
    yield
    value=yield from transfer(0x10)
    assert value==0xa5000010
    yield from transfer(0x24,0x9f)
    assert events==[(0xf8000010,0,0),(0xf8000024,0x9f,1)],events
    for off,data in [(1,None),(0x80,None),(0,7),(0x7c,1)]:
        yield from transfer(off,data,error=1)
    assert len(events)==2,'invalid access reached vendor interface'
    print('PASS Flash FR setup/access waits, stable fields, translated address, exactly-once and invalid-access isolation')
run_simulation(d,[bench(),native()])
