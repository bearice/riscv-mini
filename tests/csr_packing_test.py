"""Exercise actual CSR bank readback and sticky button event clearing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Signal
from migen.sim import run_simulation
from litex.gen import LiteXModule
from litex.soc.interconnect.csr_bus import CSRBank, Interface
from gateware.csr_layout import STATUS_LAYOUTS, packed_status
from gateware.board_io import BoardIO

for name, words in STATUS_LAYOUTS.items():
    dut = LiteXModule()
    packed_status(dut, name)
    bus = Interface(data_width=32, address_width=14)
    csrs = [getattr(dut, '_'+word) for word, _ in words]
    dut.submodules.bank = CSRBank(csrs, 0, bus=bus)
    def check():
        for index, (word, fields) in enumerate(words):
            expected = 0
            offset = 0
            for field, width in fields:
                value = (1 << width)-1
                yield getattr(dut, '_'+field).status.eq(value)
                expected |= value << offset
                offset += width
            yield bus.adr.eq(index)
            yield
            yield
            assert (yield bus.dat_r) == expected, (name, word)
            # Isolate every field so swapped offsets cannot pass an all-ones test.
            offset = 0
            for selected, width in fields:
                for field, field_width in fields:
                    yield getattr(dut, '_'+field).status.eq((1 << field_width)-1 if field == selected else 0)
                yield
                yield
                assert (yield bus.dat_r) == ((1 << width)-1) << offset, (name, selected, 'offset')
                offset += width
            for field, width in fields:
                yield getattr(dut, '_'+field).status.eq((1 << width)-1)
            yield
            yield
            # A write to the packed read-only word must not mutate state.
            yield bus.we.eq(1)
            yield bus.dat_w.eq(0)
            yield
            yield bus.we.eq(0)
            yield
            yield
            assert (yield bus.dat_r) == expected, (name, word, 'write')
    run_simulation(dut, check())

leds, buttons, switches = Signal(6), Signal(4, reset=15), Signal(4)
dut = BoardIO(leds, buttons, switches, debounce_cycles=3)
def events():
    yield switches.eq(10)
    yield buttons.eq(14)
    for _ in range(12):yield
    assert (yield dut._inputs.status) == 0x1a1
    yield dut._clear.wr_data.eq(1)
    yield dut._clear.wr_stb.eq(1)
    yield
    yield dut._clear.wr_stb.eq(0)
    yield
    assert (yield dut._pressed.status) == 0
    yield buttons.eq(15)
    for _ in range(12):yield
    assert (yield dut._inputs.status) == 0x10a0
    yield dut._clear.wr_data.eq(16)
    yield dut._clear.wr_stb.eq(1)
    yield
    yield dut._clear.wr_stb.eq(0)
    yield
    assert (yield dut._released.status) == 0
run_simulation(dut, events())
print('PASS: packed CSR readback, read-only writes, button debounce/sticky clear')
