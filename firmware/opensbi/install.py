"""Install the riscv-mini OpenSBI port into a pinned upstream OpenSBI source tree.

AE350 runs upstream's generic platform with the Andes AE350 support, so the
port adds no files; its edits to upstream code are the machine patches.
The VexRiscv machine has no upstream platform, so its port supplies the whole
platform/riscv-mini directory plus one platform patch.
"""
import sys
from pathlib import Path

port = Path(__file__).resolve().parent
sys.path.insert(0, str(port.parent))
import vendor_install

MACHINES = {'mega138k_ae350': dict(patches=port/'ae350/patches'),
            'primer20k_vexriscv': dict(
                files={'vexriscv/platform': 'platform/riscv_mini'},
                patches=port/'vexriscv/patches')}


def port_files(machine):
    """Port files this machine copies into the tree, as (source, in-tree) pairs."""
    for source, target in MACHINES[machine].get('files', {}).items():
        for path in sorted((port/source).rglob('*')):
            if path.is_file():
                yield path, Path(target)/path.relative_to(port/source)


def inputs(machine):
    return [Path(__file__).resolve(), *(path for path, _ in port_files(machine)),
            *vendor_install.patches(MACHINES[machine]['patches'])]


def install(src, machine):
    for path, target in port_files(machine):
        vendor_install.copy(path, src/target)
    vendor_install.apply_patches(src, MACHINES[machine]['patches'])


if __name__ == '__main__':
    vendor_install.main(__doc__, MACHINES, install, inputs)
