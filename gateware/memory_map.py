"""Physical address layout shared by gateware and firmware build tools."""
RAM_BASE = 0x00000000
RAM_SIZE = 128 * 1024 * 1024
RAM_END = RAM_BASE + RAM_SIZE
BOOT_RAM_BASE = RAM_BASE + 0x007ff000
BOOT_RAM_SIZE = 4096
BIOS_BASE = RAM_BASE + 0x00800000
BIOS_SIZE = 4 * 1024 * 1024
PAYLOAD_BASE = RAM_BASE + 0x01000000
PAYLOAD_LIMIT = RAM_END - 0x00200000
STAGING_BASE = RAM_END - 0x01000000
CSR_BASE = 0xf0000000
ETHMAC_BASE = 0xf1000000
USB_BASE = 0xf2000000
XIP_BASE = 0xf3000000
XIP_WINDOW_SIZE = 0x400000


def c_header():
    values = {name: value for name, value in globals().items()
              if name.isupper() and isinstance(value, int)}
    return '#pragma once\n' + ''.join(
        f'#define MINI_{name} 0x{value:08x}u\n' for name, value in values.items())


def linker_flags():
    return [f'-Wl,--defsym,MINI_{name}={value}' for name, value in globals().items()
            if name.isupper() and isinstance(value, int)]
