"""Machine registry: the only place a machine id maps to a backend package.

Framework modules never import a machine backend directly; they ask this
registry for a part and get the machine's own module back. An unknown machine
raises instead of falling back to the original 20K backend.
"""
import importlib

from scripts.modular.storage import read

PRIMARY = 'primer20k_vexriscv'
LEGACY = PRIMARY
AE350 = 'mega138k_ae350'
MACHINES = (PRIMARY, AE350)
PARTS = ('arguments', 'configuration', 'contracts', 'cpu', 'firmware', 'hardware', 'rtl', 'system')


def normalise(machine=None):
    name = machine or PRIMARY
    if name not in MACHINES:
        raise ValueError('Unknown machine: ' + str(name))
    return name


def part(machine, name):
    """Import one backend module of one machine, on demand."""
    machine = normalise(machine)
    if name not in PARTS:
        raise ValueError('Unknown machine backend: ' + str(name))
    return importlib.import_module('scripts.modular.machines.%s.%s' % (machine, name))


def release(machine, name):
    """Release operation of one machine, imported on demand."""
    machine = normalise(machine)
    if machine != PRIMARY:
        raise ValueError('Machine ' + machine + ' has no release operation')
    module = importlib.import_module('scripts.mini_ops.%s.release' % PRIMARY)
    return getattr(module, name)


def description(machine):
    machine = normalise(machine)
    return importlib.import_module('scripts.modular.machines.%s.description' % machine)


def operations(machine):
    """Board and suite operations for one machine, imported on demand."""
    machine = normalise(machine)
    if machine == AE350:
        from scripts.mini_ops.mega138k_ae350 import (
            ae350, ae350_ddr_suite, ae350_dma_suite, ae350_io_suite, ae350_jtag_test, ae350_upload, ae350_audio_suite, ae350_video_suite)
        return {
            'jtag-test': ae350_jtag_test.perform,
            'ddr-suite': ae350_ddr_suite.perform,
            'dma-suite': ae350_dma_suite.perform,
            'io-suite': ae350_io_suite.perform,
            'audio-suite': ae350_audio_suite.perform,
            'video-suite': ae350_video_suite.perform,
            'ae350-upload': ae350_upload.perform_upload,
            '*': ae350.perform,
        }
    from scripts.mini_ops.primer20k_vexriscv import board
    return {'*': board.perform}


def operation(machine, name):
    table = operations(machine)
    return table.get(name, table['*'])


def reference_path(store, name, machine=None):
    machine = normalise(machine)
    if name not in ('current', 'baseline'):
        raise ValueError('Only current/baseline references can be pinned')
    if machine == PRIMARY:
        return store.build / 'refs' / (name + '.json')
    return store.build / 'refs/machines' / machine / (name + '.json')


def select_hardware(store, name, machine=None):
    """Resolve scoped aliases without ever falling back to another machine."""
    machine = normalise(machine)
    if name in ('current', 'baseline') and machine != PRIMARY:
        path = reference_path(store, name, machine)
        if not path.is_file():
            raise ValueError('No ' + name + ' reference for machine ' + machine)
        return str(path)
    return name


def system_machine(store, ref):
    config = read(store.path(ref) / 'bundle.lock.json')['configuration']
    return config.get('machine', PRIMARY)


def check_system(store, ref, machine=None):
    expected = normalise(machine)
    actual = system_machine(store, ref)
    if actual != expected:
        raise ValueError('Machine mismatch: expected ' + expected + ', got ' + actual)


def execute(store, run, args, dry):
    """Dispatch a machine-backed build request to its owning backend."""
    machine = normalise(getattr(args, 'machine', None))
    if machine == PRIMARY:
        raise ValueError('The 20K backend is dispatched by targets.execute, not the registry')
    return description(machine).execute(store, run, args, dry)
