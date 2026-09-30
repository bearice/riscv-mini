"""Two native callers, delayed one-cycle DRAM replies and consumer backpressure."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation, passive
from litedram.common import LiteDRAMNativePort
from gateware.memory import SharedNativePort

native=LiteDRAMNativePort('both',24,128)
dut=SharedNativePort(native)
counts={'cpu':0,'video':0,'reads':0,'writes':0}
def value(addr): return addr*0x10203040506070809
def master(port,name,base):
    for i in range(30):
        addr=base+i
        write=name=='cpu' and i%3==0
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
        if counts['cpu']==counts['video']==30: return
        yield
    raise AssertionError(('Arbitration stalled',counts))

run_simulation(dut,[master(dut.cpu,'cpu',100),master(dut.video,'video',1000),controller(),deadline()])
assert counts=={'cpu':30,'video':30,'reads':50,'writes':10},counts
print('Shared native port PASS:',counts)
