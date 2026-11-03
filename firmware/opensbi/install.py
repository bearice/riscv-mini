"""Install the riscv-mini OpenSBI port into a pinned upstream OpenSBI source tree.

AE350 runs upstream's generic platform with the Andes AE350 support, so the
port adds no files; its edits to upstream code are the machine patches.
"""
import sys
from pathlib import Path

port = Path(__file__).resolve().parent
sys.path.insert(0, str(port.parent))
import vendor_install

MACHINES = {'mega138k_ae350': dict(patches=port/'ae350/patches')}


def inputs(machine):
    return [Path(__file__).resolve(), *vendor_install.patches(MACHINES[machine]['patches'])]


def install(src, machine):
    vendor_install.apply_patches(src, MACHINES[machine]['patches'])


if __name__ == '__main__':
    vendor_install.main(__doc__, MACHINES, install, inputs)
