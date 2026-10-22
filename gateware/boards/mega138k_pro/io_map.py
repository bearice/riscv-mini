"""Published Mega 138K board I/O. The machine file is the only pin map."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BOARD_PATH = ROOT / 'gateware' / 'boards' / 'mega138k_pro' / 'board.json'
MACHINE_PATH = ROOT / 'gateware' / 'machines' / 'mega138k_ae350' / 'machine.json'
PIN = re.compile(r'\b([A-Z]{1,2}\d{1,2})\b')


def load():
    return json.loads(BOARD_PATH.read_text(encoding='utf-8'))


def user_io():
    board = load()
    if 'user_io' not in board:
        raise ValueError('mega138k board description has no user_io contract')
    return board['user_io']


def leds():
    return user_io()['leds']


def buttons():
    return user_io()['buttons']


def ws2812():
    return user_io()['ws2812']


def inject_bit():
    return user_io()['gpio_inject_bit']


def audio_binding():
    """One authoritative pin map for the audio output and user-wired mic."""
    resources = load()['resources']
    tx, rx = resources['audio'], resources['microphone']
    if rx['active_owner'] != 'microphone':
        raise ValueError('PMOD0/J28/SSPI must be owned exclusively by microphone')
    pins = [tx[k] for k in ('bck', 'ws', 'data', 'pa_en')]
    pins += [rx[k] for k in ('data', 'bck', 'lr', 'ws')]
    if len(set(pins)) != 8 or set(pins) & (reserved_pins() | set(published_pins())):
        raise ValueError('audio/microphone pins collide with board resources')
    if any(r['io_standard'] != 'LVCMOS33' or r['bank_vccio'] != 3.3 for r in (tx, rx)):
        raise ValueError('audio/microphone require the documented 3.3V banks')
    return {'tx': {k: tx[k] for k in ('bck', 'ws', 'data', 'pa_en', 'io_standard')},
            'rx': {k: rx[k] for k in ('data', 'bck', 'lr', 'ws', 'io_standard')}}


def _mask(bits):
    value = 0
    for bit in bits:
        value |= 1 << bit
    return value


def contiguous(bits):
    return bool(bits) and list(bits) == list(range(bits[0], bits[0] + len(bits)))


def gpio_binding(io=None):
    """Software-visible GPIO contract taken from the machine description."""
    io = user_io() if io is None else io
    led_bits = [item['gpio_bit'] for item in io['leds']]
    button_bits = [item['gpio_bit'] for item in io['buttons']]
    inject = io['gpio_inject_bit']
    button_mask = _mask(button_bits)
    return {
        'led_count': len(led_bits),
        'button_count': len(button_bits),
        'led_bit': led_bits[0],
        'button_bit': button_bits[0],
        'led_bits': tuple(led_bits),
        'button_bits': tuple(button_bits),
        'led_mask': _mask(led_bits),
        'button_mask': button_mask,
        'inject_bit': inject,
        'irq_mask': button_mask | (1 << inject),
        'led_active_low': tuple(bool(item['active_low']) for item in io['leds']),
        'button_active_low': tuple(bool(item['active_low']) for item in io['buttons']),
    }


def debounce_cycles(clock_hz):
    samples = {item['debounce_ms'] for item in buttons()}
    if len(samples) != 1:
        raise ValueError('button debounce must be one value')
    cycles = round(clock_hz * samples.pop() / 1000)
    if cycles < 2:
        raise ValueError('debounce is shorter than two clocks')
    return cycles


def _pins(text):
    return set(PIN.findall(text))


def reserved_pins():
    board = load()
    resources = board['resources']
    pins = set(resources['clock']['pins'])
    pins.update(resources['core_reset']['pins'])
    pins.add(resources['debug_uart']['rx'])
    pins.add(resources['debug_uart']['tx'])
    jtag = resources['cpu_jtag']
    for name in ('tck', 'tdi', 'tdo', 'tms'):
        pins.add(jtag[name]['pin'])
    camera = resources['camera']
    for key in ('mipi0_lanes', 'mipi1_lanes', 'dock_control', 'dvp', 'shared_i2c_control'):
        pins.update(camera[key])
    pins.update(user_io()['protected_pins'])
    machine = json.loads(MACHINE_PATH.read_text(encoding='utf-8'))
    for value in machine['ddr3']['pins'].values():
        pins.update(_pins(value if isinstance(value, str) else ''))
    return pins


def published_pins():
    pins = [item['pin'] for item in leds()]
    pins += [item['pin'] for item in buttons()]
    pins.append(ws2812()['pin'])
    return pins


def validate(io):
    errors = []
    seen_pins = []
    seen_bits = []
    for kind, rows in (('led', io['leds']), ('button', io['buttons'])):
        for index, item in enumerate(rows):
            if item['index'] != index:
                errors.append('%s index gap at %d' % (kind, index))
            if item['pin'] in seen_pins:
                errors.append('duplicate pin ' + item['pin'])
            seen_pins.append(item['pin'])
            if item['gpio_bit'] in seen_bits:
                errors.append('duplicate gpio bit %d' % item['gpio_bit'])
            seen_bits.append(item['gpio_bit'])
            if item['gpio_bit'] > 31 or item['bank_vccio'] not in (1.5, 1.8, 2.5, 3.3):
                errors.append('bad electrical description for ' + item['pin'])
            if kind == 'led' and item['reset'] != 'off':
                errors.append('LED reset must be off: ' + item['pin'])
            if kind == 'led' and item['gpio_bit'] != index:
                errors.append('LED register bit must match its index: ' + item['pin'])
            if 'active_low' not in item:
                errors.append('missing polarity for ' + item['pin'])
        bits = [item['gpio_bit'] for item in rows]
        if not contiguous(bits):
            errors.append(kind + ' gpio bits are not a contiguous block')
    if io['gpio_inject_bit'] in seen_bits or not 0 <= io['gpio_inject_bit'] <= 31:
        errors.append('inject bit collides with a published GPIO')
    ws = io['ws2812']
    if ws['pin'] in seen_pins:
        errors.append('WS2812 pin is also a GPIO')
    seen_pins.append(ws['pin'])
    if not ws['present'] or not ws['wired']:
        errors.append('published WS2812 must stay present until the machine file says otherwise')
    if ws.get('physical_display') == 'accepted':
        errors.append('physical WS2812 display is not an accepted result')
    overlap = set(seen_pins) & reserved_pins()
    if overlap:
        errors.append('published pins use protected resources: ' + ' '.join(sorted(overlap)))
    if io['switches']:
        errors.append('this board has no published DIP switches')
    return errors


def check():
    return validate(user_io())


def require():
    errors = check()
    if errors:
        raise ValueError('board I/O map: ' + '; '.join(errors))
