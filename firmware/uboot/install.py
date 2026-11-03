"""Install the riscv-mini U-Boot port into a pinned upstream U-Boot source tree.

Board files and native drivers are tracked here in the U-Boot tree layout and
copied in; their Kconfig/Makefile wiring and the board hook are added from the
tables below. Machine patches are kept for edits to upstream code only.
"""
import re
import sys
from pathlib import Path

port = Path(__file__).resolve().parent
sys.path.insert(0, str(port.parent))
import vendor_install

AE350_SD_HELP = '''\
Native LiteSDCard command core of the riscv-mini AE350 machine. Block
data moves between the card FIFOs and a DDR bounce buffer through one
channel of the hard ATCDMAC300, selected by the node's dmas property.'''
AE350_ETH_HELP = '''\
Descriptor-ring Ethernet MAC of the riscv-mini AE350 machine with a
clause-22 MDIO port to the board RTL8211 PHY.'''

MACHINES = {
    'mega138k_ae350': dict(
        board='riscv-mini/mega138k_ae350',
        target=('TARGET_MEGA138K_AE350', 'Support riscv-mini Tang Mega 138K Pro AE350'),
        files=['configs/mega138k_ae350_defconfig', 'include/configs/mega138k_ae350.h'],
        # (directory under drivers/, source name, Kconfig symbol, title, dependencies, help)
        drivers=[('mmc', 'ae350_sd', 'MMC_AE350_SD', 'riscv-mini AE350 SD card host',
                  'MMC && DM_MMC && BLK', AE350_SD_HELP),
                 ('net', 'ae350_eth', 'AE350_ETH', 'riscv-mini AE350 Ethernet',
                  'NETDEVICES && DM_ETH', AE350_ETH_HELP)],
        patches=port/'ae350/patches'),
}


def inputs(machine):
    spec = MACHINES[machine]
    return [Path(__file__).resolve(), *sorted((port/'board'/spec['board']).iterdir()),
            *(port/name for name in spec['files']),
            *(port/'drivers'/directory/(name+'.c') for directory, name, *_ in spec['drivers']),
            *vendor_install.patches(spec['patches'])]


def hook_board(src, board, target, title):
    """Board target in the arch target choice and its Kconfig among the board sources."""
    kconfig = src/'arch/riscv/Kconfig'
    text = kconfig.read_text()
    if f'config {target}\n' not in text:
        choice, end = text.find('prompt "Target select"'), text.find('\nendchoice\n')
        if choice < 0 or end < choice:
            raise RuntimeError('arch/riscv/Kconfig has no target choice to extend')
        text = text[:end+1] + f'config {target}\n\tbool "{title}"\n\n' + text[end+1:]
    line = f'source "board/{board}/Kconfig"\n'
    if line not in text:
        sources = list(re.finditer(r'^source "board/[^"]+/Kconfig"\n', text, re.M))
        if not sources:
            raise RuntimeError('arch/riscv/Kconfig has no board Kconfig sources')
        text = text[:sources[-1].end()] + line + text[sources[-1].end():]
    kconfig.write_text(text)


def add_driver(src, directory, name, config, title, depends, help_text):
    folder = src/'drivers'/directory
    vendor_install.copy(port/'drivers'/directory/(name+'.c'), folder/(name+'.c'))
    kconfig = folder/'Kconfig'
    if f'config {config}\n' not in kconfig.read_text():
        body = ''.join('\t  '+line+'\n' for line in help_text.splitlines())
        with kconfig.open('a') as stream:
            stream.write(f'\nconfig {config}\n\tbool "{title}"\n\tdepends on {depends}\n\thelp\n{body}')
        with (folder/'Makefile').open('a') as stream:
            stream.write(f'obj-$(CONFIG_{config}) += {name}.o\n')


def install(src, machine):
    spec = MACHINES[machine]
    for path in sorted((port/'board'/spec['board']).iterdir()):
        vendor_install.copy(path, src/'board'/spec['board']/path.name)
    for name in spec['files']:
        vendor_install.copy(port/name, src/name)
    hook_board(src, spec['board'], *spec['target'])
    for driver in spec['drivers']:
        add_driver(src, *driver)
    vendor_install.apply_patches(src, spec['patches'])


if __name__ == '__main__':
    vendor_install.main(__doc__, MACHINES, install, inputs)
