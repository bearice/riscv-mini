"""AE350 register definitions and pure, explicitly instantiated machine layout."""
from gateware.io import IOBindings
from dataclasses import asdict
from gateware.machines.mega138k_ae350.video_modes import MODES, pll_config

ROM_BASE = 2147483648

BOOT_CONTRACT_ADDR = 0xf800000c
BOOT_CLOCK_ADDR = 0xf8000010
BOOT_IMAGE_ABI_ADDR = 0xf8000014
BOOT_CONTRACT_VERSION = 1

FLASH_CTRL_BASE = 0xf8090000
FLASH_CTRL_SIZE = 0x80

ILM_BASE = 2684354560

ILM_SIZE = 64 << 10

ILM_CODE_BYTES = ILM_SIZE

DLM_BASE = 2686451712

DLM_SIZE = 64 << 10

DLM_DATA_BYTES = 32768

RESULTS_BASE = DLM_BASE + 32768

RESULTS_BYTES = 1536

LOCAL_PARAMS = DLM_BASE + 34304

LOCAL_PARAMS_BYTES = 256

PAYLOAD_STACK_BOTTOM = DLM_BASE + 40960

PAYLOAD_STACK_TOP = DLM_BASE + 57344

ROM_STACK_BOTTOM = PAYLOAD_STACK_TOP

ROM_STACK_TOP = DLM_BASE + DLM_SIZE

CSR_BASE = 4160749568

CSR_SIZE = 65536

SYSINFO_BASE = CSR_BASE

IRQ_BASE = CSR_BASE + 8192

DDR_CSR_BASE = CSR_BASE + 12288

DMA_SVC_BASE = CSR_BASE + 16384

SENSE_BASE = CSR_BASE + 28672

WS2812_BASE = 4161208320

IO_BASE = 4161273856

AUDIO_BASE = 4160815104

MIC_BASE = 4160880640

SPI_BASE = 0xf8050000
I2C_BASE = 0xf0a00000
SPI_ID = 0xae350b01
I2C_ID = 0xae350a01
TWI_MUX_GPIO_SHIFT = 20
TWI_MUX_GPIO_MASK = 7 << TWI_MUX_GPIO_SHIFT

SD_BASE = 0xf8040000

SD_ID = 0xae350801

SD_CAP = 7  # native 1/4-bit, hard DMAC FIFO, card detect

SD_TX_DATA = 0xe8020000
SD_RX_DATA = 0xe8020004
SD_TX_REQ = 4
SD_RX_REQ = 5
SD_TX_DMA_SEL = 11
SD_RX_DMA_SEL = 12

VIDEO_BASE = 4160946176

VIDEO_ID = 2922710273

VIDEO_CAP = 15 | 0x1f0  # RGB888, XRGB8888, runtime modes, HPD, HPD IRQ

VIDEO_REGS = dict(CONTROL=12, STATUS=16, IRQ_STATUS=20, IRQ_ENABLE=24, LAST_ERROR=28, BASE0=32, BASE1=36, STRIDE=40, COMMIT=44, FRAMES=48, UNDERFLOWS=52, ADDRESS_ERROR=56, TIMEOUT_ERROR=60, ACTIVE_BASE=64, PIXEL_HZ=68, GEOMETRY=72, EPOCH=76, FIFO_MIN=80, MAX_WAIT=84, READS=88, CPU_COMMANDS=92, MISSING=96, SEQUENCE=100, ACTIVE_SEQUENCE=104, CANCELLED=108, ERRORS=112, CLEAR_ERRORS=116, REQUEST_OVERFLOW=120, MODE=124, FORMAT=128, MODE_CONFIG=132, MODE_STATUS=136, MODE_COUNT=140, ENABLE=144, HPD=148, MODE_INFO_BASE=256)

AUDIO_DATA = 3892314112

MIC_DATA = 3892379648

AUDIO_REQ = 2

MIC_REQ = 3

AUDIO_DMA_SEL = 9

MIC_DMA_SEL = 10

EXT_INT_AUDIO = 2

EXT_INT_MIC = 3

EXT_INT_VIDEO = 8

AUDIO_ID = 2922710017

MIC_ID = 2922710018

AUDIO_CAP = 7

MIC_CAP = 7

GPIO_BASE = 4033871872

PLIC_BASE = 3825205248

PLIC_CLAIM = PLIC_BASE + 2097156

PLIC_ENABLE = PLIC_BASE + 8192

PLIC_PENDING = PLIC_BASE + 4096

PLIC_THRESHOLD = PLIC_BASE + 2097152

GPIO_ID_PRODUCT = 8241

