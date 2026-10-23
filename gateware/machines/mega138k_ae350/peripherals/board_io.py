"""AE350 APB adapters for the shared event core and the shared WS2812 core.

Pages follow the common 32-byte header. The cores do not see addresses or
package pins. Buttons and the diagnostic inject bit drive GPIO_IN of the hard controller.
LEDs are the register at 0x28: 1 means on, and the pad adapter applies
polarity. This page also publishes debounced button events and the WS2812
register view.
"""
from migen import Signal, If, Cat, Mux, Case
from litex.gen import LiteXModule

from gateware.boards.mega138k_pro import io_map
from gateware.devices.gpio.core import ButtonEvents
from gateware.devices.ws2812.core import WS2812Core
from gateware.machines.mega138k_ae350 import address_map as amap


class FabricBoard(LiteXModule):
    def __init__(self, clock_hz=amap.FABRIC_HZ, debounce_cycles=None):
        io_map.require()
        self.leds = Signal(len(io_map.leds()))
        self.gpio_pin = Signal(32)
        self.button_async = Signal(len(io_map.buttons()))
        self.ws_pad = Signal()
        self.irq = Signal()
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.ws_selected = Signal()
        self.io_selected = Signal()
        self.commit = Signal()
        self.ws_rdata = Signal(32)
        self.io_rdata = Signal(32)
        self.ws_ok = Signal()
        self.io_ok = Signal()
        self.io_ok.attr.add(("syn_keep", 1))

        cycles = io_map.debounce_cycles(clock_hz) if debounce_cycles is None else debounce_cycles
        self.submodules.events = ButtonEvents(self.button_async, cycles)
        self.submodules.ws = WS2812Core(self.ws_pad, clock_hz)
        color = Signal(24)
        inject = Signal()
        irq_en = Signal()
        ws_en = Signal()
        diag = Signal()
        edges = Signal(32)
        ws_done = Signal()
        ws_error = Signal(32)
        io_error = Signal(32)
        seen = Signal(32)
        press_clear = Signal(len(io_map.buttons()))
        release_clear = Signal(len(io_map.buttons()))
        self.comb += [
            self.ws.color.eq(color),
            self.events.clear.eq(Cat(press_clear, release_clear)),
        ]
        for item in io_map.buttons():
            self.comb += self.gpio_pin[item['gpio_bit']].eq(self.events.stable[item['index']])
        button_pending = Signal()
        self.comb += [
            self.gpio_pin[io_map.inject_bit()].eq(inject),
            button_pending.eq((self.events.pressed | self.events.released) != 0),
            self.irq.eq(irq_en & (button_pending | diag | (ws_en & ws_done))),
        ]

        ws_read = {
            0x00: amap.WS2812_ID, 0x04: amap.ABI_VERSION, 0x08: amap.WS2812_CAP,
            0x0c: 0, 0x10: self.ws.busy, 0x14: ws_done, 0x18: ws_en, 0x1c: ws_error,
            0x20: color, 0x24: self.ws.completed,
        }
        io_read = {
            0x00: amap.IO_ID, 0x04: amap.ABI_VERSION, 0x08: amap.IO_CAP,
            0x0c: inject << 1, 0x10: button_pending | diag | ws_done,
            0x14: Cat(self.events.pressed, self.events.released, diag),
            0x18: irq_en, 0x1c: io_error, 0x20: edges, 0x24: self.events.stable,
            0x28: self.leds,
        }
        ws_offsets = tuple(ws_read)
        io_offsets = tuple(io_read)
        ws_hit = Signal()
        io_hit = Signal()
        self.comb += [
            ws_hit.eq(Cat(*[self.off == item for item in ws_offsets]) != 0),
            io_hit.eq(Cat(*[self.off == item for item in io_offsets]) != 0),
            self.ws_ok.eq((~self.write & ws_hit) |
                (self.write & (self.off == 0x0c) & (self.wdata <= 1)) |
                (self.write & (self.off == 0x14) & (self.wdata <= 1)) |
                (self.write & (self.off == 0x18) & (self.wdata <= 1)) |
                (self.write & (self.off == 0x20) & (self.wdata[24:] == 0))),
            self.io_ok.eq((~self.write & io_hit) |
                (self.write & (self.off == 0x0c) & ((self.wdata & ~0x6) == 0)) |
                (self.write & (self.off == 0x14) & (self.wdata <= 0x1ff)) |
                (self.write & (self.off == 0x18) & (self.wdata <= 1)) |
                (self.write & (self.off == 0x28) & (self.wdata[len(io_map.leds()):] == 0))),
            Case(self.off, {**{offset: self.ws_rdata.eq(value) for offset, value in ws_read.items()},
                            'default': self.ws_rdata.eq(0)}),
            Case(self.off, {**{offset: self.io_rdata.eq(value) for offset, value in io_read.items()},
                            'default': self.io_rdata.eq(0)}),
        ]
        ws_admit,io_admit=Signal(),Signal()
        self.sync += [ws_admit.eq(self.ws_selected & self.ws_ok & self.write),
                      io_admit.eq(self.io_selected & self.io_ok & self.write)]
        ws_go = self.commit & ws_admit
        io_go = self.commit & io_admit
        send = ws_go & (self.off == 0x0c) & self.wdata[0] & ~self.ws.busy
        busy_reject = ws_go & (self.off == 0x0c) & self.wdata[0] & self.ws.busy
        diag_pulse = io_go & (self.off == 0x0c) & self.wdata[2]
        io_w1c = io_go & (self.off == 0x14)
        ws_w1c = ws_go & (self.off == 0x14)
        done_rise = Signal()
        self.comb += [
            self.ws.send.eq(send),
            press_clear.eq(Mux(io_w1c, self.wdata[:len(press_clear)], 0)),
            release_clear.eq(Mux(io_w1c, self.wdata[4:4 + len(release_clear)], 0)),
            done_rise.eq(self.ws.completed != seen),
        ]
        self.sync += [
            seen.eq(self.ws.completed),
            If(ws_go & (self.off == 0x20), color.eq(self.wdata[:24])),
            If(ws_go & (self.off == 0x18), ws_en.eq(self.wdata[0])),
            If(busy_reject, ws_error.eq(1)).Elif(send, ws_error.eq(0)),
            ws_done.eq(Mux(ws_w1c & self.wdata[0], done_rise, ws_done | done_rise)),
            If(io_go & (self.off == 0x28), self.leds.eq(self.wdata[:len(io_map.leds())])),
            If(io_go & (self.off == 0x0c), inject.eq(self.wdata[1])),
            If(io_go & (self.off == 0x18), irq_en.eq(self.wdata[0])),
            If(diag_pulse & (edges != 0xffffffff), edges.eq(edges + 1)),
            diag.eq(Mux(io_w1c & self.wdata[8], diag_pulse, diag | diag_pulse)),
            If(diag_pulse, io_error.eq(0)),
        ]
