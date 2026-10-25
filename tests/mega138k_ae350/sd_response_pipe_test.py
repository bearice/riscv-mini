"""A buffered last byte must not re-arm the PHY while Core request stays high."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation,passive
from migen.fhdl.specials import Memory
from litex.soc.interconnect import stream
from gateware.machines.mega138k_ae350.peripherals.sd import CommandResponsePipe
p=SimpleNamespace(sink=stream.Endpoint([('cmd_type',2),('data_type',2),('length',8)]),source=stream.Endpoint([('data',8),('status',3)]))
d=CommandResponsePipe(p)
def bench():
    yield [d.sink.valid.eq(1),d.sink.cmd_type.eq(1),d.sink.length.eq(6),d.source.ready.eq(0)]
    yield
    assert (yield p.sink.valid)
    # PHY has finished while Core cannot yet consume the buffered last byte.
    yield [p.source.valid.eq(1),p.source.last.eq(1),p.source.data.eq(0x95),p.sink.ready.eq(1)]
    yield
    yield [p.source.valid.eq(0),p.sink.ready.eq(0)]
    for _ in range(12):
        yield
        assert not (yield p.sink.valid),'buffering re-armed a completed request'
    assert (yield d.source.valid) and (yield d.source.last)
    assert (yield d.source.data)==0x95
    yield d.source.ready.eq(1)
    yield
    yield d.source.ready.eq(0)
    yield
    assert not (yield p.sink.valid)
    # Core releases valid before its next command; one new PHY request follows.
    yield d.sink.valid.eq(0)
    yield
    yield d.sink.valid.eq(1)
    yield
    assert (yield p.sink.valid)
fragment=d.get_fragment()
for memory in fragment.specials:
    if isinstance(memory,Memory):
        for port in memory.ports:
            if port.dat_r is None:port.dat_r=Signal(memory.width)
run_simulation(fragment,bench())
print('PASS buffered SD response completion cannot duplicate PHY requests')