PLIC_GPIO_SOURCE = 7

PLIC_FABRIC_IO_SOURCE = 0

EXT_INT_IO = 7

EXT_AHB_BASE = 3892314112

EXT_AHB_SIZE = 128 << 20

LOOPBACK_DATA = 3892641792

LOOPBACK_DEPTH = 16

DMAC_BASE = 4039114752

ABI_VERSION = 65536

SYSINFO_ID = 2922708993

IRQ_ID = 2922708994

DMA_SVC_ID = 2922708995

LOOPBACK_ID = 2922709249

DMAC_ID = 2922709505

CAP_LOOPBACK = 1

CAP_EXT_IRQ = 2

CAP_DMA_REQ = 4

CAP_FABRIC_READER = 8

CAP_DMAC_DECLARED = 16

CAP_BOARD_IO = 32

CAP_WS2812 = 64

CAP_SENSE = 128

CAP_AUDIO = 256

CAP_MIC = 512

CAP_VIDEO = 1024

CAP_SD = 2048
CAP_FLASH = 4096
CAP_I2C = 8192

FLAG_PRESENT = 1

FLAG_DATA = 2

FLAG_DMA = 4

FLAG_IRQ = 8

SYSINFO_CAP = CAP_LOOPBACK | CAP_EXT_IRQ | CAP_DMA_REQ | CAP_DMAC_DECLARED | CAP_BOARD_IO | CAP_WS2812 | CAP_SENSE | CAP_AUDIO | CAP_MIC | CAP_VIDEO | CAP_FABRIC_READER | CAP_SD | CAP_FLASH | CAP_I2C

WS2812_ID = 2922709761

IO_ID = 2922709762

WS2812_CAP = 3

IO_CAP = 7

SENSE_ID = 2922709763

SENSE_CAP = 3

TRAP_RECORD = 160

TRAP_RECORD_WORDS = 5

ERR_BUSY = 1

DMA_SVC_CAP = CAP_LOOPBACK | CAP_DMA_REQ

REQ_MAP = 256

