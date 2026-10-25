"""Real asynchronous FIFOs must retain AHB beats, errors and request ownership."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350.sd_cdc import AHBClockCrossing
from gateware.machines.mega138k_ae350.peripherals.audio import AudioData
from gateware.machines.mega138k_ae350.address_map import SD_TX_DATA
from litex.soc.interconnect.ahb import AHBInterface
from test_ae350_interconnect import xact
m=Module();front=AHBInterface(data_width=32,address_width=32);back=AHBInterface(data_width=32,address_width=32)
m.submodules.bridge=AHBClockCrossing(front,back)
m.submodules.target=ClockDomainsRenamer('mem')(AudioData(back,SD_TX_DATA,True))
m.comb += m.target.valid.eq(1)
observed=[]
from migen.sim import passive
@passive
def target():
    while True:
        if (yield m.target.transfer):observed.append((yield back.wdata))
        yield

def bench():
    for i in range(4):
        error,_=yield from xact(front,True,SD_TX_DATA,0x12340000+i)
        assert error==0
    assert observed==[0x12340000+i for i in range(4)],observed
    error,_=yield from xact(front,False,SD_TX_DATA)
    assert error==1
    error,_=yield from xact(front,True,SD_TX_DATA+4,123)
    assert error==1
    assert len(observed)==4
fragment=m.get_fragment()
from migen.fhdl.specials import Memory
for memory in fragment.specials:
    if isinstance(memory,Memory):
        for port in memory.ports:
            if port.dat_r is None:port.dat_r=Signal(memory.width)
run_simulation(fragment,{'sys':bench(),'mem':target()},clocks={'sys':(10,3),'mem':8})
print('PASS asynchronous SD AHB data phase, ordered words and two-cycle ERROR propagation')

from gateware.machines.mega138k_ae350.sd_cdc import SDClockCrossing
from gateware.machines.mega138k_ae350.fabric import AE350Fabric
from gateware.machines.mega138k_ae350.address_map import AddressMap,SD_BASE
from gateware.description import resolve_machine
from gateware.machines.mega138k_ae350.ports import bind_ports
from test_ae350_platform import Cpu
class Native(Module):
    def __init__(self):
        self.off,self.wdata,self.rdata=Signal(12),Signal(32),Signal(32)
        self.write,self.commit,self.selected,self.ok=(Signal() for _ in range(4))
        self.tx_bus=AHBInterface(data_width=32,address_width=32);self.rx_bus=AHBInterface(data_width=32,address_width=32)
        self.tx_req,self.rx_req,self.tx_ack,self.rx_ack=(Signal() for _ in range(4))
        self.word,self.commits=Signal(32),Signal(32)
        self.ack_events=Signal(32)
        self.sync += If(self.tx_ack,self.ack_events.eq(self.ack_events+1))
        self.comb += [self.ok.eq(self.off==0x80),self.rdata.eq(self.word)]
        self.sync += If(self.commit & self.selected & self.ok & self.write,self.word.eq(self.wdata),self.commits.eq(self.commits+1))
m=Module();machine=resolve_machine('mega138k_ae350');layout=AddressMap(machine)
native=Native();m.submodules.native=ClockDomainsRenamer('mem')(native)
m.submodules.sd=sd=SDClockCrossing(native);m.submodules.cpu=cpu=Cpu()
m.submodules.fabric=AE350Fabric(cpu,{0:Signal(32)},bind_ports(machine),layout,sd=sd)
def access(write,off,value=0):
    yield [cpu.apb_select.eq(1),cpu.apb_enable.eq(0),cpu.apb_write.eq(write),
           cpu.apb_addr.eq(SD_BASE+off),cpu.apb_wdata.eq(value),cpu.apb_strb.eq(15)]
    yield
    yield cpu.apb_enable.eq(1)
    yield
    for _ in range(128):
        yield
        if (yield cpu.apb_ready):break
    else:raise AssertionError('SD APB CDC did not complete')
    error=(yield cpu.apb_slverr);result=(yield cpu.apb_data)
    yield [cpu.apb_select.eq(0),cpu.apb_enable.eq(0)]
    yield
    return error,result
def bench2():
    assert (yield from access(1,0x80,0x12345678))[0]==0
    assert (yield from access(0,0x80))==(0,0x12345678)
    assert (yield native.commits)==1
    assert (yield from access(1,0x84,0xffffffff))[0]==1
    assert (yield native.commits)==1
    assert (yield from access(1,0x81,0xffffffff))[0]==1
    assert (yield native.commits)==1
    yield native.tx_req.eq(1)
    for _ in range(8):yield
    assert (yield sd.tx_req)==1
    yield cpu.dma_ack[4].eq(1)
    yield
    yield cpu.dma_ack[4].eq(0)
    for _ in range(8):
        yield
        assert (yield sd.tx_req)==0,'CDC ack latency duplicated a request'
    assert (yield native.ack_events)==1
    yield native.tx_req.eq(0)
    for _ in range(8):yield
    yield native.tx_req.eq(1)
    for _ in range(8):yield
    assert (yield sd.tx_req)==1
    yield cpu.dma_ack[4].eq(1)
    for _ in range(4):yield
    yield cpu.dma_ack[4].eq(0)
    for _ in range(12):yield
    assert (yield native.ack_events)==2,'held ACK duplicated native events'
fragment=m.get_fragment()
for memory in fragment.specials:
    if isinstance(memory,Memory):
        for port in memory.ports:
            if port.dat_r is None:port.dat_r=Signal(memory.width)
run_simulation(fragment,{'sys':bench2()},clocks={'sys':(10,3),'mem':8})
print('PASS full fabric APB CDC exactly-once commit/readback/errors and no duplicate REQ after ACK')
