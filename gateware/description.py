"""Validated board resources and machine configuration shared by both backends."""
import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_INPUTS = ('requirements.lock', 'gateware/description.py', 'gateware/schemas/board.schema.json',
                 'gateware/schemas/machine.schema.json')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON property: ' + key)
        result[key] = value
    return result


@lru_cache(maxsize=16)
def _validator(source):
    schema = json.loads(source, object_pairs_hook=_pairs)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load(path, kind, root=ROOT):
    """Validate before returning data; schema files are always repository inputs."""
    path, root = Path(path), Path(root)
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_pairs)
    validate(data, kind, path, root)
    return data


def validate(data, kind, path, root=ROOT):
    path, root = Path(path), Path(root)
    schema = root / 'gateware/schemas' / (kind + '.schema.json')
    errors = sorted(_validator(schema.read_text(encoding='utf-8')).iter_errors(data),
                    key=lambda error: str(list(error.absolute_path)))
    if errors:
        error = errors[0]
        location = '.'.join(map(str, error.absolute_path)) or '<root>'
        raise ValueError('%s:%s: %s' % (path, location, error.message))
    if data['id'] != path.parent.name:
        raise ValueError('%s: id must match its directory' % path)


def load_board(board, root=ROOT):
    _identifier(board)
    return load(Path(root) / 'gateware/boards' / board / 'board.json', 'board', root)


def _identifier(value):
    import re
    if not isinstance(value, str) or not re.fullmatch('[a-z][a-z0-9_]*', value):
        raise ValueError('Invalid platform identifier: ' + str(value))


def load_machine(machine, root=ROOT):
    """Return the same validated (board, machine) pair for every build caller."""
    _identifier(machine)
    config = load(Path(root) / 'gateware/machines' / machine / 'machine.json', 'machine', root)
    board = load_board(config['board'], root)
    validate_pair(board, config)
    return board, config


