"""Machine selection and immutable P0 inputs; no hardware generation is implied."""
import hashlib
from pathlib import Path
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[4]), str(Path(__file__).resolve().parents[4] / 'scripts')]
from scripts.modular.storage import canonical, digest, source_file_hash, source_bytes, SOURCE_HASH_FORMAT, read, write
from scripts.modular.environment import discover, identity
from scripts.modular.machines.mega138k_ae350.ae350 import timing_report  # re-export for tools

LEGACY = 'primer20k_vexriscv'
AE350 = 'mega138k_ae350'
MACHINES = (LEGACY, AE350)


def reference_path(store, name, machine=None):
    machine = machine or LEGACY
    if machine not in MACHINES:
        raise ValueError('Unknown machine: ' + machine)
    if name not in ('current', 'baseline'):
        raise ValueError('Only current/baseline references can be pinned')
    if machine == LEGACY:
        return store.build / 'refs' / (name + '.json')
    return store.build / 'refs/machines' / machine / (name + '.json')


def select_hardware(store, name, machine=None):
    """Resolve scoped aliases without ever falling back to another machine."""
    machine = machine or LEGACY
    if name in ('current', 'baseline') and machine != LEGACY:
        path = reference_path(store, name, machine)
        if not path.is_file():
            raise ValueError('No ' + name + ' reference for machine ' + machine)
        return str(path)
    return name


def system_machine(store, ref):
    config = read(store.path(ref) / 'bundle.lock.json')['configuration']
    return config.get('machine', LEGACY)


def check_system(store, ref, machine=None):
    expected = machine or LEGACY
    actual = system_machine(store, ref)
    if actual != expected:
        raise ValueError('Machine mismatch: expected ' + expected + ', got ' + actual)


def resolve(store, snapshot=None):
    """Resolve P0 from repository-owned board and machine contracts only.

    Vendor example projects are design references. They are never opened,
    inventoried, or copied into the machine-description artifact.
    """
    from gateware.description import load_machine, source_inputs
    board, machine = load_machine(AE350, store.root)
    files = {'inputs/source/' + name: store.root / name for name in source_inputs(AE350, store.root)}
    # Lock the resolver itself; dependency selection changes invalidate descriptions.
    for name, path in (('description.py', store.root / 'scripts/modular/machines/mega138k_ae350/description.py'),
                       ('storage.py', store.root / 'scripts/modular/storage.py'),
                       ('environment.py', store.root / 'scripts/modular/environment.py')):
        files['inputs/build/' + name] = path
    hashes = {name: source_file_hash(path) for name, path in sorted(files.items())}
    env = discover(['python', 'gowin'], store.root)
    if snapshot is not None and (snapshot['board'] != board or snapshot['machine'] != machine):
        raise ValueError('Source configuration differs from resolved IP snapshot')
    lock = dict(schema=1, storage_policy='outputs-only', machine=machine, board=board, ip_artifacts=(snapshot or {}).get('artifacts', {}), source_hash_format=SOURCE_HASH_FORMAT, sources=hashes,
                tools=identity(env),
                device=dict(name=board['fpga']['device'], part=board['fpga']['part'], source='board.json'),
                evidence=dict(host_description=True, physical_board=False,
                              rtl_generated=False, synthesized=False, board_tested=False),
                closure=dict(scope='repository-owned-board-machine-and-build-inputs'),
                blockers=[])
    return lock, files


def execute(store, run, args, dry):
    """Resolve one source snapshot and dispatch the native AE350 adapters."""
    if getattr(args, 'locked', None):
        raise ValueError('AE350 locked system replay requires a completed system backend')
    if getattr(args, 'synthesize', False) and args.target != 'gateware':
        raise ValueError('AE350 --synthesize requires the gateware target')
    if getattr(args, 'source', None) is not None and args.target != 'payload':
        raise ValueError('AE350 --source is only meaningful for the payload target')
    if args.target not in ('machine-description', 'cpu', 'bootloader', 'system', 'soc-description', 'gateware', 'payload'):
        raise ValueError('AE350 ' + args.target + ' backend is pending P1; cannot use the 20K backend')
    if args.target == 'system' and not dry:
        raise ValueError('AE350 complete system backend is pending P1; build bootloader or gateware for host bring-up')
    # A P0 description starts from explicit source inputs, never from 20K current.
    if args.hardware != 'current':
        raise ValueError('AE350 P0 describes source inputs; --hardware selection is pending P1')
    resolved_snapshot = getattr(args, 'resolved_machine_snapshot', None)
    lock, files = resolve(store, resolved_snapshot)
    key = digest(lock)
    run.result.update(machine=AE350, configuration=lock['machine'], blockers=lock['blockers'],
                      board_identity_confirmed=False, resolved_input_key=key)
    write(run.path / 'machine.lock.json', lock)
    found = store.lookup('machine-description', key)
    run.step('machine-description', 'REUSE' if found else 'BUILD', found,
             reason='exact repository-owned Board/Machine/tool input closure')
    if not dry and not found:
        snapshot = {}
        if any(source_file_hash(path) != lock['sources'][name] for name, path in files.items()):
            raise ValueError('Machine inputs changed during capture')
        current_lock, _ = resolve(store, resolved_snapshot)
        if current_lock != lock:
            raise ValueError('Machine input inventory changed during capture')
        snapshot['machine.lock.json'] = canonical(lock)
        found = store.publish('machine-description', key, snapshot, config=lock,
            dependencies={'ip-' + name: value['artifact'] for name,value in lock['ip_artifacts'].items()})
    cpu_config = dict(kind='hard', machine=AE350, part=lock['board']['fpga']['part'],
                      cpu=lock['machine']['cpu'], input_key=key, qualification='description-only')
    cpu_key = digest(cpu_config)
    cpu = store.lookup('cpu', cpu_key) if found else None
    if args.target != 'machine-description':
        run.step('cpu', 'REUSE' if cpu else 'BUILD', cpu, reason='AE350 hard macro configuration; no soft CPU RTL')
        if not dry and not cpu:
            cpu = store.publish('cpu', cpu_key, {'cpu.lock.json': canonical(cpu_config)},
                                config=cpu_config, dependencies={'machine-description': found})
    if args.target in ('bootloader', 'soc-description', 'gateware', 'payload'):
        if cpu is None and dry:
            run.step(args.target, 'BUILD', reason='requires exact hard CPU configuration')
            run.finish(plan=True, artifact=None, qualification='host-generated-only', hardware_qualified=False)
            return None
        from scripts.modular.machines.mega138k_ae350.ae350 import build
        return build(store, run, args, cpu, lock['board'], dry)
    pending = args.target not in ('machine-description', 'cpu')
    if pending:
        run.step(args.target, 'BLOCKED', reason='P1 backend not implemented; no generated RTL or runnable system')
    artifact = found if args.target == 'machine-description' else cpu
    if not dry:
        write(store.build / 'refs/machines' / AE350 / 'candidates' / (run.id + '.json'),
              dict(machine=AE350, artifact=artifact, run=run.id, qualification='description-only'))
    run.finish(plan=dry, artifact=artifact, ready_for_execution=not pending,
               qualification='description-only', hardware_qualified=False)
    return artifact
