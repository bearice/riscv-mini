"""Shared event core, WS2812 timing, and the AE350 I/O pages.

Run: python tests/common/test_board_events.py
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'mega138k_ae350'))

from migen import Signal
from migen.sim import run_simulation

from gateware.boards.mega138k_pro import io_map
from gateware.devices.gpio.core import ButtonEvents
from gateware.devices.ws2812.core import WS2812Core, timing
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.platform import Ae350Platform
from gateware.machines.mega138k_ae350.peripherals.board_io import FabricBoard
from test_ae350_platform import Cpu, apb


def check(failures, name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ((' ' + str(detail)) if detail != '' else ''))
    if not cond:
        failures.append(name)


def events(failures):
    raw = Signal(4)
    dut = ButtonEvents(raw, 4)

    def tb():
        for _ in range(6):
            yield
        yield raw.eq(1)
        for _ in range(2):
            yield
        yield raw.eq(0)
        for _ in range(8):
            yield
        check(failures, 'bounce ignored', (yield dut.stable) == 0 and (yield dut.pressed) == 0)
        yield raw.eq(1)
        for _ in range(8):
            yield
        check(failures, 'press sticks', (yield dut.stable) == 1 and (yield dut.pressed) == 1)
        held = yield dut.pressed
        for _ in range(6):
            yield
        check(failures, 'read does not clear', (yield dut.pressed) == held)
        yield raw.eq(0x5)
        for _ in range(8):
            yield
        check(failures, 'simultaneous press', (yield dut.stable) == 0x5)
        yield raw.eq(0)
        for _ in range(8):
            yield
        check(failures, 'release sticks', (yield dut.released) == 0x5, hex((yield dut.released)))
    run_simulation(dut, tb())


def _rising_latency(debounce):
    raw = Signal()
    dut = ButtonEvents(raw, debounce)
    seen = []

    def tb():
        yield raw.eq(1)
        for cycle in range(1, 40):
            yield
            if (yield dut.pressed) & 1:
                seen.append(cycle)
                return
    run_simulation(dut, tb())
    if not seen:
        raise RuntimeError('press never became pending')
    return seen[0]


def same_cycle(failures):
    """Clear on the clock that creates the sticky bit, not on the raw input change.

    The simulator commits a generator write after the edge that resumes the
    generator, so the clear for an edge observed on step N is written on step N-1.
    """
    debounce = 4
    latency = _rising_latency(debounce)
    raw = Signal(2)
    dut = ButtonEvents(raw, debounce)

    def tb():
        yield raw.eq(1)
        for _ in range(latency):
            yield
        check(failures, 'primer press is pending', (yield dut.pressed) == 1, hex((yield dut.pressed)))
        yield raw.eq(0b11)
        before = None
        at_edge = None
        for cycle in range(latency):
            yield dut.clear.eq(1 if cycle == latency - 2 else 0)
            yield
            if cycle == latency - 2:
                before = yield dut.pressed
            if cycle == latency - 1:
                at_edge = yield dut.pressed
        check(failures, 'clear is armed on the cycle before it is sampled', before == 1, hex(before))
        check(failures, 'same-cycle edge survives clear', at_edge == 0b10, hex(at_edge))
        yield dut.clear.eq(0b10)
        yield
        yield
        check(failures, 'clear without a new edge drops sticky', (yield dut.pressed) == 0)
    run_simulation(dut, tb())

    early_raw = Signal(2)
    early = ButtonEvents(early_raw, debounce)

    def early_tb():
        yield early_raw.eq(1)
        for _ in range(latency):
            yield
        yield early_raw.eq(0b11)
        gap = None
        arrived = None
        for cycle in range(latency):
            yield early.clear.eq(1 if cycle == latency - 3 else 0)
            yield
            if cycle == latency - 2:
                gap = yield early.pressed
            if cycle == latency - 1:
                arrived = yield early.pressed
        check(failures, 'earlier clear removes the old edge first', gap == 0, hex(gap))
        check(failures, 'clear before the event does not swallow it', arrived == 0b10, hex(arrived))
    run_simulation(early, early_tb())


def _commit(dut, ws, offset, data):
    yield [dut.off.eq(offset), dut.write.eq(1), dut.wdata.eq(data), dut.commit.eq(1),
           dut.ws_selected.eq(1 if ws else 0), dut.io_selected.eq(0 if ws else 1)]
    yield
    yield dut.commit.eq(0)
    yield


def _read(dut, offset):
    yield [dut.off.eq(offset), dut.write.eq(0), dut.io_selected.eq(1), dut.ws_selected.eq(0)]
    yield
    return (yield dut.io_rdata)


def combinations(failures):
    """Button, diagnostic and WS2812 completion share one IRQ line."""
    dut = FabricBoard(clock_hz=8_000_000, debounce_cycles=4)

    def tb():
        yield from _commit(dut, False, 0x18, 1)
        yield dut.button_async.eq(1)
        seen = 0
        for _ in range(30):
            yield
            seen = yield dut.irq
            if seen:
                break
        check(failures, 'button alone raises irq', seen == 1, seen)
        status = yield from _read(dut, 0x10)
        check(failures, 'button status is pending', status & 1 == 1, hex(status))
        yield from _commit(dut, False, 0x0c, 4)
        check(failures, 'diag does not clear a pending button irq', (yield dut.irq) == 1, (yield dut.irq))
        sticky = yield from _read(dut, 0x14)
        check(failures, 'button and diag are both sticky', sticky & 0x101 == 0x101, hex(sticky))
        summary = yield from _read(dut, 0x10)
        check(failures, 'combined status stays pending', summary & 1 == 1, hex(summary))
        yield from _commit(dut, False, 0x14, 0x100)
        sticky = yield from _read(dut, 0x14)
        check(failures, 'clearing diag leaves the button irq', (yield dut.irq) == 1 and sticky == 1,
              (hex(sticky), (yield dut.irq)))
        yield from _commit(dut, False, 0x0c, 4)
        yield from _commit(dut, False, 0x14, 1)
        sticky = yield from _read(dut, 0x14)
        check(failures, 'clearing the button leaves diag', (yield dut.irq) == 1 and sticky == 0x100,
              (hex(sticky), (yield dut.irq)))
        yield from _commit(dut, False, 0x14, 0x100)
        check(failures, 'clearing the last event drops irq', (yield dut.irq) == 0, (yield dut.irq))
        yield dut.button_async.eq(0)
        for _ in range(30):
            yield
            if (yield dut.events.released) & 1:
                break
        yield from _commit(dut, False, 0x14, 0x10)
        check(failures, 'release clear leaves irq low', (yield dut.irq) == 0 and ((yield dut.events.released) & 1) == 0)
        for _ in range(2600):
            if (yield dut.ws.busy) == 0:
                break
            yield
        check(failures, 'ws2812 leaves reset', (yield dut.ws.busy) == 0)
        yield from _commit(dut, True, 0x18, 1)
        yield from _commit(dut, True, 0x20, 0x008000)
        yield from _commit(dut, True, 0x0c, 1)
        rose = 0
        for _ in range(2800):
            yield
            rose = yield dut.irq
            if rose:
                break
        check(failures, 'ws2812 completion raises irq', rose == 1, rose)
        yield dut.button_async.eq(1)
        for _ in range(30):
            yield
            if (yield dut.events.pressed) & 1:
                break
        check(failures, 'button and ws2812 stay pending together',
              (yield dut.irq) == 1 and ((yield dut.events.pressed) & 1) == 1, (yield dut.irq))
        yield from _commit(dut, True, 0x14, 1)
        check(failures, 'clearing ws2812 leaves the button irq', (yield dut.irq) == 1, (yield dut.irq))
        yield from _commit(dut, False, 0x14, 1)
        check(failures, 'clearing both events drops irq', (yield dut.irq) == 0, (yield dut.irq))
    run_simulation(dut, tb())


def waveform(failures):
    period, zero, one, latch = timing(80_000_000)
    check(failures, '80 MHz slot', (period, zero, one) == (100, 32, 64) and latch == 24000, (period, zero, one, latch))
    pad = Signal()
    core = WS2812Core(pad, 80_000_000)

    def tb():
        for _ in range(latch + 2):
            yield
        check(failures, 'initial latch ends', (yield core.busy) == 0 and (yield core.completed) == 0)
        yield core.color.eq(0x008000)
        yield core.send.eq(1)
        yield
        yield core.send.eq(0)
        high = 0
        for _ in range(period):
            high += yield pad
            yield
        check(failures, '80 MHz one-bit high', high == one, high)
        yield core.color.eq(0x008000)
        yield core.send.eq(1)
        yield
        yield core.send.eq(0)
        high = 0
        for _ in range(period):
            high += yield pad
            yield
        check(failures, 'busy send ignored', (yield core.busy) == 1 and (yield core.completed) == 0 and high == zero, high)
    run_simulation(core, tb())


def binding(failures):
    dut = FabricBoard(debounce_cycles=4)

    def tb():
        yield dut.button_async.eq(1)
        for _ in range(8):
            yield
        check(failures, 'debounced button reaches the GPIO pin', ((yield dut.gpio_pin) >> 8) & 1 == 1,
              hex((yield dut.gpio_pin)))
        yield dut.button_async.eq(0)
        for _ in range(8):
            yield
        check(failures, 'release reaches the GPIO pin', ((yield dut.gpio_pin) >> 8) & 1 == 0)
        check(failures, 'button edge stays until cleared', (yield dut.events.pressed) & 1 == 1)
    run_simulation(dut, tb())


def pages(failures):
    cpu = Cpu()
    dut = Ae350Platform(cpu, {0: Signal(32, reset=1), 4: Signal(32)})

    def tb():
        check(failures, 'reset outputs are off', (yield dut.board.leds) == 0 and (yield dut.board.ws_pad) == 0)
        err, value, _ = yield from apb(cpu, False, amap.SYSINFO_BASE + 0x20)
        check(failures, 'device count', err == 0 and value == amap.DEVICE_COUNT, value)
        err, value, _ = yield from apb(cpu, False, amap.SYSINFO_BASE + 0x08)
        check(failures, 'io capabilities', err == 0 and (value & (amap.CAP_BOARD_IO | amap.CAP_WS2812)) == (amap.CAP_BOARD_IO | amap.CAP_WS2812), hex(value))
        err, value, _ = yield from apb(cpu, False, amap.WS2812_BASE)
        check(failures, 'ws2812 id', err == 0 and value == amap.WS2812_ID, hex(value))
        err, value, _ = yield from apb(cpu, False, amap.IO_BASE)
        check(failures, 'event id', err == 0 and value == amap.IO_ID, hex(value))
        err, before, _ = yield from apb(cpu, False, amap.WS2812_BASE + 0x20)
        err, _, _ = yield from apb(cpu, True, amap.WS2812_BASE + 0x20, 0x01000000)
        _, after, _ = yield from apb(cpu, False, amap.WS2812_BASE + 0x20)
        check(failures, 'reserved color rejected', err == 1 and after == before, (err, hex(after)))
        err, _, _ = yield from apb(cpu, True, amap.WS2812_BASE + 0x20, 0x00010203)
        check(failures, 'color accepted', err == 0)
        _, color, _ = yield from apb(cpu, False, amap.WS2812_BASE + 0x20)
        check(failures, 'color stored', color == 0x00010203, hex(color))
        err, _, _ = yield from apb(cpu, True, amap.IO_BASE + 0x28, 0x15)
        _, leds, _ = yield from apb(cpu, False, amap.IO_BASE + 0x28)
        check(failures, 'logical LED readback', err == 0 and leds == 0x15 and (yield dut.board.leds) == 0x15,
              hex(leds))
        err, _, _ = yield from apb(cpu, True, amap.IO_BASE + 0x28, 0x40)
        _, leds, _ = yield from apb(cpu, False, amap.IO_BASE + 0x28)
        check(failures, 'LED bit outside the mask is rejected', err == 1 and leds == 0x15, (err, hex(leds)))
        err, _, _ = yield from apb(cpu, True, amap.IO_BASE + 0x18, 0)
        err, _, _ = yield from apb(cpu, True, amap.IO_BASE + 0x0c, 4)
        err, _, _ = yield from apb(cpu, True, amap.IO_BASE + 0x0c, 4)
        _, status, _ = yield from apb(cpu, False, amap.IO_BASE + 0x14)
        _, edges, _ = yield from apb(cpu, False, amap.IO_BASE + 0x20)
        check(failures, 'level merges two edges', err == 0 and status == 0x100 and edges == 2, (hex(status), edges))
        check(failures, 'masked fabric irq stays down', ((yield cpu.interrupt) & 0x80) == 0, hex((yield cpu.interrupt)))
        err, _, _ = yield from apb(cpu, True, amap.IO_BASE + 0x18, 1)
        check(failures, 'fabric irq rises while pending', ((yield cpu.interrupt) & 0x80) == 0x80,
              hex((yield cpu.interrupt)))
        err, _, _ = yield from apb(cpu, True, amap.IO_BASE + 0x14, 0x100)
        _, status, _ = yield from apb(cpu, False, amap.IO_BASE + 0x14)
        check(failures, 'clear drops the irq', err == 0 and status == 0 and ((yield cpu.interrupt) & 0x80) == 0,
              hex(status))
        err, fan, _ = yield from apb(cpu, False, amap.SENSE_BASE + 0x20)
        check(failures, 'fan resets to full', err == 0 and fan == 100, fan)
        err, _, _ = yield from apb(cpu, True, amap.SENSE_BASE + 0x20, 101)
        _, fan, _ = yield from apb(cpu, False, amap.SENSE_BASE + 0x20)
        check(failures, 'fan duty above 100 is rejected', err == 1 and fan == 100, (err, fan))
        err, _, _ = yield from apb(cpu, True, amap.SENSE_BASE + 0x20, 0)
        _, fan, _ = yield from apb(cpu, False, amap.SENSE_BASE + 0x20)
        for _ in range(8):
            yield
        check(failures, 'fan duty 0 releases the gate', err == 0 and fan == 0 and (yield dut.sense.pull) == 0,
              (fan, (yield dut.sense.pull)))
        err, _, _ = yield from apb(cpu, True, amap.SENSE_BASE + 0x20, 50)
        lows = 0
        for _ in range(3200):
            lows += yield dut.sense.pull
            yield
        check(failures, 'fan duty 50 pulls the gate for half the period', lows == 1600, lows)
        yield dut.sense.tach.eq(0)
        for _ in range(4):
            yield
        for _ in range(3):
            yield dut.sense.tach.eq(1)
            for _ in range(1000):
                yield
            yield dut.sense.tach.eq(0)
            for _ in range(1000):
                yield
        _, period, _ = yield from apb(cpu, False, amap.SENSE_BASE + 0x2c)
        check(failures, 'tach period is one pulse', 1984 <= period <= 2016 and period % 16 == 0, period)
        err, _, _ = yield from apb(cpu, True, amap.SENSE_BASE + 0x0c, 1)
        status = 0
        for _ in range(16):
            err, status, _ = yield from apb(cpu, False, amap.SENSE_BASE + 0x10)
            if status & 1:
                break
        _, raw, _ = yield from apb(cpu, False, amap.SENSE_BASE + 0x24)
        _, mdeg, _ = yield from apb(cpu, False, amap.SENSE_BASE + 0x28)
        check(failures, 'temperature sample', err == 0 and status == 1 and raw == 100 and mdeg == 25000,
              (status, raw, mdeg))
        err, _, _ = yield from apb(cpu, False, amap.WS2812_BASE + 0x1000)
        check(failures, 'next peripheral page is empty', err == 1, err)
    fragment = dut.get_fragment()
    from migen.fhdl.specials import Memory
    for memory in fragment.specials:
        if isinstance(memory, Memory):
            for port in memory.ports:
                if port.dat_r is None:
                    port.dat_r = Signal(memory.width)
    run_simulation(fragment, tb())


def main():
    failures = []
    check(failures, 'pin map', io_map.check() == [], io_map.check())
    check(failures, 'address map', amap.check() == [], amap.check())
    binding_now = io_map.gpio_binding()
    described = amap.interface()['gpio']
    check(failures, 'generated masks match the board file',
          amap.GPIO_LED_MASK == binding_now['led_mask'] and
          amap.GPIO_BUTTON_MASK == binding_now['button_mask'] and
          amap.GPIO_IRQ_MASK == binding_now['irq_mask'] and
          amap.GPIO_INJECT_BIT == binding_now['inject_bit'] and
          described['led_active_low'] == [int(item) for item in binding_now['led_active_low']] and
          ((binding_now['irq_mask'] >> 17) & 1) == 0 and
          ((binding_now['irq_mask'] >> binding_now['button_bit']) & 1) == 1)
    gapped = copy.deepcopy(io_map.user_io())
    gapped['leds'][1]['gpio_bit'] = 7
    check(failures, 'gapped led bits are rejected',
          any('contiguous' in item for item in io_map.validate(gapped)))
    events(failures)
    same_cycle(failures)
    waveform(failures)
    combinations(failures)
    binding(failures)
    pages(failures)
    print('RESULT', 'PASS' if not failures else 'FAIL: ' + ','.join(failures))
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
