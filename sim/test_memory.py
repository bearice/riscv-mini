"""Two native callers, delayed one-cycle DRAM replies and consumer backpressure."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation, passive
from litedram.common import LiteDRAMNativePort
from gateware.memory import SharedNativePort
from gateware.dma_scheduler import DMAMemoryScheduler

native=LiteDRAMNativePort('both',24,128)
shared_dma='--dma-shared' in sys.argv
dut=DMAMemoryScheduler(native) if shared_dma else SharedNativePort(native,spacing_csr=len(sys.argv)>1)
if len(sys.argv)>1 and not shared_dma:
    spacing=int(sys.argv[1]);assert spacing in (0,2,4,8)
    dut._spacing.storage.reset=spacing
counts={'cpu':0,'video':0,'reads':0,'writes':0}
if shared_dma: counts['dma']=0
def value(addr): return addr*0x10203040506070809
def master(port,name,base):
    for i in range(30):
        addr=base+i
        write=name!='video' and i%3==0
        yield port.cmd.addr.eq(addr)
        yield port.cmd.we.eq(write)
        yield port.cmd.valid.eq(1)
        yield
        while not (yield port.cmd.ready): yield
        yield port.cmd.valid.eq(0)
        if write:
            yield port.wdata.data.eq(value(addr))
            yield port.wdata.we.eq(0xffff)
            yield port.wdata.valid.eq(1)
            yield
            while not (yield port.wdata.ready): yield
            yield port.wdata.valid.eq(0)
        else:
            # Ready remains low while the response is being received/held.
            for _ in range(i%5+1): yield
            while not (yield port.rdata.valid): yield
            assert (yield port.rdata.data)==value(addr),(name,i,(yield port.rdata.data))
            yield port.rdata.ready.eq(1)
            yield
            yield port.rdata.ready.eq(0)
        counts[name]+=1
        yield

@passive
def controller():
    yield native.cmd.ready.eq(1)
    while True:
        while not (yield native.cmd.valid): yield
        addr=(yield native.cmd.addr);write=(yield native.cmd.we)
        yield native.cmd.ready.eq(0)
        for _ in range(addr%7+2):
            yield
            assert not (yield native.cmd.valid),'Another request issued before completion'
        if write:
            yield native.wdata.ready.eq(1)
            yield
            while not (yield native.wdata.valid): yield
            assert (yield native.wdata.data)==value(addr)
            assert (yield native.wdata.we)==0xffff
            yield native.wdata.ready.eq(0)
            counts['writes']+=1
        else:
            yield native.rdata.data.eq(value(addr))
            yield native.rdata.valid.eq(1)
            yield
            yield native.rdata.valid.eq(0)
            # Change shared bus immediately after the single-cycle response.
            yield native.rdata.data.eq(0xdeadbeef)
            counts['reads']+=1
        yield native.cmd.ready.eq(1)
        yield

def deadline():
    for _ in range(3000):
        if counts['cpu']==counts['video']==30 and (not shared_dma or counts['dma']==30): return
        yield
    raise AssertionError(('Arbitration stalled',counts))

processes=[master(dut.cpu,'cpu',100),master(dut.video,'video',1000),controller(),deadline()]
if shared_dma: processes.append(master(dut.dma,'dma',2000))
run_simulation(dut,processes)
expected={'cpu':30,'video':30,'reads':50,'writes':10}
if shared_dma: expected.update(dma=30,reads=70,writes=20)
assert counts==expected,counts
print('Shared native port PASS:',counts)
