"""AE350 memory address/ownership contract; generates C and linker inputs."""
DDR_BASE = 0
DDR_SIZE = 1 << 30
# Peripheral AHB/APB and the vendor ITCM. 100 MHz sys failed STA on
# 2026-10-20: sys Fmax 84.8 MHz, and u_vc_ilm_ram high pulse was 0.221 ns
# against a 0.900 ns requirement. 80 MHz is the passing point.
FABRIC_HZ = 80_000_000
# Delivered data-port point is 100 MHz. The 133.333 MHz configuration that
# was tried failed both minimum pulse width and setup under full STA. That
# report does not establish a device or global-clock physical limit.
DATA_HZ = 100_000_000
ROM_BASE = 0x80000000
ROM_SIZE = 32768
ROM_CODE_BYTES = ROM_SIZE
ILM_BASE = 0xA0000000
ILM_SIZE = 64 << 10
ILM_CODE_BYTES = ILM_SIZE
DLM_BASE = 0xA0200000
DLM_SIZE = 64 << 10
DLM_DATA_BYTES = 0x8000
RESULTS_BASE = DLM_BASE + 0x8000
RESULTS_BYTES = 0x600
LOCAL_PARAMS = DLM_BASE + 0x8600
LOCAL_PARAMS_BYTES = 0x100
PAYLOAD_STACK_BOTTOM = DLM_BASE + 0xA000
PAYLOAD_STACK_TOP = DLM_BASE + 0xE000
ROM_STACK_BOTTOM = PAYLOAD_STACK_TOP
ROM_STACK_TOP = DLM_BASE + DLM_SIZE
CSR_BASE = 0xF8000000
CSR_SIZE = 0x10000
SYSINFO_BASE = CSR_BASE
IRQ_BASE = CSR_BASE + 0x2000
DDR_CSR_BASE = CSR_BASE + 0x3000  # platform DDR diagnostics page, not SYSINFO
DMA_SVC_BASE = CSR_BASE + 0x4000
SENSE_BASE = CSR_BASE + 0x7000  # fan PWM and die temperature; first 4 KiB only
# Peripheral slots from the address contract. Only the first 4 KiB of each
# 64 KiB reservation is implemented; the rest of the extended APB window
# still returns an error.
WS2812_BASE = 0xF8070000
IO_BASE = 0xF8080000
AUDIO_BASE = 0xF8010000
MIC_BASE = 0xF8020000
AUDIO_DATA = 0xE8000000
MIC_DATA = 0xE8010000
AUDIO_REQ = 2
MIC_REQ = 3
# The encrypted AE350 mux is not identity-mapped: port REQ[0:1] use
# selectors 0:1, but REQ[2:3] use 9:10. Qualified by gated board transfers.
AUDIO_DMA_SEL = 9
MIC_DMA_SEL = 10
EXT_INT_AUDIO = 2
EXT_INT_MIC = 3
AUDIO_ID = 0xAE350401
MIC_ID = 0xAE350402
AUDIO_CAP = 7  # PIO, handshake DMA, PCM16 stereo
MIC_CAP = 7  # snapshot, stream DMA, signed PCM24 in 32-bit words
GPIO_BASE = 0xF0700000
PLIC_BASE = 0xE4000000
PLIC_CLAIM = PLIC_BASE + 0x200004
PLIC_ENABLE = PLIC_BASE + 0x2000
PLIC_PENDING = PLIC_BASE + 0x1000
PLIC_THRESHOLD = PLIC_BASE + 0x200000
# Andes ATCGPIO100 product field in IDREV[31:12]. A different value is a
# failed identity check, not a silent accept.
GPIO_ID_PRODUCT = 0x02031
# Andes AE350 BSP numbers GPIO as PLIC source 7. The fabric wire is
# EXT_INT[7]; its PLIC source is unpublished until a board claim records it.
PLIC_GPIO_SOURCE = 7
PLIC_FABRIC_IO_SOURCE = 0
EXT_INT_IO = 7
EXT_AHB_BASE = 0xE8000000
EXT_AHB_SIZE = 128 << 20
LOOPBACK_DATA = 0xE8050000
LOOPBACK_DEPTH = 16
DMAC_BASE = 0xF0C00000
# MUG1031 documents the DMAC and the 8 external REQ/ACK pairs, not the
# register map or the PLIC source number of EXT_INT[0]. The hard DMAC can
# master external DDR; these IDs publish the fabric that exists.
ABI_VERSION = 0x00010000
SYSINFO_ID = 0xAE350001
IRQ_ID = 0xAE350002
DMA_SVC_ID = 0xAE350003
LOOPBACK_ID = 0xAE350101
DMAC_ID = 0xAE350201
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
FLAG_PRESENT = 1
FLAG_DATA = 2
FLAG_DMA = 4
FLAG_IRQ = 8
SYSINFO_CAP = (CAP_LOOPBACK | CAP_EXT_IRQ | CAP_DMA_REQ | CAP_DMAC_DECLARED |
               CAP_BOARD_IO | CAP_WS2812 | CAP_SENSE | CAP_AUDIO | CAP_MIC)
