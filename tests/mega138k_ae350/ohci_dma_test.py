"""Exercise OHCI word accesses through the real asynchronous DDR queues."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from gateware.machines.mega138k_ae350.peripherals.usb_ohci import OHCIDDRPort

m = Module()
b = wishbone.Interface(data_width=32, address_width=32, addressing='word')
m.submodules.dut = dut = OHCIDDRPort(b, ram_bytes=4096)
ui = dut.memory.ui
commands = []
line = int.from_bytes(bytes(range(32)), 'little')
@passive
def memory():
    yield ui.cmd_ready.eq(1)
    yield ui.wr_data_rdy.eq(1)
    delay = 0
    while True:
        yield ui.rd_data_valid.eq(0)
        if delay:
            delay -= 1
            if not delay:
                yield ui.rd_data.eq(line)
                yield ui.rd_data_valid.eq(1)
        if (yield ui.cmd_en) and (yield ui.cmd_ready):
            cmd = (yield ui.cmd)
            commands.append((cmd, (yield ui.addr), (yield ui.wr_data), (yield ui.wr_data_mask)))
            if cmd == 1:
                delay = 4
        yield

def access(address, write=0, data=0, enables=15):
    yield b.adr.eq(address // 4)
    yield b.we.eq(write)
    yield b.dat_w.eq(data)
    yield b.sel.eq(enables)
    yield b.cyc.eq(1)
    yield b.stb.eq(1)
    for _ in range(100):
        yield
        if (yield b.ack) or (yield b.err):
            result = (yield b.dat_r), (yield b.err)
            break
    else:
        raise AssertionError('OHCI DMA timed out')
    yield b.cyc.eq(0)
    yield b.stb.eq(0)
    yield
    yield
    return result

def bench():
    for lane in range(8):
        value, error = yield from access(32 + lane * 4)
        assert not error
        assert value == int.from_bytes(bytes(range(lane*4, lane*4+4)), 'little')
    for lane in range(8):
        _, error = yield from access(64 + lane * 4, 1, 0x12345678, 5)
        assert not error
        cmd, address, data, mask = commands[-1]
        assert cmd == 0 and address == 16
        assert data == 0x12345678 << (lane * 32)
        assert mask == (0xffffffff ^ (5 << (lane * 4)))
    count = len(commands)
    assert (yield from access(4096))[1]
    assert len(commands) == count, 'out-of-range DMA reached DDR'
fragment = m.get_fragment()
for special in fragment.specials:
    if isinstance(special, Memory):
        for port in special.ports:
            if port.dat_r is None:
                port.dat_r = Signal(special.width)
run_simulation(fragment, {'sys': bench(), 'ddr': memory()}, clocks={'sys':20,'ddr':10})
print('PASS OHCI DMA: eight lanes, byte masks, delayed reads, bounded address errors')
