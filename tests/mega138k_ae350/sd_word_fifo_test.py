"""Register FIFO: full backpressure, wraparound, simultaneous transfer, reset."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import ResetInserter
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350.peripherals.sd import WordFIFO
d=ResetInserter()(WordFIFO())
def push(value):
    assert (yield d.sink.ready)
    yield [d.sink.valid.eq(1),d.sink.data.eq(value),d.sink.first.eq(value&1),d.sink.last.eq((value>>1)&1)]
    yield
    yield d.sink.valid.eq(0)
    yield
def pop(value):
    assert (yield d.source.valid)
    assert (yield d.source.data)==value
    assert (yield d.source.first)==(value&1)
    assert (yield d.source.last)==((value>>1)&1)
    yield d.source.ready.eq(1)
    yield
    yield d.source.ready.eq(0)
    yield
def bench():
    for value in range(16):yield from push(value)
    assert (yield d.level)==16 and not (yield d.sink.ready)
    yield [d.sink.valid.eq(1),d.sink.data.eq(99)]
    for _ in range(3):yield
    yield d.sink.valid.eq(0)
    yield
    assert (yield d.level)==16
    for value in range(8):yield from pop(value)
    for value in range(16,24):yield from push(value)
    for value in range(8,24):yield from pop(value)
    assert not (yield d.source.valid)
    yield from push(25)
    yield [d.sink.valid.eq(1),d.sink.data.eq(26),d.sink.first.eq(0),d.sink.last.eq(1),d.source.ready.eq(1)]
    yield
    yield [d.sink.valid.eq(0),d.source.ready.eq(0)]
    yield
    assert (yield d.level)==1
    yield from pop(26)
    yield from push(27)
    yield d.reset.eq(1)
    yield
    yield d.reset.eq(0)
    yield
    assert (yield d.level)==0 and not (yield d.source.valid)
run_simulation(d,bench())
print('PASS SD register FIFO full/wrap/metadata/simultaneous transfer/reset')
