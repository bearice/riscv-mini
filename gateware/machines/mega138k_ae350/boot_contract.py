"""Small, stable reset/loader contract; deliberately independent of devices."""
VERSION = 1
# Official SDK layout: configuration image below 6 MiB, firmware at 6 MiB.
# CPU reset/link address stays 0x80000000; this is a physical Flash offset.
FLASH_OFFSET = 0x600000
CONSTANTS = dict(
    ROM_BASE=0x80000000,
    ILM_BASE=0xa0000000, ILM_SIZE=0x10000, ILM_CODE_BYTES=0x10000,
    DLM_BASE=0xa0200000, DLM_SIZE=0x10000, DLM_DATA_BYTES=0x8000,
    RESULTS_BASE=0xa0208000, LOCAL_PARAMS=0xa0208600,
    ROM_STACK_BOTTOM=0xa020e000, ROM_STACK_TOP=0xa0210000,
    PAYLOAD_STACK_BOTTOM=0xa020a000, PAYLOAD_STACK_TOP=0xa020e000,
    DDR_CODE=0x3000, DDR_CODE_MAX=0x7d000,
    STATUS_ADDR=0xf8003000, CALIB_CYCLES_ADDR=0xf8003004,
    BOOT_CONTRACT_ADDR=0xf800000c, BOOT_CLOCK_ADDR=0xf8000010,
    BOOT_IMAGE_ABI_ADDR=0xf8000014, BOOT_CONTRACT_VERSION=VERSION,
)


def validate(layout):
    for name, value in CONSTANTS.items():
        if getattr(layout, name) != value:
            raise ValueError('Boot contract changed: ' + name)
    if layout.ROM_SIZE != region_size(layout.FLASH_BYTES):
        raise ValueError('Boot region must end at the selected board Flash capacity')


def region_size(flash_bytes):
    if type(flash_bytes) is not int or flash_bytes <= FLASH_OFFSET:
        raise ValueError('Configuration Flash does not contain the firmware region')
    return flash_bytes - FLASH_OFFSET


def c_header(rom_size):
    return '#pragma once\n' + ''.join(
        '#define AE350_%s 0x%xu\n' % item for item in sorted(dict(CONSTANTS, ROM_SIZE=rom_size).items()))


def ld_fragment(rom_size):
    c = CONSTANTS
    regions = [('rom', 'rx', c['ROM_BASE'], rom_size),
               ('ilm', 'rwx', c['ILM_BASE'], c['ILM_CODE_BYTES']),
               ('dlm', 'rw', c['DLM_BASE'], c['DLM_DATA_BYTES'])]
    text = 'MEMORY {\n' + ''.join(' %s (%s) : ORIGIN = 0x%x, LENGTH = 0x%x\n' % r for r in regions) + '}\n'
    for name, value in dict(ilm_base=c['ILM_BASE'], ilm_limit=c['ILM_BASE']+c['ILM_SIZE'],
                           dlm_base=c['DLM_BASE'], dlm_limit=c['DLM_BASE']+c['DLM_SIZE'],
                           local_stack_bottom=c['ROM_STACK_BOTTOM'], local_stack_top=c['ROM_STACK_TOP']).items():
        text += '__%s = 0x%x;\n' % (name, value)
    return text
