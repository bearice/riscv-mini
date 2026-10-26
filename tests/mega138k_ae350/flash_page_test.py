"""FR APB page coexists with SYSINFO and rejects partial command writes."""
from migen import *
from migen.sim import run_simulation,passive
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test_ae350_platform import Cpu,_io,amap,apb
from gateware.machines.mega138k_ae350.fabric import AE350Fabric

cpu=Cpu()
p=Record([('addr',32),('wdata',32),('rdata',32),('select',1),('enable',1),('write',1),('ready',1)])
d=AE350Fabric(cpu,{0:Signal(32)},_io,amap,flash=p)
events=[]
@passive
def vendor():
    count=0
    while True:
        if (yield p.select) and (yield p.enable):
            if (yield p.ready):events.append(((yield p.addr),(yield p.wdata),(yield p.write)))
            count+=1
        else:count=0
        yield p.ready.eq(count>=3)
        yield p.rdata.eq(0xabcd0000 | ((yield p.addr)&0xff))
        yield
def bench():
    yield
    error,value,_=yield from apb(cpu,False,amap.SYSINFO_BASE)
    assert error==0 and value==amap.SYSINFO_ID and not events
    error,value,_=yield from apb(cpu,False,amap.FLASH_CTRL_BASE)
    assert error==0 and value==0xabcd0000
    error,_,_=yield from apb(cpu,True,amap.FLASH_CTRL_BASE+0x24,0x9f)
    assert error==0 and len(events)==2
    error,_,_=yield from apb(cpu,True,amap.FLASH_CTRL_BASE+0x24,0x20,strb=1)
    assert error==1 and len(events)==2,'partial erase opcode reached FR'
    error,_,_=yield from apb(cpu,False,amap.FLASH_CTRL_BASE+0x80)
    assert error==1 and len(events)==2
    error,value,_=yield from apb(cpu,False,amap.BOOT_CLOCK_ADDR)
    assert error==0 and value==amap.FABRIC_HZ
    assert events==[(0xf8000000,0,0),(0xf8000024,0x9f,1)],events
    print('PASS FR page translation/handshake, SYSINFO isolation and partial/unknown access rejection')
fragment=d.get_fragment()
for memory in fragment.specials:
    if isinstance(memory,Memory):
        for port in memory.ports:
            if port.dat_r is None:port.dat_r=Signal(memory.width)
run_simulation(fragment,[bench(),vendor()],clocks={'sys':10})
