"""Physical address layout shared by gateware and firmware build tools."""
CSR_BASE = 0xf0000000
ETHMAC_BASE = 0xf1000000
USB_BASE = 0xf2000000
XIP_BASE = 0xf3000000
XIP_WINDOW_SIZE = 0x400000


def address_values(base, size):
    end = base + size
    return dict(RAM_BASE=base, RAM_SIZE=size, RAM_END=end,
        BOOT_RAM_BASE=base+0x007ff000, BOOT_RAM_SIZE=4096,
        BIOS_BASE=base+0x00800000, BIOS_SIZE=4*1024*1024,
        PAYLOAD_BASE=base+0x01000000, PAYLOAD_LIMIT=end-0x00200000,
        STAGING_BASE=end-0x01000000, CSR_BASE=CSR_BASE, ETHMAC_BASE=ETHMAC_BASE,
        USB_BASE=USB_BASE, XIP_BASE=XIP_BASE, XIP_WINDOW_SIZE=XIP_WINDOW_SIZE)


def c_header(memories):
    ram = memories['main_ram']
    values = address_values(ram['base'], ram['size'])
    return '#pragma once\n' + ''.join(
        f'#define MINI_{name} 0x{value:08x}u\n' for name, value in values.items())


def linker_flags(memories):
    ram = memories['main_ram']
    return [f'-Wl,--defsym,MINI_{name}={value}'
            for name, value in address_values(ram['base'], ram['size']).items()]


class AddressMap:
    def __init__(self, memory):
        self.values = address_values(memory.base, memory.size_bytes)

    def __getattr__(self, name):
        try:
            return self.values[name]
        except KeyError as error:
            raise AttributeError(name) from error
