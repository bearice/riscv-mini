"""AE350 upstream source pins; the reconstruction mechanism is framework-level.

The pin tables and machine identity stay here; prepare/tree checks live in
scripts.modular.sourceprep and take this table as an argument.
"""
from scripts.modular import sourceprep
from scripts.modular.sourceprep import tree_hash  # re-export: callers hash trees for recipes

MANIFESTS = {'linux': 'firmware/linux/ae350/source.json',
             'opensbi': 'firmware/opensbi/ae350/source.json',
             'uboot': 'firmware/uboot/ae350/source.json'}


def manifest(store, name):
    return sourceprep.manifest(store, MANIFESTS[name])


def installer_inputs(store, name):
    return sourceprep.installer_inputs(store, name, MANIFESTS)


def prepare(store, run, name, source, dry=False):
    return sourceprep.prepare(store, run, name, source, MANIFESTS, dry=dry)
