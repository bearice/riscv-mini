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
FLAG_PRESENT = 1
FLAG_DATA = 2
FLAG_DMA = 4
FLAG_IRQ = 8
SYSINFO_CAP = CAP_LOOPBACK | CAP_EXT_IRQ | CAP_DMA_REQ | CAP_DMAC_DECLARED
DMA_SVC_CAP = CAP_LOOPBACK | CAP_DMA_REQ
REQ_MAP = 0x00000100  # TX uses DMA_REQ[0], RX uses DMA_REQ[1]
LOOPBACK_EP = (
    LOOPBACK_ID, DMA_SVC_BASE, 0x1000, LOOPBACK_DATA, 4, ABI_VERSION,
    0 | (0 << 8) | ((FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16), 0)
DMAC_EP = (
    DMAC_ID, DMAC_BASE, 0, 0, 0, 0,
    0xff | (0xff << 8) | (FLAG_PRESENT << 16), 0)
STATUS_ADDR = DDR_CSR_BASE
CALIB_CYCLES_ADDR = DDR_CSR_BASE + 4
READ_COUNT_ADDR = DDR_CSR_BASE + 0x18
WRITE_COUNT_ADDR = DDR_CSR_BASE + 0x14
TRACE_ADDR = DDR_CSR_BASE + 0x10
WRITE_TRACE_ADDR = DDR_CSR_BASE + 0x1c
READ_TRACE_ADDR = DDR_CSR_BASE + 0x40
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
    ('ext-ahb', EXT_AHB_BASE, EXT_AHB_SIZE, 'loopback-data-word'),
)
SUBRANGES = (
    ('ilm.code', ILM_BASE, ILM_CODE_BYTES),
    ('dlm.private', DLM_BASE, DLM_DATA_BYTES),
    ('dlm.results', RESULTS_BASE, RESULTS_BYTES),
    ('dlm.params', LOCAL_PARAMS, LOCAL_PARAMS_BYTES),
    ('dlm.payload-stack', PAYLOAD_STACK_BOTTOM, PAYLOAD_STACK_TOP - PAYLOAD_STACK_BOTTOM),
    ('dlm.rom-stack', ROM_STACK_BOTTOM, ROM_STACK_TOP - ROM_STACK_BOTTOM),
)


def image_abi():
    import json
    import zlib
    interface = dict(cpu='ae350-rv32imac', image_version=1, load=DDR_CODE,
        memories=WINDOWS, reserved=SUBRANGES,
        registers=dict(ddr_status=STATUS_ADDR, calib_cycles=CALIB_CYCLES_ADDR,
                       reads=READ_COUNT_ADDR, writes=WRITE_COUNT_ADDR,
                       write_trace=WRITE_TRACE_ADDR, read_trace=READ_TRACE_ADDR),
        loader=dict(max_bytes=DDR_CODE_MAX, stack_top=PAYLOAD_STACK_TOP),
        uart=dict(base=0xf0300000, baud=115200),
        capabilities=dict(atomics='none', cache='write-back', loader='ae350-20k-frame-v1',
                          dmac='exported-unproven', ext_ahb='loopback', fabric_reader='unsupported'))
    return zlib.crc32(json.dumps(interface, sort_keys=True, separators=(',', ':')).encode())


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
                       ('dma-service', DMA_SVC_BASE)):
        if not CSR_BASE <= base < base + 0x1000 <= CSR_BASE + CSR_SIZE:
            errors.append(name + ' page escapes the platform CSR window')
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
