"""Thin JSON-to-LiteX resource conversion and project GPIO binding helpers.

Native ConstraintManager owns resource allocation; this module owns no parallel
port graph, reservation state or resource manager.
"""
from dataclasses import replace
from gateware.description import MachineDescription
from litex.build.generic_platform import Pins, Subsignal
from gateware.gowin_platform import constraints


class IOBindings:
    def __init__(self, machine: MachineDescription):
        self.machine = machine
        self.board = machine.board
        self.resources = []

    def signal(self, resource, signal):
        return self.board.resources[resource].signals[signal]

    def pad(self, name, resource, signal='pad', index=0, bit=None):
        spec = self.signal(resource, signal)
        if bit is not None:
            drive = (spec.drive_overrides or {}).get(str(bit), spec.drive_ma)
            spec = replace(spec, pins=(spec.pins[bit],), drive_ma=drive, drive_overrides=None)
        self.resources.append((name, index, Pins(' '.join(spec.pins)), *constraints(spec)))

    def resource(self, name, resource, index=0, rename=None, select=None):
        rename = rename or {}
        signals = self.board.resources[resource].signals
        keys = select if select is not None else signals
        self.signals(name, {rename.get(key,key): signals[key] for key in keys}, index)

    def differential_port(self, positive, negative, signals):
        # Gowin accepts each differential pair as one native Pins token.
        # ConstraintManager still owns allocation and final port resolution.
        for index, signal in enumerate(signals):
            self.resources.append((positive, index, Pins(','.join(signal.pins)), *constraints(signal)))
            self.resources.append((negative, index, Pins('X')))

    def signals(self, name, signals, index=0):
        self.resources.append((name, index, *[Subsignal(key, Pins(' '.join(spec.pins)),
            *constraints(spec)) for key,spec in signals.items()]))

    def gpio_rows(self, group):
        offset = self.machine.gpio.button_bit_offset if group == 'buttons' else 0
        return tuple((i, offset + i, self.signal(ref, 'pad'))
                     for i, ref in enumerate(getattr(self.machine.gpio, group)))

    def _rows(self, group):
        return tuple(dict(index=index, name=ref, pin=spec.pins[0], gpio_bit=bit,
            io_standard=spec.io_standard, bank_vccio=spec.bank_vccio or 3.3,
            active_low=spec.active_low, reset='off', debounce_ms=self.machine.gpio.debounce_ms)
            for (index, bit, spec), ref in zip(self.gpio_rows(group), getattr(self.machine.gpio, group)))

    def leds(self):
        return self._rows('leds')

    def buttons(self):
        return self._rows('buttons')

    def ws2812(self):
        spec = self.signal(self.machine.devices.ws2812, 'data')
        return dict(pin=spec.pins[0], io_standard=spec.io_standard, present=True, wired=True)

    def inject_bit(self):
        return self.machine.gpio.inject_bit

    def user_io(self):
        return dict(leds=self.leds(), buttons=self.buttons(), switches=self._rows('switches'),
                    ws2812=self.ws2812(), gpio_inject_bit=self.inject_bit())

    def gpio_binding(self, data=None):
        data = self.user_io() if data is None else data
        leds = tuple(row['gpio_bit'] for row in data['leds'])
        buttons = tuple(row['gpio_bit'] for row in data['buttons'])
        led_mask = sum(1 << bit for bit in leds)
        button_mask = sum(1 << bit for bit in buttons)
        inject = data['gpio_inject_bit']
        return dict(led_count=len(leds), button_count=len(buttons), led_bit=leds[0],
            button_bit=buttons[0], led_bits=leds, button_bits=buttons, led_mask=led_mask,
            button_mask=button_mask, inject_bit=inject,
            irq_mask=button_mask | (1 << inject if inject is not None else 0),
            led_active_low=tuple(spec.active_low for _, _, spec in self.gpio_rows('leds')),
            button_active_low=tuple(spec.active_low for _, _, spec in self.gpio_rows('buttons')))

    def validate(self, data):
        errors = []
        for group in ('leds', 'buttons'):
            bits = [row['gpio_bit'] for row in data[group]]
            if not bits or bits != list(range(bits[0], bits[0] + len(bits))):
                errors.append(group + ' bits are not contiguous')
        pins = [row['pin'] for group in ('leds', 'buttons') for row in data[group]]
        if len(pins) != len(set(pins)):
            errors.append('GPIO pins overlap')
        return errors

    def check(self):
        return self.validate(self.user_io())

    def require(self):
        errors = self.check()
        if errors:
            raise ValueError('; '.join(errors))

    def debounce_cycles(self, clock_hz):
        cycles = round(clock_hz * self.machine.gpio.debounce_ms / 1000)
        if cycles < 2:
            raise ValueError('button debounce is shorter than two clocks')
        return cycles

    def video_binding(self):
        ref = self.machine.devices.video
        signals = self.board.resources[ref].signals
        if self.board.resources[ref].kind != 'hdmi':
            return {key: signal.pins for key, signal in signals.items()}
        clk = signals['clk']
        pairs = tuple(signals['data_' + str(i)].pins for i in range(3))
        return dict(port=ref.removeprefix('video_'), clock_pair=clk.pins, data_pairs=pairs,
            pins=clk.pins + tuple(pin for pair in pairs for pin in pair),
            io_standard=clk.io_standard, bank_vccio=clk.bank_vccio or 3.3, drive=clk.drive_ma or 8)

    def audio_binding(self):
        def pins(ref, keys):
            signals = self.board.resources[ref].signals
            return {**{key: signals[key].pins[0] for key in keys},
                    'io_standard': signals[keys[0]].io_standard}
        return dict(tx=pins(self.machine.devices.audio, ('bck','ws','data','pa_en')),
                    rx=pins(self.machine.devices.microphones[0], ('data','bck','lr','ws')))
