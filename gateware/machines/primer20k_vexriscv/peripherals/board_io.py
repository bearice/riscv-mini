"""20K dock adapter. Event and WS2812 timing live in the shared cores."""
from gateware.machines.primer20k_vexriscv.csr_layout import packed_status
from gateware.devices.gpio.core import ButtonEvents
from gateware.devices.ws2812.core import WS2812Core
from migen import Signal, Mux
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourceLevel


class BoardIO(LiteXModule):
    def __init__(self, leds, buttons_n, switches, debounce_cycles=300000, led_active_low_mask=0x3f, button_active_low_mask=0xf, switch_active_low_mask=0):
        self._leds = CSRStorage(len(leds), name='leds')
        packed_status(self, 'board_io')
        self._clear = CSR(8, name='clear')
        self.submodules.events = ButtonEvents(buttons_n ^ button_active_low_mask, debounce_cycles)
        switch_sync = Signal(len(switches))
        self.specials += MultiReg(switches, switch_sync)
        self.comb += [
            leds.eq(self._leds.storage ^ led_active_low_mask),
            self._buttons.status.eq(self.events.stable),
            self._switches.status.eq(switch_sync ^ switch_active_low_mask),
            self._pressed.status.eq(self.events.pressed),
            self._released.status.eq(self.events.released),
            self.events.clear.eq(Mux(self._clear.wr_stb, self._clear.wr_data, 0)),
        ]
        self.ev = EventManager()
        self.ev.change = EventSourceLevel()
        self.ev.finalize()
        self.comb += self.ev.change.trigger.eq((self.events.pressed | self.events.released) != 0)


class WS2812(LiteXModule):
    def __init__(self, pad, clock_hz=60000000):
        self._color = CSRStorage(24, name='color')
        self._send = CSR(name='send')
        self._busy = CSRStatus(name='busy')
        self._completed = CSRStatus(32, name='completed')
        self.submodules.core = WS2812Core(pad, clock_hz)
        self.comb += [
            self.core.color.eq(self._color.storage),
            self.core.send.eq(self._send.wr_stb),
            self._busy.status.eq(self.core.busy),
            self._completed.status.eq(self.core.completed),
        ]
