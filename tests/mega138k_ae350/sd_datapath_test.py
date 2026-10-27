"""Actual LiteSDCard stream conversion: stalls, byte order and reset tails."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import ResetInserter, Signal
from migen.fhdl.specials import Memory
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350.peripherals.sd import SDDataPath

d=ResetInserter()(SDDataPath())

def bench():
    # Receive two card words; a stopped DMA must retain the first word.
    for byte in (0x12,0x34,0x56,0x78,0x9a,0xbc,0xde,0xf0):
        yield d.sink.data.eq(byte)
        yield d.sink.valid.eq(1)
        yield
        while not (yield d.sink.ready): yield
    yield d.sink.valid.eq(0)
    for _ in range(8): yield
    assert (yield d.rx_fifo.level)==2
    assert (yield d.rx_fifo.source.data)==0x12345678
    for _ in range(10):
        yield
        assert (yield d.rx_fifo.source.data)==0x12345678
    yield d.rx_fifo.source.ready.eq(1)
    yield
    yield
    assert (yield d.rx_fifo.source.data)==0x9abcdef0
    yield
    yield d.rx_fifo.source.ready.eq(0)
    # TX convert preserves bytes across consumer stalls.
    yield d.tx_input.data.eq(0x12345678)
    yield d.tx_input.valid.eq(1)
    yield
    yield d.tx_input.valid.eq(0)
    out=[]
    for _ in range(4):
        while not (yield d.source.valid): yield
        old=(yield d.source.data)
        for _ in range(3):
            yield
            assert (yield d.source.data)==old
        out.append(old)
        yield d.source.ready.eq(1)
        yield
        yield d.source.ready.eq(0)
        yield
    assert out==[0x12,0x34,0x56,0x78],out
    # A reset discards an incomplete receive word before the next transfer.
    yield d.sink.valid.eq(1)
    yield d.sink.data.eq(0xaa)
    yield
    yield d.sink.valid.eq(0)
    yield d.reset.eq(1)
    yield
    yield d.reset.eq(0)
    for _ in range(5):yield
    assert (yield d.rx_fifo.level)==0
    assert not (yield d.source.valid)
fragment=d.get_fragment()
for memory in fragment.specials:
    if isinstance(memory,Memory):
        for port in memory.ports:
            if port.dat_r is None:port.dat_r=Signal(memory.width)
run_simulation(fragment,bench())
print('PASS SD stream byte order, backpressure and partial-word reset')