WS2812_ID = 0xAE350301
IO_ID = 0xAE350302
WS2812_CAP = 3  # wired controller present
IO_CAP = 7  # debounced events, diagnostic edge, GPIO inject bit
SENSE_ID = 0xAE350303
SENSE_CAP = 3  # fan PWM duty and one die-temperature sample
# Counts, bit masks and the inject bit are the machine description, not a
# second copy of the pin list. ae350_map.require() refuses a description
# that does not match these generated values.
def _gpio_contract():
    from gateware.boards.mega138k_pro import io_map
    io_map.require()
    return io_map.gpio_binding()


_GPIO = _gpio_contract()
GPIO_LED_COUNT = _GPIO['led_count']
GPIO_BUTTON_COUNT = _GPIO['button_count']
GPIO_LED_BIT = _GPIO['led_bit']
GPIO_BUTTON_BIT = _GPIO['button_bit']
GPIO_INJECT_BIT = _GPIO['inject_bit']
GPIO_LED_MASK = _GPIO['led_mask']
GPIO_BUTTON_MASK = _GPIO['button_mask']
GPIO_IRQ_MASK = _GPIO['irq_mask']
# Synchronous exceptions are recorded here, outside the 32-word suite block.
# An interrupt that returns with mret leaves mcause intact; this record stays 0.
TRAP_RECORD = 0xA0
TRAP_RECORD_WORDS = 5
ERR_BUSY = 1
DMA_SVC_CAP = CAP_LOOPBACK | CAP_DMA_REQ
REQ_MAP = 0x00000100  # TX uses DMA_REQ[0], RX uses DMA_REQ[1]
LOOPBACK_EP = (
    LOOPBACK_ID, DMA_SVC_BASE, 0x1000, LOOPBACK_DATA, 4, ABI_VERSION,
    0 | (0 << 8) | ((FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16), 0)
DMAC_EP = (
    DMAC_ID, DMAC_BASE, 0, 0, 0, 0,
    0xff | (0xff << 8) | (FLAG_PRESENT << 16), 0)
WS2812_EP = (
    WS2812_ID, WS2812_BASE, 0x1000, 0, 0, ABI_VERSION,
    0 | (0 << 8) | ((FLAG_PRESENT | FLAG_IRQ) << 16), 0)
IO_EP = (
    IO_ID, IO_BASE, 0x1000, 0, 0, ABI_VERSION,
    EXT_INT_IO | (0 << 8) | ((FLAG_PRESENT | FLAG_IRQ) << 16), 0)
SENSE_EP = (
    SENSE_ID, SENSE_BASE, 0x1000, 0, 0, ABI_VERSION,
    0 | (0 << 8) | (FLAG_PRESENT << 16), 0)
AUDIO_EP = (
    AUDIO_ID, AUDIO_BASE, 0x1000, AUDIO_DATA, 4, ABI_VERSION,
    EXT_INT_AUDIO | (AUDIO_REQ << 8) | ((FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16), AUDIO_DMA_SEL)
MIC_EP = (
    MIC_ID, MIC_BASE, 0x1000, MIC_DATA, 4, ABI_VERSION,
    EXT_INT_MIC | (MIC_REQ << 8) | ((FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16), MIC_DMA_SEL)
DEVICE_COUNT = 7
STATUS_ADDR = DDR_CSR_BASE
CALIB_CYCLES_ADDR = DDR_CSR_BASE + 4
READ_COUNT_ADDR = DDR_CSR_BASE + 0x18
WRITE_COUNT_ADDR = DDR_CSR_BASE + 0x14
TRACE_ADDR = DDR_CSR_BASE + 0x10
WRITE_TRACE_ADDR = DDR_CSR_BASE + 0x1c
READ_TRACE_ADDR = DDR_CSR_BASE + 0x40
# Mem-domain counters. Not part of image_abi(); the offsets above stay put.
RLINE_HIT_ADDR = DDR_CSR_BASE + 0x44
WAIT_CYCLES_ADDR = DDR_CSR_BASE + 0x48
XIP_BASE = 0x88000000
XIP_SIZE = 128 << 20
DDR_CODE = 0x3000
DDR_CODE_MAX = 0x7D000
WINDOWS = (
    ('ddr', DDR_BASE, DDR_SIZE, 'implemented'),
    ('boot-itcm', ROM_BASE, ROM_SIZE, 'vendor-fabric-ITCM'),
    ('xip', XIP_BASE, XIP_SIZE, 'reserved-not-implemented'),
    ('ilm', ILM_BASE, ILM_SIZE, 'hard-local-memory'),
    ('dlm', DLM_BASE, DLM_SIZE, 'hard-local-memory'),
    ('platform-csr', CSR_BASE, CSR_SIZE, 'implemented'),
    ('periph-csr', 0xF8010000, 0x80000, 'audio-mic-ws2812-board-io'),
    ('ext-ahb', EXT_AHB_BASE, EXT_AHB_SIZE, 'audio-mic-loopback-data'),
)
SUBRANGES = (
    ('ilm.code', ILM_BASE, ILM_CODE_BYTES),
    ('dlm.private', DLM_BASE, DLM_DATA_BYTES),
    ('dlm.results', RESULTS_BASE, RESULTS_BYTES),
    ('dlm.params', LOCAL_PARAMS, LOCAL_PARAMS_BYTES),
    ('dlm.payload-stack', PAYLOAD_STACK_BOTTOM, PAYLOAD_STACK_TOP - PAYLOAD_STACK_BOTTOM),
    ('dlm.rom-stack', ROM_STACK_BOTTOM, ROM_STACK_TOP - ROM_STACK_BOTTOM),
)


def interface():
    from gateware.boards.mega138k_pro import io_map
    binding = io_map.gpio_binding()
    return dict(cpu='ae350-rv32imac', image_version=1, load=DDR_CODE,
        audio=dict(pins=io_map.audio_binding(), sample_rate=48000,
            tx=dict(csr=AUDIO_BASE, data=AUDIO_DATA, irq=EXT_INT_AUDIO, request=AUDIO_REQ,
                    selector=AUDIO_DMA_SEL, pcm='stereo-s16-le', abi=ABI_VERSION),
            rx=dict(csr=MIC_BASE, data=MIC_DATA, irq=EXT_INT_MIC, request=MIC_REQ,
                    selector=MIC_DMA_SEL, pcm='mono-s24-in-s32-le', abi=ABI_VERSION)),
        memories=WINDOWS, reserved=SUBRANGES,
        registers=dict(ddr_status=STATUS_ADDR, calib_cycles=CALIB_CYCLES_ADDR,
                       reads=READ_COUNT_ADDR, writes=WRITE_COUNT_ADDR,
                       write_trace=WRITE_TRACE_ADDR, read_trace=READ_TRACE_ADDR,
                       gpio=GPIO_BASE, ws2812=WS2812_BASE, board_io=IO_BASE,
                       sense=SENSE_BASE, plic=PLIC_BASE),
        gpio=dict(leds=list(binding['led_bits']), buttons=list(binding['button_bits']),
                  inject=binding['inject_bit'], irq_mask=binding['irq_mask'],
                  led_active_low=[int(item) for item in binding['led_active_low']],
                  button_active_low=[int(item) for item in binding['button_active_low']]),
        loader=dict(max_bytes=DDR_CODE_MAX, stack_top=PAYLOAD_STACK_TOP),
        uart=dict(base=0xf0300000, baud=115200),
        capabilities=dict(atomics='none', cache='write-back', loader='ae350-20k-frame-v1',
                          dmac='exported-unproven', ext_ahb='loopback', fabric_reader='unsupported',
                          board_io='led-register-and-hard-gpio-inputs', ws2812='shared-core',
                          fan='pwm-percent-25khz', temperature='die-quarter-degree'))


def image_abi():
    import json
    import zlib
    return zlib.crc32(json.dumps(interface(), sort_keys=True, separators=(',', ':')).encode())


def check():
    errors = []
    for spans in ([(n, b, s) for n, b, s, _ in WINDOWS], SUBRANGES):
        spans = sorted(spans, key=lambda span: span[1])
        for name, base, size in spans:
            if size <= 0 or base % 4 or base + size > 1 << 32:
                errors.append('invalid range: ' + name)
        for left, right in zip(spans, spans[1:]):
            if left[1] + left[2] > right[1]:
                errors.append('%s overlaps %s' % (left[0], right[0]))
    for name, base, size in SUBRANGES:
        parent, length = (ILM_BASE, ILM_SIZE) if name.startswith('ilm.') else (DLM_BASE, DLM_SIZE)
        if not parent <= base < base + size <= parent + length:
            errors.append('local subrange escapes memory: ' + name)
    if DDR_BASE != 0 or DDR_CODE + DDR_CODE_MAX > DDR_SIZE:
        errors.append('invalid DDR loader layout')
    if not 0 < DATA_HZ < 200_000_000:
        errors.append('AE350 data clock must be below 200MHz')
    if not EXT_AHB_BASE <= LOOPBACK_DATA < LOOPBACK_DATA + 4 <= EXT_AHB_BASE + EXT_AHB_SIZE:
        errors.append('loopback data word escapes the extended AHB window')
    for name, base in (('sysinfo', SYSINFO_BASE), ('irq', IRQ_BASE),
                       ('dma-service', DMA_SVC_BASE), ('sense', SENSE_BASE)):
        if not CSR_BASE <= base < base + 0x1000 <= CSR_BASE + CSR_SIZE:
            errors.append(name + ' page escapes the platform CSR window')
    periph_base, periph_size = 0xF8010000, 0x80000
    for name, base in (('ws2812', WS2812_BASE), ('board-io', IO_BASE)):
        if not periph_base <= base < base + 0x1000 <= periph_base + periph_size:
            errors.append(name + ' page escapes the peripheral CSR window')
    from gateware.boards.mega138k_pro import io_map
    errors.extend(io_map.check())
    binding = io_map.gpio_binding()
    expected = {
        'led count': (GPIO_LED_COUNT, binding['led_count']),
        'button count': (GPIO_BUTTON_COUNT, binding['button_count']),
        'led bit': (GPIO_LED_BIT, binding['led_bit']),
        'button bit': (GPIO_BUTTON_BIT, binding['button_bit']),
        'inject bit': (GPIO_INJECT_BIT, binding['inject_bit']),
        'led mask': (GPIO_LED_MASK, binding['led_mask']),
        'button mask': (GPIO_BUTTON_MASK, binding['button_mask']),
        'irq mask': (GPIO_IRQ_MASK, binding['irq_mask']),
    }
    for name, (have, want) in expected.items():
        if have != want:
            errors.append('%s does not match board.json' % name)
    described = interface()['gpio']
    if described['leds'] != list(binding['led_bits']) or described['buttons'] != list(binding['button_bits']):
        errors.append('image ABI gpio binding does not match board.json')
    if described['led_active_low'] != [int(item) for item in binding['led_active_low']]:
        errors.append('image ABI led polarity does not match board.json')
    if described['button_active_low'] != [int(item) for item in binding['button_active_low']]:
        errors.append('image ABI button polarity does not match board.json')
    return errors


def require():
    errors = check()
    if errors:
        raise ValueError('AE350 address map: ' + '; '.join(errors))


def c_header():
    require()
    constants = {name: value for name, value in globals().items()
                 if name.isupper() and isinstance(value, int)}
    return ('#pragma once\n/* Generated from ae350_map.py. */\n' +
        ''.join('#define AE350_%s 0x%xu\n' % item for item in sorted(constants.items())) +
        '#define AE350_IMAGE_ABI 0x%08xu\n' % image_abi())


def ld_fragment():
    require()
    return '\n'.join([
        '/* Generated from ae350_map.py. */', 'MEMORY {',
        ' rom (rx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (ROM_BASE, ROM_SIZE),
        ' ilm (rwx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (ILM_BASE, ILM_CODE_BYTES),
        ' dlm (rw) : ORIGIN = 0x%x, LENGTH = 0x%x' % (DLM_BASE, DLM_DATA_BYTES),
        ' ddr (rwx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (DDR_CODE, DDR_CODE_MAX), '}',
        '__ilm_base = 0x%x;' % ILM_BASE, '__ilm_limit = 0x%x;' % (ILM_BASE + ILM_SIZE),
        '__dlm_base = 0x%x;' % DLM_BASE, '__dlm_limit = 0x%x;' % (DLM_BASE + DLM_SIZE),
        '__local_stack_bottom = 0x%x;' % ROM_STACK_BOTTOM,
        '__local_stack_top = 0x%x;' % ROM_STACK_TOP,
        '__payload_stack_bottom = 0x%x;' % PAYLOAD_STACK_BOTTOM,
        '__payload_stack_top = 0x%x;' % PAYLOAD_STACK_TOP,
    ]) + '\n'
