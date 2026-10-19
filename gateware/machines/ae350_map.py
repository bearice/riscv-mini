"""Single source of truth for the AE350 platform physical address map.

Every consumer is generated from this module rather than re-declaring numbers:
the ROM/payload C code includes ``ae350_address_map.h`` and the linker scripts
``INCLUDE ae350_map.ld``; the host tools and the build metadata import the same
constants.  ``check()`` is called by the SoC construction and by the build, so a
map change either propagates or fails the build.
"""

DDR_BASE = 0x00000000
DDR_SIZE = 1 << 30                 # expected 1 GiB; published capacity is measured
FABRIC_HZ = 80_000_000             # P2 delivery clock; 100 MHz fails BSRAM MPW STA
SRAM_BASE = 0x40000000
SRAM_SIZE = 8192
ROM_BASE = 0x80000000
ROM_SIZE = 8192
XIP_BASE = 0x88000000
XIP_SIZE = 128 << 20               # address reservation only

# The ROM status/diagnostic window occupies the last 64 bytes of the ROM image.
ROM_STATUS_BYTES = 64
ROM_CODE_BYTES = ROM_SIZE - ROM_STATUS_BYTES

# SRAM sub-regions: data grows up from the base, the stack grows down from the
# top, and the fixed blocks sit in between.  Nothing may overlap.
SRAM_DATA = 0x0000
SRAM_DATA_MAX = 0x0800
SRAM_MAILBOX = 0x0800
SRAM_MAILBOX_BYTES = 0x20
SRAM_RESULTS = 0x0900
SRAM_RESULTS_BYTES = 0x0600
SRAM_PARAMS = 0x0F00
SRAM_PARAMS_BYTES = 0x0100
SRAM_PROBE = 0x1000          # JTAG-loaded recovery probe (2 KiB)
SRAM_PROBE_BYTES = 0x0800
SRAM_STACK = 0x1800          # 2 KiB stack down from the top
SRAM_STACK_BYTES = SRAM_SIZE - SRAM_STACK
SRAM_STACK_TOP = SRAM_BASE + SRAM_SIZE

# Boot memory layout for the payload executed from DDR.
DDR_PARAMS = 0x2000
DDR_CODE = 0x3000
DDR_CODE_MAX = 0x7D000
DDR_STACK_TOP = 0x80000

WINDOWS = (
    ('ddr', DDR_BASE, DDR_SIZE, 'implemented'),
    ('sram', SRAM_BASE, SRAM_SIZE, 'implemented'),
    ('rom', ROM_BASE, ROM_SIZE, 'implemented'),
    ('xip', XIP_BASE, XIP_SIZE, 'reserved-not-implemented'),
)

SUBRANGES = (
    ('sram.data', SRAM_BASE + SRAM_DATA, SRAM_DATA_MAX),
    ('sram.mailbox', SRAM_BASE + SRAM_MAILBOX, SRAM_MAILBOX_BYTES),
    ('sram.results', SRAM_BASE + SRAM_RESULTS, SRAM_RESULTS_BYTES),
    ('sram.params', SRAM_BASE + SRAM_PARAMS, SRAM_PARAMS_BYTES),
    ('sram.probe', SRAM_BASE + SRAM_PROBE, SRAM_PROBE_BYTES),
    ('sram.stack', SRAM_BASE + SRAM_STACK, SRAM_STACK_BYTES),
    ('rom.code', ROM_BASE, ROM_CODE_BYTES),
    ('rom.status', ROM_BASE + ROM_CODE_BYTES, ROM_STATUS_BYTES),
)


def image_abi():
    """ABI tag of the AE350 boot image, shared by the host builder and the ROM."""
    import json
    import zlib
    interface = {
        'cpu': 'ae350-rv32imac', 'load': DDR_CODE, 'image_version': 1,
        'memories': {'ddr': DDR_SIZE, 'sram': [SRAM_BASE, SRAM_SIZE], 'rom': [ROM_BASE, ROM_SIZE],
                     'mailbox': SRAM_BASE + SRAM_MAILBOX, 'results': SRAM_BASE + SRAM_RESULTS},
        'uart': {'base': 0xf0300000, 'baud': 115200},
        'loader': {'max_bytes': DDR_CODE_MAX, 'stack_top': DDR_STACK_TOP,
                   'params': DDR_PARAMS, 'entry': 'image-start'},
        'capabilities': {'atomics': 'none', 'cache': 'write-back', 'loader': 'ae350-20k-frame-v1'},
    }
    return zlib.crc32(json.dumps(interface, sort_keys=True, separators=(',', ':')).encode())


def check():
    """Return a list of contract violations (empty when the map is consistent)."""
    problems = []
    spans = sorted(((n, b, s) for n, b, s, _ in WINDOWS), key=lambda w: w[1])
    for name, base, size in spans:
        if size <= 0:
            problems.append('%s has non-positive size' % name)
        if base % 4:
            problems.append('%s base 0x%08x is not word aligned' % (name, base))
    for (n1, b1, s1), (n2, b2, s2) in zip(spans, spans[1:]):
        if b1 + s1 > b2:
            problems.append('%s overlaps %s' % (n1, n2))
    if DDR_BASE != 0:
        problems.append('DDR must be zero based')
    subs = sorted(SUBRANGES, key=lambda w: w[1])
    for name, base, size in subs:
        if size <= 0:
            problems.append('%s has non-positive size' % name)
    for (n1, b1, s1), (n2, b2, s2) in zip(subs, subs[1:]):
        if b1 + s1 > b2:
            problems.append('%s overlaps %s' % (n1, n2))
    if DDR_CODE + DDR_CODE_MAX > DDR_STACK_TOP:
        problems.append('payload code range overlaps the stack')
    if DDR_PARAMS + 0x100 > DDR_CODE:
        problems.append('payload parameter block overlaps the code')
    return problems