def validate_pair(board, config):
    resources = board['resources']
    for ref, resource in resources.items():
        for name, signal in resource['signals'].items():
            standard = signal['io_standard']
            nominal = {'LVCMOS15': 1.5, 'SSTL15': 1.5, 'SSTL15D': 1.5,
                       'LVCMOS18': 1.8, 'LVCMOS25': 2.5, 'LVCMOS33': 3.3, 'LVCMOS33D': 3.3}[standard]
            if 'bank_vccio' in signal and signal['bank_vccio'] != nominal:
                raise ValueError('I/O standard and bank voltage disagree: ' + ref + '.' + name)
            if 'active_low' in signal and not (resource['kind'] in ('reset', 'led', 'button', 'switch') or
                                                resource['kind'] == 'sd' and name == 'detect'):
                raise ValueError('Polarity is unsupported for ' + ref + '.' + name)
            if 'drive_overrides' in signal and not (config['cpu']['kind'] == 'hard' and
                                                    resource['kind'] == 'ddr3' and name == 'addr'):
                raise ValueError('Per-bit drive is unsupported for ' + ref + '.' + name)
            if any(int(index) >= len(signal['pins']) for index in signal.get('drive_overrides', {})):
                raise ValueError('Drive override index exceeds pin width')
            if 'vref' in signal and not (resource['kind'] == 'ddr3' and name == 'dq'):
                raise ValueError('VREF is supported only for DDR data pins')
    required = {'clock': 'clock', 'core_reset': 'reset', 'debug_uart': 'uart',
                config['memory']['resource']: 'ddr3'}
    kinds = dict(video=('hdmi', 'rgb_lcd'), audio=('audio',), ws2812=('ws2812',),
                 i2c=('i2c',), twi_mux=('twi_mux',), sd=('sd',), fan=('fan',), cpu_jtag=('jtag',), flash=('spi',),
                 ethernet=('rmii',), ethernet_clock=('clock',), usb=('ulpi',),
                 spi_lcd=('spi_lcd',), phy_reset=('reset',))
    for name, ref in config['devices'].items():
        if name == 'microphones':
            required.update({item: 'microphone' for item in ref})
        else:
            if ref not in resources or resources[ref]['kind'] not in kinds[name]:
                raise ValueError('Invalid %s resource: %s' % (name, ref))
            required[ref] = resources[ref]['kind']
    for group, kind in (('leds', 'led'), ('buttons', 'button'), ('switches', 'switch')):
        required.update({ref: kind for ref in config['gpio'][group]})
        offset = config['gpio'].get({'leds': 'led', 'buttons': 'button', 'switches': 'switch'}[group] + '_bit_offset', 0)
        if offset + len(config['gpio'][group]) > 32:
            raise ValueError('GPIO bit range exceeds 32 bits: ' + group)
    occupied = {pin: 'reserved' for pin in board['reserved_pins']}
    for ref, kind in required.items():
        if ref not in resources or resources[ref]['kind'] != kind:
            raise ValueError('Missing or incompatible board resource: ' + ref)
        for signal in resources[ref]['signals'].values():
            for pin in signal['pins']:
                if pin in occupied:
                    raise ValueError('Pin %s claimed by %s and %s' % (pin, occupied[pin], ref))
                occupied[pin] = ref
    memory = config['memory']
    if config['cpu']['kind'] == 'hard':
        parameters = config['ip']['cpu']['parameters']
        # The native adapter wires these ports. Reject layouts it cannot consume
        # before invoking a generator rather than silently ignoring the setting.
        required_ports = ('ENABLE_CUSTOMIZED_DATA', 'ENABLE_ITCM', 'ENABLE_EXTENDED_APB_SLAVE',
                          'ENABLE_UART2', 'ENABLE_GPIO', 'ENABLE_RTC', 'ENABLE_DMAC',
                          'ENABLE_EXTENDED_AHB_SLAVE', 'ENABLE_PLIC', 'ENABLE_SPI', 'ENABLE_I2C')
        if any(not parameters[name] for name in required_ports):
            raise ValueError('AE350 fabric requires its ITCM, DDR master, APB/AHB slave, UART2, GPIO, RTC, DMA, PLIC, SPI and I2C ports')
        unsupported_ports = ('ENABLE_DTCM', 'ENABLE_DDR3', 'ENABLE_CUSTOMIZED_INSTRUCTION',
                             'ENABLE_SPI_FLASH', 'ENABLE_EXTENDED_AHB_MASTER',
                             'ENABLE_GPIO_IO_PORTS', 'ENABLE_SPI2_IO_PORTS', 'ENABLE_I2C_IO_PORTS')
        if any(parameters[name] for name in unsupported_ports):
            raise ValueError('AE350 fabric does not connect the selected additional native ports')
        if parameters['ItcmSize'] != 0 or config['boot']['rom_size_bytes'] != 32768:
            raise ValueError('AE350 ITCM boot adapter currently requires the 32 KiB configuration')
        if config['clocks']['data_memory_bus'] * 4 != config['ip']['ddr3']['parameters']['Memory_Clock'] * 1000000:
            raise ValueError('DDR UI clock must match the selected controller CK/4 clock')
        ddr = config['ip']['ddr3']['parameters']
        pins = resources[memory['resource']]['signals']
        if any(ddr[key] != len(pins[signal]['pins']) for key, signal in
               (('Dq_Width', 'dq'), ('Row_Address', 'addr'), ('Bank_Address', 'ba'))):
            raise ValueError('DDR controller geometry must match the physical bus widths')
        if (ddr['CLK_Ratio'] != '1:4' or ddr['User_Interface'] != 'Controller'
                or ddr['Burst_Mode'] != 8 or ddr['Ecc'] or ddr['Rank_Address'] != 1):
            raise ValueError('DDR adapter requires the single-rank CK/4 BL8 controller interface without ECC')
        if not 1 <= ddr['Column_Address'] <= 16:
            raise ValueError('DDR column address width is unsupported')
        capacity = (1 << (ddr['Row_Address'] + ddr['Bank_Address'] + ddr['Column_Address'])) * ddr['Dq_Width'] // 8
        if memory['size_bytes'] > capacity:
            raise ValueError('DDR memory window exceeds the configured controller capacity')
        pll = config['ip']['ddr_pll']['parameters']
        if pll['CLKIN_FREQ'] * 1000000 != board['input_clock_hz']:
            raise ValueError('DDR PLL input frequency differs from the board oscillator')
        if pll['CLKOUT2_FREQ'] != config['ip']['ddr3']['parameters']['Memory_Clock']:
            raise ValueError('DDR PLL output frequency differs from controller CK')
        if pll['CLKOUT0_FREQ'] != 50:
            raise ValueError('The selected DDR controller requires a 50 MHz reference clock')
        if not all(pll[key] for key in ('General_Mode', 'PLL_Reset', 'LOCK_EN', 'Clock_Enable_Ports', 'CLKOUT2_EN')):
            raise ValueError('DDR adapter requires general-mode PLL with reset, lock, clock enables and CLKOUT2')
        gpio = config['gpio']
        bits = list(range(0, len(gpio['leds'])))
        bits += list(range(gpio['button_bit_offset'], gpio['button_bit_offset'] + len(gpio['buttons'])))
        bits.append(gpio['inject_bit'])
        if len(bits) != len(set(bits)) or gpio['switches']:
            raise ValueError('Invalid AE350 GPIO allocation')
    else:
        features = config['rtl']['features']
        for child, parent in (('filesystem', 'sd'), ('mic_stereo', 'mic'), ('eth', 'flash'), ('eth_dma', 'eth')):
            if features[child] and not features[parent]:
                raise ValueError('%s requires %s' % (child, parent))
        if features['sd'] != (config['rtl']['sd_profile'] != 'none'):
            raise ValueError('SD feature and sd_profile disagree')
        if config['boot']['mode'] == 'xip' and not features['flash']:
            raise ValueError('XIP requires Flash')