LOOPBACK_EP = (LOOPBACK_ID, DMA_SVC_BASE, 4096, LOOPBACK_DATA, 4, ABI_VERSION, 0 | 0 << 8 | (FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16, 0)

DMAC_EP = (DMAC_ID, DMAC_BASE, 0, 0, 0, 0, 255 | 255 << 8 | FLAG_PRESENT << 16, 0)

WS2812_EP = (WS2812_ID, WS2812_BASE, 4096, 0, 0, ABI_VERSION, 0 | 0 << 8 | (FLAG_PRESENT | FLAG_IRQ) << 16, 0)

IO_EP = (IO_ID, IO_BASE, 4096, 0, 0, ABI_VERSION, EXT_INT_IO | 0 << 8 | (FLAG_PRESENT | FLAG_IRQ) << 16, 0)

SENSE_EP = (SENSE_ID, SENSE_BASE, 4096, 0, 0, ABI_VERSION, 0 | 0 << 8 | FLAG_PRESENT << 16, 0)

AUDIO_EP = (AUDIO_ID, AUDIO_BASE, 4096, AUDIO_DATA, 4, ABI_VERSION, EXT_INT_AUDIO | AUDIO_REQ << 8 | (FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16, AUDIO_DMA_SEL)

MIC_EP = (MIC_ID, MIC_BASE, 4096, MIC_DATA, 4, ABI_VERSION, EXT_INT_MIC | MIC_REQ << 8 | (FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16, MIC_DMA_SEL)

VIDEO_EP = (VIDEO_ID, VIDEO_BASE, 4096, 0, 0, ABI_VERSION, EXT_INT_VIDEO | (FLAG_PRESENT | FLAG_IRQ) << 16, 0)

SD_EP = (SD_ID, SD_BASE, 4096, SD_TX_DATA, 8, ABI_VERSION, (FLAG_PRESENT | FLAG_DATA | FLAG_DMA) << 16, SD_TX_DMA_SEL | (SD_RX_DMA_SEL << 8))

SPI_EP = (SPI_ID, SPI_BASE, 0x28, 0, 0, 1, FLAG_PRESENT << 16, 0)
I2C_EP = (I2C_ID, I2C_BASE, 0x30, 0, 0, 0, FLAG_PRESENT << 16, 0)

DEVICE_COUNT = 11

STATUS_ADDR = DDR_CSR_BASE

CALIB_CYCLES_ADDR = DDR_CSR_BASE + 4

READ_COUNT_ADDR = DDR_CSR_BASE + 24

WRITE_COUNT_ADDR = DDR_CSR_BASE + 20

TRACE_ADDR = DDR_CSR_BASE + 16

WRITE_TRACE_ADDR = DDR_CSR_BASE + 28

READ_TRACE_ADDR = DDR_CSR_BASE + 64

RLINE_HIT_ADDR = DDR_CSR_BASE + 68

WAIT_CYCLES_ADDR = DDR_CSR_BASE + 72

XIP_BASE = ROM_BASE

XIP_SIZE = 256 << 20

DDR_CODE = 12288

DDR_CODE_MAX = 512000

SUBRANGES = (('ilm.code', ILM_BASE, ILM_CODE_BYTES), ('dlm.private', DLM_BASE, DLM_DATA_BYTES), ('dlm.results', RESULTS_BASE, RESULTS_BYTES), ('dlm.params', LOCAL_PARAMS, LOCAL_PARAMS_BYTES), ('dlm.payload-stack', PAYLOAD_STACK_BOTTOM, PAYLOAD_STACK_TOP - PAYLOAD_STACK_BOTTOM), ('dlm.rom-stack', ROM_STACK_BOTTOM, ROM_STACK_TOP - ROM_STACK_BOTTOM))

class AddressMap:
    def __init__(self, machine):
        self.machine = machine
        self.io = IOBindings(machine)
        self.DDR_BASE = machine.memory.base
        self.DDR_SIZE = machine.memory.size_bytes
        self.FABRIC_HZ = machine.clocks.sys
        self.CPU_HZ = machine.clocks.cpu
        self.DATA_HZ = machine.clocks.data_memory_bus
        from gateware.machines.mega138k_ae350.boot_contract import region_size
        self.FLASH_BYTES = machine.board.resources[machine.devices.boot_flash].size_bytes
        self.ROM_SIZE = region_size(self.FLASH_BYTES)
        self.USER_FLASH_BYTES = machine.board.resources[machine.devices.flash].size_bytes
        gpio = self.io.gpio_binding()
        self.GPIO_LED_COUNT = gpio['led_count']
        self.GPIO_BUTTON_COUNT = gpio['button_count']
        self.GPIO_LED_BIT = gpio['led_bit']
        self.GPIO_BUTTON_BIT = gpio['button_bit']
        self.GPIO_INJECT_BIT = gpio['inject_bit']
        self.GPIO_LED_MASK = gpio['led_mask']
        self.GPIO_BUTTON_MASK = gpio['button_mask']
        self.GPIO_IRQ_MASK = gpio['irq_mask']
        self.ROM_CODE_BYTES = self.ROM_SIZE
        self.WINDOWS = (('ddr', self.DDR_BASE, self.DDR_SIZE, 'implemented'), ('boot-xip', self.ROM_BASE, self.ROM_SIZE, 'SDK-Flash-U4-offset-0x600000'), ('flash-control', self.FLASH_CTRL_BASE, 4096, 'SDK-FR-APB'), ('sdk-flash-rest', self.ROM_BASE + self.ROM_SIZE, self.XIP_SIZE - self.ROM_SIZE, 'SDK-aperture-capacity-bounds-unqualified'), ('ilm', self.ILM_BASE, self.ILM_SIZE, 'hard-local-memory'), ('dlm', self.DLM_BASE, self.DLM_SIZE, 'hard-local-memory'), ('platform-csr', self.CSR_BASE, self.CSR_SIZE, 'implemented'), ('periph-csr', 4160815104, 524288, 'audio-mic-video-ws2812-board-io'), ('ext-ahb', self.EXT_AHB_BASE, self.EXT_AHB_SIZE, 'audio-mic-loopback-data'))

    def __getattr__(self, name):
        if name.isupper() and name in globals():
            return globals()[name]
        raise AttributeError(name)

    def constants(self):
        return {**{name:value for name,value in globals().items() if name.isupper()},
                **{name:value for name,value in vars(self).items() if name.isupper()}}

    def interface(self):
        binding = self.io.gpio_binding()
        return dict(cpu='ae350-rv32imac', image_version=1, load=self.DDR_CODE, management=dict(spi=dict(csr=self.SPI_BASE,backend='fabric-QSPI',register_abi=1,data_width=4,clock_hz=self.FABRIC_HZ//2,size_bytes=self.USER_FLASH_BYTES,flash='user-U7',pins={n:list(v.pins) for n,v in self.machine.board.resources[self.machine.devices.flash].signals.items()}),i2c=dict(csr=self.I2C_BASE,backend='ATCIIC100',pins={n:list(v.pins) for n,v in self.machine.board.resources[self.machine.devices.i2c].signals.items()},mux=dict(pins=list(self.machine.board.resources[self.machine.devices.twi_mux].signals['sel'].pins),gpio_shift=self.TWI_MUX_GPIO_SHIFT,reset=0))), sd=dict(csr=self.SD_BASE, abi=self.ABI_VERSION, transport='litesdcard-native-4bit', clock_domain='mem', default_hz=25000000, clock_hz=self.DATA_HZ, cdc='retained-APB-AHB-REQ-ACK', dma='hard-DMAC-AHB-FIFO', tx=dict(data=self.SD_TX_DATA,request=self.SD_TX_REQ,selector=self.SD_TX_DMA_SEL), rx=dict(data=self.SD_RX_DATA,request=self.SD_RX_REQ,selector=self.SD_RX_DMA_SEL), pins={name:list(sig.pins) for name,sig in self.machine.board.resources[self.machine.devices.sd].signals.items()}), video=dict(csr=self.VIDEO_BASE, width=640, height=480, format='RGB565', formats=['RGB565','RGB888','XRGB8888'], modes=[dict(id=i,**asdict(m),actual_pixel_hz=pll_config(m.pixel_hz)['actual_hz']) for i,m in enumerate(MODES)], runtime_modes=True, irq=self.EXT_INT_VIDEO, hpd_irq='sticky-W1C-connect-disconnect', pixel_requested_hz=self.machine.clocks.video, serializer_requested_hz=self.machine.clocks.video * 5, pins=self.io.video_binding(), commit='vblank-snapshot', memory='round-robin-single-outstanding-DDR3-UI', abi=self.ABI_VERSION, registers=self.VIDEO_REGS), audio=dict(pins=self.io.audio_binding(), sample_rate=48000, tx=dict(csr=self.AUDIO_BASE, data=self.AUDIO_DATA, irq=self.EXT_INT_AUDIO, request=self.AUDIO_REQ, selector=self.AUDIO_DMA_SEL, pcm='stereo-s16-le', abi=self.ABI_VERSION), rx=dict(csr=self.MIC_BASE, data=self.MIC_DATA, irq=self.EXT_INT_MIC, request=self.MIC_REQ, selector=self.MIC_DMA_SEL, pcm='mono-s24-in-s32-le', abi=self.ABI_VERSION)), memories=self.WINDOWS, reserved=self.SUBRANGES, registers=dict(ddr_status=self.STATUS_ADDR, calib_cycles=self.CALIB_CYCLES_ADDR, reads=self.READ_COUNT_ADDR, writes=self.WRITE_COUNT_ADDR, write_trace=self.WRITE_TRACE_ADDR, read_trace=self.READ_TRACE_ADDR, gpio=self.GPIO_BASE, ws2812=self.WS2812_BASE, board_io=self.IO_BASE, sense=self.SENSE_BASE, plic=self.PLIC_BASE), gpio=dict(leds=list(binding['led_bits']), buttons=list(binding['button_bits']), inject=binding['inject_bit'], irq_mask=binding['irq_mask'], led_active_low=[int(item) for item in binding['led_active_low']], button_active_low=[int(item) for item in binding['button_active_low']]), loader=dict(max_bytes=self.DDR_CODE_MAX, stack_top=self.PAYLOAD_STACK_TOP), uart=dict(base=4029677568, baud=115200), capabilities=dict(atomics='none', cache='write-back', loader='ae350-20k-frame-v1', dmac='exported-unproven', ext_ahb='loopback', fabric_reader='RGB565-scanout', board_io='led-register-and-hard-gpio-inputs', ws2812='shared-core', fan='pwm-percent-25khz', temperature='die-quarter-degree'))

    def image_abi(self):
        import json
        import zlib
        return zlib.crc32(json.dumps(self.interface(), sort_keys=True, separators=(',', ':')).encode())

    def check(self):
        errors = []
        if len(set(self.VIDEO_REGS.values())) != len(self.VIDEO_REGS) or any((off < 12 or off >= 4096 or off % 4 for off in self.VIDEO_REGS.values())):
            errors.append('invalid video register layout')
        for spans in ([(n, b, s) for (n, b, s, _) in self.WINDOWS], self.SUBRANGES):
            spans = sorted(spans, key=lambda span: span[1])
            for (name, base, size) in spans:
                if size <= 0 or base % 4 or base + size > 1 << 32:
                    errors.append('invalid range: ' + name)
            for (left, right) in zip(spans, spans[1:]):
                if left[1] + left[2] > right[1]:
                    errors.append('%s overlaps %s' % (left[0], right[0]))
        for (name, base, size) in self.SUBRANGES:
            (parent, length) = (self.ILM_BASE, self.ILM_SIZE) if name.startswith('ilm.') else (self.DLM_BASE, self.DLM_SIZE)
            if not parent <= base < base + size <= parent + length:
                errors.append('local subrange escapes memory: ' + name)
        if self.DDR_BASE != 0 or self.DDR_CODE + self.DDR_CODE_MAX > self.DDR_SIZE:
            errors.append('invalid DDR loader layout')
        if not 0 < self.DATA_HZ < 200000000:
            errors.append('AE350 data clock must be below 200MHz')
        if not self.EXT_AHB_BASE <= self.LOOPBACK_DATA < self.LOOPBACK_DATA + 4 <= self.EXT_AHB_BASE + self.EXT_AHB_SIZE:
            errors.append('loopback data word escapes the extended AHB window')
        for (name, base) in (('sysinfo', self.SYSINFO_BASE), ('irq', self.IRQ_BASE), ('dma-service', self.DMA_SVC_BASE), ('sense', self.SENSE_BASE)):
            if not self.CSR_BASE <= base < base + 4096 <= self.CSR_BASE + self.CSR_SIZE:
                errors.append(name + ' page escapes the platform CSR window')
        (periph_base, periph_size) = (4160815104, 524288)
        for (name, base) in (('ws2812', self.WS2812_BASE), ('board-io', self.IO_BASE)):
            if not periph_base <= base < base + 4096 <= periph_base + periph_size:
                errors.append(name + ' page escapes the peripheral CSR window')
        errors.extend([])
        binding = self.io.gpio_binding()
        expected = {'led count': (self.GPIO_LED_COUNT, binding['led_count']), 'button count': (self.GPIO_BUTTON_COUNT, binding['button_count']), 'led bit': (self.GPIO_LED_BIT, binding['led_bit']), 'button bit': (self.GPIO_BUTTON_BIT, binding['button_bit']), 'inject bit': (self.GPIO_INJECT_BIT, binding['inject_bit']), 'led mask': (self.GPIO_LED_MASK, binding['led_mask']), 'button mask': (self.GPIO_BUTTON_MASK, binding['button_mask']), 'irq mask': (self.GPIO_IRQ_MASK, binding['irq_mask'])}
        for (name, (have, want)) in expected.items():
            if have != want:
                errors.append('%s does not match board.json' % name)
        described = self.interface()['gpio']
        if described['leds'] != list(binding['led_bits']) or described['buttons'] != list(binding['button_bits']):
            errors.append('image ABI gpio binding does not match board.json')
        if described['led_active_low'] != [int(item) for item in binding['led_active_low']]:
            errors.append('image ABI led polarity does not match board.json')
        if described['button_active_low'] != [int(item) for item in binding['button_active_low']]:
            errors.append('image ABI button polarity does not match board.json')
        return errors

    def require(self):
        errors = self.check()
        if errors:
            raise ValueError('AE350 address map: ' + '; '.join(errors))

    def c_header(self):
        self.require()
        constants = {name: value for (name, value) in self.constants().items() if name.isupper() and isinstance(value, int)}
        return '#pragma once\n/* Generated from ae350_map.py. */\n' + ''.join(('#define AE350_%s 0x%xu\n' % item for item in sorted(constants.items()))) + ''.join(('#define AE350_VIDEO_%s 0x%xu\n' % item for item in sorted(self.VIDEO_REGS.items()))) + '#define AE350_IMAGE_ABI 0x%08xu\n' % self.image_abi()

    def ld_fragment(self):
        self.require()
        return '\n'.join(['/* Generated from ae350_map.py. */', 'MEMORY {', ' rom (rx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.ROM_BASE, self.ROM_SIZE), ' ilm (rwx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.ILM_BASE, self.ILM_CODE_BYTES), ' dlm (rw) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.DLM_BASE, self.DLM_DATA_BYTES), ' ddr (rwx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.DDR_CODE, self.DDR_CODE_MAX), '}', '__ilm_base = 0x%x;' % self.ILM_BASE, '__ilm_limit = 0x%x;' % (self.ILM_BASE + self.ILM_SIZE), '__dlm_base = 0x%x;' % self.DLM_BASE, '__dlm_limit = 0x%x;' % (self.DLM_BASE + self.DLM_SIZE), '__local_stack_bottom = 0x%x;' % self.ROM_STACK_BOTTOM, '__local_stack_top = 0x%x;' % self.ROM_STACK_TOP, '__payload_stack_bottom = 0x%x;' % self.PAYLOAD_STACK_BOTTOM, '__payload_stack_top = 0x%x;' % self.PAYLOAD_STACK_TOP]) + '\n'