def require():
    problems = check()
    if problems:
        raise ValueError('AE350 address map is inconsistent: ' + '; '.join(problems))


def c_header():
    """C definitions for the ROM, the payload and any diagnostic app."""
    rows = [
        ('#pragma once', None),
        ('/* Generated from gateware/machines/ae350_map.py - do not edit. */', None),
        ('#define AE350_DDR_BASE 0x%08xu' % DDR_BASE, None),
        ('#define AE350_FABRIC_HZ %du' % FABRIC_HZ, None),
        ('#define AE350_DDR_SIZE 0x%08xu' % DDR_SIZE, None),
        ('#define AE350_SRAM_BASE 0x%08xu' % SRAM_BASE, None),
        ('#define AE350_SRAM_SIZE 0x%08xu' % SRAM_SIZE, None),
        ('#define AE350_ROM_BASE 0x%08xu' % ROM_BASE, None),
        ('#define AE350_ROM_SIZE 0x%08xu' % ROM_SIZE, None),
        ('#define AE350_ROM_CODE_BYTES 0x%08xu' % ROM_CODE_BYTES, None),
        ('#define AE350_XIP_BASE 0x%08xu' % XIP_BASE, None),
        ('#define AE350_XIP_SIZE 0x%08xu' % XIP_SIZE, None),
        ('#define AE350_SRAM_MAILBOX 0x%08xu' % (SRAM_BASE + SRAM_MAILBOX), None),
        ('#define AE350_SRAM_RESULTS 0x%08xu' % (SRAM_BASE + SRAM_RESULTS), None),
        ('#define AE350_SRAM_PARAMS 0x%08xu' % (SRAM_BASE + SRAM_PARAMS), None),
        ('#define AE350_SRAM_PROBE 0x%08xu' % (SRAM_BASE + SRAM_PROBE), None),
        ('#define AE350_SRAM_PROBE_BYTES 0x%08xu' % SRAM_PROBE_BYTES, None),
        ('#define AE350_SRAM_STACK_TOP 0x%08xu' % SRAM_STACK_TOP, None),
        ('#define AE350_SRAM_DATA_MAX 0x%08xu' % (SRAM_BASE + SRAM_DATA_MAX), None),
        ('#define AE350_DDR_PARAMS 0x%08xu' % DDR_PARAMS, None),
        ('#define AE350_DDR_CODE 0x%08xu' % DDR_CODE, None),
        ('#define AE350_DDR_CODE_MAX 0x%08xu' % DDR_CODE_MAX, None),
        ('#define AE350_DDR_STACK_TOP 0x%08xu' % DDR_STACK_TOP, None),
        ('#define AE350_IMAGE_ABI 0x%08xu' % image_abi(), None),
        ('_Static_assert(AE350_SRAM_MAILBOX + 0x20u <= AE350_SRAM_RESULTS, "mailbox overlaps results");', None),
        ('_Static_assert(AE350_SRAM_RESULTS + 0x600u <= AE350_SRAM_PARAMS, "results overlap params");', None),
        ('_Static_assert(AE350_SRAM_PARAMS + 0x100u <= AE350_SRAM_BASE + 0x1000u, "params overlap the stack");', None),
    ]
    return '\n'.join(r for r, _ in rows) + '\n'


def ld_fragment():
    """Linker MEMORY block and reserved-region symbols."""
    return '\n'.join([
        '/* Generated from gateware/machines/ae350_map.py - do not edit. */',
        'MEMORY {',
        '    rom (rx) : ORIGIN = 0x%08x, LENGTH = 0x%x' % (ROM_BASE, ROM_CODE_BYTES),
        '    ram (rw) : ORIGIN = 0x%08x, LENGTH = 0x%x' % (SRAM_BASE, SRAM_STACK),
        '    ddr (rwx) : ORIGIN = 0x%08x, LENGTH = 0x%x' % (DDR_CODE, DDR_CODE_MAX),
        '}',
        '__mailbox_base = 0x%08x;' % (SRAM_BASE + SRAM_MAILBOX),
        '__mailbox_end = 0x%08x;' % (SRAM_BASE + SRAM_MAILBOX + SRAM_MAILBOX_BYTES),
        '__results_base = 0x%08x;' % (SRAM_BASE + SRAM_RESULTS),
        '__results_end = 0x%08x;' % (SRAM_BASE + SRAM_RESULTS + SRAM_RESULTS_BYTES),
        '__params_base = 0x%08x;' % (SRAM_BASE + SRAM_PARAMS),
        '__params_end = 0x%08x;' % (SRAM_BASE + SRAM_PARAMS + SRAM_PARAMS_BYTES),
        '__probe_base = 0x%08x;' % (SRAM_BASE + SRAM_PROBE),
        '__probe_end = 0x%08x;' % (SRAM_BASE + SRAM_PROBE + SRAM_PROBE_BYTES),
        '__stack_bottom = 0x%08x;' % (SRAM_BASE + SRAM_STACK),
        '__stack_top = 0x%08x;' % SRAM_STACK_TOP,
        'ASSERT(ADDR(.data) + SIZEOF(.data) <= __mailbox_base, "data overlaps the mailbox")',
        'ASSERT(ADDR(.bss) + SIZEOF(.bss) <= __mailbox_base, "bss overlaps the mailbox")',
    ]) + '\n'
