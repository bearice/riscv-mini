"""Exercise real serial edges, all four lanes, APB rejection and CS holding."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation, passive
from test_ae350_platform import Cpu, _io, amap, apb
from gateware.machines.mega138k_ae350.fabric import AE350Fabric

cpu = Cpu()
dut = AE350Fabric(cpu, {0: Signal(32)}, _io, amap)
q = dut.qspi
wire = []
quad_value = 0x12a5c3e7

@passive
def device():
    old_clk = 0
    position = 0
    while True:
        clk = yield q.clk
        if (yield q.cs_n):
            position = 0
        elif clk and not old_clk:
            wire.append((yield q.dq_o))
            position += 1
        shift = 28 - (position % 8) * 4
        yield q.dq_i.eq((quad_value >> shift) & 15)
        old_clk = clk
        yield

def bench():
    def access(write, off, value=0, strb=15):
        return (yield from apb(cpu, write, amap.SPI_BASE+off, value, strb))
    yield
    assert (yield from access(False, 0))[1] == amap.SPI_ID
    assert (yield from access(True, 0xc, 1, 1))[0] == 1
    assert (yield q.cs_n) == 1
    assert (yield from access(True, 0xc, 1))[0] == 0
    wire.clear()
    assert (yield from access(True, 0x18, 0x9f000000))[0] == 0
    # Reconfiguration must not truncate an active transfer.
    assert (yield from access(True, 0xc, 0))[0] == 1
    for _ in range(20): yield
    assert [v & 1 for v in wire] == [1,0,0,1,1,1,1,1], wire
    assert (yield q.cs_n) == 0 and (yield q.clk) == 0
    assert (yield from access(True, 0xc, 0))[0] == 0
    assert (yield from access(True, 0x18, 0))[0] == 1
    # Distinct bits on every DQ lane expose swapping and nibble order errors.
    assert (yield from access(True, 0xc, 0x307))[0] == 0
    assert (yield q.dq_oe) == 0
    assert (yield from access(True, 0x18, 0))[0] == 0
    for _ in range(20): yield
    value = (yield from access(False, 0x1c))[1]
    assert value == quad_value, hex(value)
    assert (yield from access(True, 0xc, 0))[0] == 0
    assert (yield from access(False, 0x28))[0] == 1
    print('PASS QSPI serial opcode, quad lane/nibble order, CS, busy and partial-write rejection')

fragment = dut.get_fragment()
for memory in fragment.specials:
    if isinstance(memory, Memory):
        for port in memory.ports:
            if port.dat_r is None: port.dat_r = Signal(memory.width)
run_simulation(fragment, [bench(), device()], clocks={'sys': 10})
