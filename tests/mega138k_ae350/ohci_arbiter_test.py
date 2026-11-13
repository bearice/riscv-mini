"""Four simultaneous DDR readers must keep response ownership and make progress."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI
from gateware.machines.mega138k_ae350.video_memory import DDRMemoryArbiter
m=Module();memory=DDR3UI();ports=[DDR3UI() for _ in range(4)]
m.submodules.arbiter=a=DDRMemoryArbiter(memory,*ports)
counts=[0]*4

def bench():
    for signal in (a.cpu_pending,a.video_pending,a.network_pending,a.usb_pending):
        yield signal.eq(1)
    for index,p in enumerate(ports):
        yield p.addr.eq(index*8)
        yield p.cmd.eq(1)
        yield p.cmd_en.eq(1)
    yield memory.cmd_ready.eq(1)
    yield memory.wr_data_rdy.eq(1)
    outstanding=None;delay=0;expected=None;delivered=None
    for cycle in range(800):
        # Clients see a response one cycle after the controller returns it.
        seen=[]
        for index,p in enumerate(ports):
            if (yield p.rd_data_valid):
                seen.append(index)
                assert (yield p.rd_data)==0x12340000+index
        assert seen==([] if delivered is None else [delivered]),(seen,delivered)
        delivered=None
        if (yield memory.rd_data_valid):
            counts[outstanding]+=1
            delivered=outstanding
            outstanding=None
        yield memory.rd_data_valid.eq(0)
        if delay:
            delay-=1
            if not delay:
                yield memory.rd_data.eq(expected)
                yield memory.rd_data_valid.eq(1)
        if (yield memory.cmd_en):
            assert outstanding is None,'another client stole an outstanding read'
            outstanding=(yield memory.addr)//8
            assert 0<=outstanding<4
            expected=0x12340000+outstanding
            delay=3
        yield
    assert min(counts)>10,counts
    assert max(counts)-min(counts)<=1,counts
run_simulation(m,bench())
print('PASS four-way DDR arbitration: fairness and response ownership',counts)