def source_inputs(machine, root=ROOT):
    board, _ = load_machine(machine, root)
    return (*SCHEMA_INPUTS, 'gateware/machines/%s/machine.json' % machine,
            'gateware/boards/%s/board.json' % board['id'])


@dataclass(frozen=True)
class SignalDescription:
    pins: Tuple[str, ...]
    io_standard: str
    active_low: Optional[bool] = None
    bank_vccio: Optional[float] = None
    drive_ma: Optional[int] = None
    pull: Optional[str] = None
    vref: Optional[str] = None
    drive_overrides: Optional[Mapping[str, int]] = None


@dataclass(frozen=True)
class ResourceDescription:
    kind: str
    signals: Mapping[str, SignalDescription]


@dataclass(frozen=True)
class FPGADescription:
    device: str
    part: str


@dataclass(frozen=True)
class BoardDescription:
    id: str
    fpga: FPGADescription
    input_clock_hz: int
    resources: Mapping[str, ResourceDescription]
    reserved_pins: Tuple[str, ...]


@dataclass(frozen=True)
class CPUDescription:
    kind: str
    implementation: str
    abi: str


@dataclass(frozen=True)
class ClockDescription:
    sys: int
    video: int
    cpu: Optional[int] = None
    data_memory_bus: Optional[int] = None


@dataclass(frozen=True)
class MemoryDescription:
    resource: str
    controller: str
    base: int
    size_bytes: int


@dataclass(frozen=True)
class BootDescription:
    mode: str
    rom_size_bytes: int


@dataclass(frozen=True)
class DeviceDescription:
    video: str
    audio: str
    microphones: Tuple[str, ...]
    ws2812: str
    sd: Optional[str] = None
    fan: Optional[str] = None
    cpu_jtag: Optional[str] = None
    flash: Optional[str] = None
    i2c: Optional[str] = None
    twi_mux: Optional[str] = None
    ethernet: Optional[str] = None
    ethernet_clock: Optional[str] = None
    usb: Optional[str] = None
    spi_lcd: Optional[str] = None
    phy_reset: Optional[str] = None


