"""20K upstream source pins; the reconstruction mechanism is framework-level.

The VexRiscv machine keeps the same pinned upstream versions as AE350 for the
shared trees (Linux, U-Boot) but installs its own port, so every reconstructed
tree hash differs and each machine names its manifests separately.
"""
from scripts.modular import sourceprep
from scripts.modular.sourceprep import tree_hash  # re-export: callers hash trees for recipes

MANIFESTS = {'linux': 'firmware/linux/vexriscv/source.json',
             'opensbi': 'firmware/opensbi/vexriscv/source.json',
             'uboot': 'firmware/uboot/vexriscv/source.json'}


def manifest(store, name):
    return sourceprep.manifest(store, MANIFESTS[name])


def installer_inputs(store, name):
    return sourceprep.installer_inputs(store, name, MANIFESTS)


def prepare(store, run, name, source, dry=False):
    return sourceprep.prepare(store, run, name, source, MANIFESTS, dry=dry)
