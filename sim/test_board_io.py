"""Debounce/transition ownership and real WS2812 pulse timing at 60 MHz."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Signal
from migen.sim import run_simulation
from gateware.board_io import BoardIO,WS2812

leds=Signal(6);keys=Signal(4,reset=15);switches=Signal(4)
gpio=BoardIO(leds,keys,switches,debounce_cycles=5)
# Standalone simulation must include the compound pending CSR's field packing;
# the SoC's CSR bank does this automatically during a real build.
gpio.ev.pending.finalize(32,'big')
gpio.submodules.pending_csr=gpio.ev.pending
def gpio_bench():
    for _ in range(10):yield
    assert (yield leds)==63
    yield gpio._leds.storage.eq(0x21);yield switches.eq(10)
    for _ in range(5):yield
    assert (yield leds)==0x1e and (yield gpio._switches.status)==10
    yield keys.eq(14)
    for _ in range(2):yield
    yield keys.eq(15)
    for _ in range(12):yield
    assert (yield gpio._buttons.status)==0 and (yield gpio._pressed.status)==0
    yield gpio.ev.enable.storage.eq(1);yield keys.eq(14)
    for _ in range(12):yield
    assert (yield gpio._buttons.status)==1 and (yield gpio._pressed.status)==1 and (yield gpio.ev.irq)==1
    yield gpio._clear.wr_data.eq(1);yield gpio._clear.wr_stb.eq(1);yield
    yield gpio._clear.wr_stb.eq(0)
    for _ in range(20):yield
    assert (yield gpio._pressed.status)==0 and (yield gpio.ev.irq)==0, 'held key repeated'
    yield keys.eq(15)
    for _ in range(12):yield
    assert (yield gpio._released.status)==1 and (yield gpio.ev.irq)==1
    yield gpio._clear.wr_data.eq(16);yield gpio._clear.wr_stb.eq(1);yield
    yield gpio._clear.wr_stb.eq(0);yield
    yield keys.eq(3) # Key4/5 simultaneously
    for _ in range(12):yield
    assert (yield gpio._pressed.status)==12 and (yield gpio._released.status)==0
    print('Board IO PASS: polarity/order, switches, bounce suppression, hold/no-repeat, release and simultaneous keys')
run_simulation(gpio,gpio_bench())

wire=Signal();ws=WS2812(wire)
def ws_bench():
    for _ in range(18005):
        assert (yield wire)==0
        yield
    assert not (yield ws._busy.status) and (yield ws._completed.status)==0
    yield ws._color.storage.eq(0x123456);yield ws._send.wr_stb.eq(1);yield
    yield ws._send.wr_stb.eq(0)
    # The simulator samples before the edge; align at the first high bit.
    for _ in range(5):
        if (yield wire):break
        yield
    samples=[]
    for cycle in range(75*24):
        samples.append((yield wire))
        if cycle==100:
            yield ws._color.storage.eq(0xffffff);yield ws._send.wr_stb.eq(1)
        if cycle==101:yield ws._send.wr_stb.eq(0)
        yield
    expected=0x341256
    for bit in range(24):
        high=48 if expected&(1<<(23-bit)) else 24
        assert samples[75*bit:75*(bit+1)]==[1]*high+[0]*(75-high), ('pulse/GRB',bit)
    for _ in range(17999):
        assert (yield wire)==0 and (yield ws._busy.status)
        yield
    for _ in range(4):yield
    assert not (yield ws._busy.status) and (yield ws._completed.status)==1
    print('WS2812 PASS: GRB MSB-first, 400/800ns high, 1.25us period, >=300us latch, busy write ignored')
run_simulation(ws,ws_bench())