@dataclass(frozen=True)
class GPIODescription:
    leds: Tuple[str, ...]
    buttons: Tuple[str, ...]
    switches: Tuple[str, ...]
    debounce_ms: int
    button_bit_offset: int = 0
    inject_bit: Optional[int] = None


@dataclass(frozen=True)
class RTLDescription:
    place_option: int
    route_option: int
    features: Optional[Mapping[str, bool]] = None
    cpu_variant: Optional[str] = None
    cpu_verilog: Optional[str] = None
    sd_profile: Optional[str] = None
    usb_backend: Optional[str] = None
    audio_clock: Optional[str] = None
    l2_size_bytes: Optional[int] = None
    rtl_hierarchy: Optional[str] = None

    def implementation_options(self):
        return dict(place_option=self.place_option, route_option=self.route_option)


@dataclass(frozen=True)
class IPDescription:
    generator: str
    module: str
    parameters: Mapping


@dataclass(frozen=True)
class MachineDescription:
    id: str
    board: BoardDescription
    cpu: CPUDescription
    clocks: ClockDescription
    memory: MemoryDescription
    boot: BootDescription
    devices: DeviceDescription
    gpio: GPIODescription
    rtl: RTLDescription
    ip: Mapping[str, IPDescription]
    artifacts: Mapping
    snapshot: bytes


def resolve_machine(machine, root=ROOT):
    """The public hardware API. Raw JSON dictionaries stay in the codec/editor."""
    board, config = load_machine(machine, root)
    return from_data(board, config)


def from_snapshot(snapshot):
    data = json.loads(snapshot) if isinstance(snapshot, (bytes, str)) else snapshot
    return from_data(data['board'], data['machine'], data.get('artifacts', {}))


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def from_data(board, config, artifacts=None):
    artifacts = artifacts or {}
    validate_pair(board, config)
    resources = {}
    for ref, resource in board['resources'].items():
        signals = {}
        for name, raw in resource['signals'].items():
            values = {**raw, 'pins': tuple(raw['pins'])}
            if 'drive_overrides' in values:
                values['drive_overrides'] = _freeze(values['drive_overrides'])
            signals[name] = SignalDescription(**values)
        resources[ref] = ResourceDescription(resource['kind'], MappingProxyType(signals))
    physical = BoardDescription(board['id'], FPGADescription(**board['fpga']),
        board['input_clock_hz'], MappingProxyType(resources), tuple(board['reserved_pins']))
    devices = {**config['devices'], 'microphones': tuple(config['devices']['microphones'])}
    gpio = {**config['gpio'], **{key: tuple(config['gpio'][key]) for key in ('leds', 'buttons', 'switches')}}
    rtl = dict(config['rtl'])
    if 'features' in rtl:
        rtl['features'] = MappingProxyType(rtl['features'])
    if config['cpu']['kind'] == 'soft':
        cpu_ip = config['ip']['cpu']['parameters']
        rtl['features'] = MappingProxyType({**dict(rtl['features']), 'mmu': cpu_ip['mmu'], 'fpu': cpu_ip['fpu']})
        rtl['cpu_variant'] = 'linux' if cpu_ip['mmu'] else 'full'
        rtl['cpu_verilog'] = str(Path(artifacts['cpu']['directory']) / artifacts['cpu']['primary']) if 'cpu' in artifacts else None
    return MachineDescription(config['id'], physical, CPUDescription(**config['cpu']),
        ClockDescription(**config['clocks']), MemoryDescription(**config['memory']),
        BootDescription(**config['boot']), DeviceDescription(**devices),
        GPIODescription(**gpio), RTLDescription(**rtl),
        MappingProxyType({name: IPDescription(value['generator'], value['module'],
                          _freeze(value['parameters'])) for name, value in config['ip'].items()}),
        _freeze(artifacts),
        json.dumps(dict(board=board, machine=config, artifacts=artifacts), sort_keys=True).encode('utf-8'))
