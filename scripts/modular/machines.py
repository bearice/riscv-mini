"""Machine selection and immutable P0 inputs; no hardware generation is implied."""
import re
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from .storage import canonical, digest, file_hash, member, read, write
from .environment import discover, identity

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


def _source_inputs(reference):
    """Archive the declared project and every non-temporary source sidecar.

    Includes encrypted HDL, IPC, constraints and arbitrary init/data suffixes.
    File inventory is locked as well as contents. Generated temp/ and impl/ are
    excluded explicitly; this is a fixed-input closure, not an IP regeneration claim.
    """
    project = reference / 'ae350_customized_demo.gprj'
    tree = ET.parse(project)
    files = {project.name: project}
    for node in tree.findall('./FileList/File'):
        if node.get('enable', '1') != '1':
            continue
        path = member(reference, node.attrib['path'])
        if not path.is_file():
            raise ValueError('Missing declared vendor input: ' + str(path))
        files[path.relative_to(reference).as_posix()] = path
    for path in sorted((reference / 'src').rglob('*')):
        relative = path.relative_to(reference)
        if path.is_file() and 'temp' not in relative.parts:
            member(reference, relative.as_posix())
            files[relative.as_posix()] = path
    return files, tree.find('Device').attrib


def resolve(store, reference=None):
    board_path = store.root / 'gateware/boards/mega138k_pro/board.json'
    machine_path = store.root / 'gateware/machines/mega138k_ae350.json'
    board, machine = read(board_path), read(machine_path)
    camera = board['resources']['camera']
    reserved = {pin for values in camera.values() if isinstance(values, list) for pin in values}
    jtag = board['resources']['cpu_jtag']
    assigned = {jtag[name]['pin'] for name in ('tck', 'tdi', 'tdo', 'tms')}
    overlap = assigned & reserved
    if overlap:
        raise ValueError('CPU JTAG occupies reserved camera pins: ' + ', '.join(sorted(overlap)))
    if len(assigned) != 4 or jtag['trst'] != {'mode': 'tied-high-internal'}:
        raise ValueError('CPU JTAG requires four distinct pins and internal inactive TRSTn')
    if board['schema'] != 1 or machine['schema'] != 1 or machine['id'] != AE350 or machine['board'] != board['id']:
        raise ValueError('Unsupported Board/Machine description')
    if machine['cpu']['kind'] != 'hard' or machine['memory']['shared_l2']:
        raise ValueError('AE350 requires hard CPU and no legacy shared L2')
    clocks = machine['clocks']
    if clocks['ahb']['reference_hz'] != clocks['apb']['reference_hz']:
        raise ValueError('AE350 AHB/APB must be synchronous 1:1')
    for name in ('ahb', 'apb', 'data_memory_bus'):
        if not 0 < clocks[name]['reference_hz'] < 200000000:
            raise ValueError('AE350 bus frequency must be below 200MHz: ' + name)
    for name, ceiling in (('core', 800000000), ('ddr_ck', 400000000)):
        if not 0 < clocks[name]['target_hz'] <= ceiling:
            raise ValueError('AE350 clock exceeds specification: ' + name)
    reference = Path(reference) if reference else store.root.parent.parent / 'TangPrimer-138K/example/ae350_customized_demo'
    vendor, device = _source_inputs(reference.resolve())
    if device.get('pn') != board['reference_part'] or device.get('name') != board['reference_device']:
        raise ValueError('Vendor project part does not match the reference Board description')
    files = {'inputs/board.json': board_path, 'inputs/machine.json': machine_path}
    files.update({'inputs/vendor/' + name: path for name, path in vendor.items()})
    # Lock the resolver itself; dependency selection changes invalidate descriptions.
    for name in ('machines.py', 'storage.py', 'environment.py'):
        files['inputs/build/' + name] = store.root / 'scripts/modular' / name
    hashes = {name: file_hash(path) for name, path in sorted(files.items())}
    external = []
    for name, path in vendor.items():
        if path.suffix.lower() not in ('.ipc', '.v', '.mod', '.sdc', '.cst'):
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if re.search(r'[A-Za-z]:[/\\]', line):
                external.append(dict(file=name, line=number, text=line.strip()))
    env = discover(['python', 'gowin'], store.root)
    lock = dict(schema=1, machine=machine, board=board, sources=hashes,
                tools=identity(env), vendor_project_device=device,
                evidence=dict(host_description=True, physical_board=False,
                              rtl_generated=False, synthesized=False, board_tested=False),
                closure=dict(fixed_vendor_inputs='archived', vendor_regeneration='incomplete',
                             external_path_references=external),
                blockers=machine['pending'])
    return lock, files


def execute(store, run, args, dry):
    """Build only the real P0 artifact; defer unimplemented machine backends."""
    unsupported = [key for key in ('output_dir', 'locked', 'profile', 'cpu_verilog', 'cpu_variant', 'sd_profile',
                   'usb_backend', 'audio_clock', 'boot_mode', 'rom_size', 'l2_size', 'place_option',
                   'route_option', 'rtl_hierarchy', 'vexriscv_source', 'java', 'sbt_launch',
                   'locked_config') if getattr(args, key, None) is not None]
    unsupported += [key for key in ('generate', 'refresh_hardware', 'relaxed_pc_calculation', 'compressed',
                                  'pipelined_fetch', 'arithmetic_matrix') if getattr(args, key, False)]
    if getattr(args, 'feature', []):
        unsupported.append('feature')
    if unsupported:
        raise ValueError('AE350 does not yet support these overrides: ' + ', '.join(unsupported))
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
    lock, files = resolve(store, getattr(args, 'reference_root', None))
    key = digest(lock)
    run.result.update(machine=AE350, configuration=lock['machine'], blockers=lock['blockers'],
                      board_identity_confirmed=False, resolved_input_key=key)
    write(run.path / 'machine.lock.json', lock)
    found = store.lookup('machine-description', key)
    run.step('machine-description', 'REUSE' if found else 'BUILD', found,
             reason='exact Board/Machine/vendor/tool input closure')
    if not dry and not found:
        snapshot = {name: path.read_bytes() for name, path in files.items()}
        if any(hashlib.sha256(data).hexdigest() != lock['sources'][name] for name, data in snapshot.items()):
            raise ValueError('Machine inputs changed during capture')
        current_lock, _ = resolve(store, getattr(args, 'reference_root', None))
        if current_lock != lock:
            raise ValueError('Machine input inventory changed during capture')
        snapshot['machine.lock.json'] = canonical(lock)
        found = store.publish('machine-description', key, snapshot, config=lock)
    cpu_config = dict(kind='hard', machine=AE350, part=lock['board']['reference_part'],
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
        from .ae350 import build
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
