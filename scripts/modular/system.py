"""Legacy import, complete system locks, portable export and component assembly."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from scripts.boot_image import abi_tag, unpack_image
from scripts.build_records import artifact_identity
from .storage import ROOT, Store, Run, canonical, digest, file_hash, member, read, text_file, write, workspace_lock
from .contracts import hardware, check, assembly
from .cpu import locked_cpu
from .firmware import build_component, source_files, generated_headers
from .environment import discover, identity, python_packages

HARDWARE_FIELDS = ('profile', 'boot_mode', 'rom_size_bytes', 'sram_size_bytes', 'boot_ram_address',
    'boot_ram_size_bytes', 'boot_ram_backend', 'cpu_variant', 'isa', 'abi', 'cpu_capabilities',
    'l2_size_bytes', 'memory_system', 'memory_port_widths', 'memory_burst_bytes', 'l2_policy',
    'sd_profile', 'sd_backend', 'usb_backend', 'audio_clock', 'clock_hz', 'ddr_clock_hz',
    'features', 'verilog_mode', 'rtl_hierarchy', 'netlist_hierarchy', 'place_option', 'route_option')


def resolve_legacy(name, root=ROOT):
    candidate = Path(name)
    if candidate.is_dir() and (candidate / 'validation.json').is_file():
        return candidate.resolve()
    from scripts.build_records import get_record
    return Path(get_record(name, root=root)['path'])


def resolve_system(store, name):
    if isinstance(name, dict):
        ref = name
    elif Path(name).is_file():
        data = read(name)
        ref = data.get('system', data)
    else:
        ref_file = store.build / 'refs' / (str(name) + '.json')
        if ref_file.is_file() and read(ref_file).get('system'):
            ref = read(ref_file)['system']
        elif len(str(name)) == 64:
            ref = dict(component='system', digest=name)
        else:
            return None
    if ref.get('component') != 'system':
        return None
    store.get(ref, recursive=True)
    assembly(store, read(store.path(ref) / 'bundle.lock.json')['components'])
    return ref


def bundle(store, parts, configuration, origin, run):
    compatibility = assembly(store, parts)
    locks = {name: store.get(ref)['config'] for name, ref in parts.items()}
    lock = dict(schema=1, components=parts, configuration=configuration, component_locks=locks,
                compatibility=compatibility)
    # Origin belongs to run provenance; it does not perturb an unchanged bundle.
    ref = store.publish('system', digest(lock), {'bundle.lock.json': canonical(lock)},
                        config=dict(configuration=configuration), dependencies=parts)
    run.result['source'] = origin
    run.result['system'] = ref
    run.result['compatibility'] = compatibility
    run.save()
    write(store.build / 'refs/candidates' / (run.id + '.json'), dict(system=ref, run=run.id))
    return ref


def import_legacy(store, name, run):
    existing = resolve_system(store, name)
    if existing:
        run.step('system', 'REUSE', existing)
        return existing
    path = resolve_legacy(name, store.root)
    v = artifact_identity(path)
    csr = read(path / 'csr.json')
    if abi_tag(csr) != v['boot_image']['abi_tag']:
        raise ValueError('Legacy CSR does not match image ABI')
    if file_hash(path / 'firmware/boot.bin') != v['firmware_sha256']:
        raise ValueError('Legacy bootloader hash differs')
    config = {k: v[k] for k in HARDWARE_FIELDS}
    # Preserve actual archived raw metadata for provenance, never use paths as bindings.
    versions = v['component_versions']
    cpu_path = path / 'inputs/cpu' / Path(v['cpu_verilog']).name if v.get('cpu_verilog') else None
    if cpu_path and not cpu_path.is_file(): cpu_path = Path(v['cpu_verilog'])
    legacy_key=digest(dict(validation=file_hash(path/'validation.json'),csr=file_hash(path/'csr.json'),
                          contract=hardware(csr,config),
                          cpu=file_hash(cpu_path) if cpu_path else None,
                          generator=file_hash(cpu_path.parent/'generator.json') if cpu_path and (cpu_path.parent/'generator.json').is_file() else None,
                          importer={name:file_hash(Path(__file__).parent/name) for name in ('system.py','cpu.py','contracts.py')}))
    cached=store.build/'index/legacy-imports'/(legacy_key+'.json')
    if cached.is_file():
        ref=read(cached);store.get(ref,recursive=True)
        if cpu_path:locked_cpu(cpu_path)
        run.step('system','REUSE',ref,reason='verified legacy inputs already imported')
        return ref
    if cpu_path:
        cpu = locked_cpu(cpu_path)
        files = {'outputs/' + cpu_path.name: cpu_path, 'cpu.lock.json': canonical(cpu)}
        files['inputs/generator.json'] = cpu_path.parent / 'generator.json'
        if cpu_path.with_suffix('.yaml').is_file(): files['outputs/' + cpu_path.with_suffix('.yaml').name] = cpu_path.with_suffix('.yaml')
    else:
        cpu = dict(schema=1, builtin_variant=v['cpu_variant'], capabilities=v['cpu_capabilities'],
                   source='legacy builtin CPU; consumed from preserved gateware')
        files = {'cpu.lock.json': canonical(cpu)}
    cpu_ref = store.publish('cpu', digest(cpu), files, config=cpu, version=versions['rtl'])
    soc_config = dict(validation=config, contract=hardware(csr, config), legacy_abi_tag=abi_tag(csr))
    soc = store.publish('soc-description', digest(dict(csr=csr, config=soc_config, cpu=cpu_ref)),
                        {'csr.json': path / 'csr.json'}, config=soc_config, dependencies={'cpu': cpu_ref}, version=versions['rtl'])
    boot_files = {'outputs/' + p.name: p for p in (path / 'firmware').glob('boot.*') if p.suffix in ('.bin', '.elf', '.map')}
    if (path / 'firmware/xip.bin').is_file(): boot_files['outputs/xip.bin'] = path / 'firmware/xip.bin'
    boot = store.publish('bootloader', digest(dict(soc=soc, binary=v['firmware_sha256'])), boot_files,
                         config=dict(legacy=True, boot_mode=v['boot_mode']), dependencies={'soc-description': soc}, version=versions['bootloader'])
    gate_files = {'outputs/riscv_mini.fs': path / 'gateware/riscv_mini.fs'} if v.get('synthesis_requested') else {}
    # Preserve vendor project/options, reports and replay inputs when available.
    for folder in ('reports/pnr', 'replay', 'inputs/cpu'):
        if (path / folder).is_dir():
            gate_files.update({(Path(folder) / p.relative_to(path / folder)).as_posix(): p for p in (path / folder).rglob('*') if p.is_file()})
    for filename in ('build-parameters.json', 'toolchain.json'):
        if (path / filename).is_file(): gate_files[filename] = path / filename
    for filename in ('run.tcl', 'riscv_mini.sdc', 'riscv_mini.cst', 'rtl-manifest.json'):
        if (path / 'gateware' / filename).is_file(): gate_files['project/' + filename] = path / 'gateware' / filename
    gate_files['validation.original.json'] = path / 'validation.json'
    gate = store.publish('gateware', digest(dict(soc=soc, boot=boot if v['boot_mode']=='rom' else None,
                                                 bitstream=v.get('bitstream_sha256'), config=config)),
                         gate_files, config=dict(legacy=True, validation=v,
                         reproducibility='preserved legacy flags and inputs; unrecorded upstream defaults remain unknown'),
                         dependencies={'soc-description': soc, **({'bootloader': boot} if v['boot_mode']=='rom' else {})}, version=versions['rtl'])
    bios_files = {'outputs/' + p.name: p for p in (path / 'firmware').glob('app.*') if p.suffix in ('.elf', '.bin', '.img', '.map')}
    bios = store.publish('bios', digest(dict(soc=soc, image=v['boot_image']['sha256'])), bios_files,
                         config=dict(legacy=True, legacy_abi_tag=abi_tag(csr)), dependencies={'soc-description': soc, 'bootloader': boot}, version=versions['bios'])
    parts = {'cpu': cpu_ref, 'soc-description': soc, 'bootloader': boot, 'gateware': gate, 'bios': bios}
    for component, ref in parts.items(): run.step(component, 'IMPORT', ref)
    # The imported SDK was statically linked, so do not pretend it is a reusable .a.
    ref=bundle(store, parts, config, dict(kind='legacy-import', path=str(path), commit=v.get('final_git_commit')), run)
    write(cached,ref)
    return ref


def build_firmware_system(store, base, target, run, plan=False, application=None):
    base_ref = import_legacy(store, base, run)
    lock = read(store.path(base_ref) / 'bundle.lock.json')
    parts = dict(lock['components'])
    soc = parts['soc-description']
    run.step('cpu', 'REUSE', parts['cpu'])
    run.step('soc-description', 'REUSE', soc)
    run.step('gateware', 'REUSE', parts['gateware'])
    if target == 'bootloader':
        boot = build_component(store, run, 'bootloader', soc, plan=plan)
        if not plan: run.finish(artifact=boot)
        return boot
    run.step('bootloader', 'REUSE', parts['bootloader'])
    sdk = build_component(store, run, 'hal-sdk', soc, plan=plan)
    if target == 'hal-sdk':
        if not plan: run.finish(artifact=sdk)
        return sdk
    if plan and sdk is None:
        run.step('bios', 'BUILD', reason='HAL SDK needs building')
        run.finish(plan=True); return None
    parts['hal-sdk'] = sdk
    bios = build_component(store, run, 'bios', soc, boot=parts['bootloader'], sdk=sdk, plan=plan, application=application)
    if plan:
        run.finish(plan=True); return None
    parts['bios'] = bios
    ref = bundle(store, parts, lock['configuration'], dict(kind='current-source', commit=git_commit(store.root)), run)
    run.finish(system=ref, artifact=bios if target == 'bios' else ref)
    return ref


def git_commit(root):
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()


def export_bundle(store, ref, destination, test_runs=()):
    """Export an exact input/artifact closure. Does not claim a fresh board test."""
    destination = Path(destination).resolve()
    if destination.exists(): raise ValueError('Release destination exists; verify instead of overwriting')
    closure = store.closure(ref)
    assembly(store, read(store.path(ref) / 'bundle.lock.json')['components'])
    destination.mkdir(parents=True)
    try:
        for item in closure:
            target = destination / 'artifacts' / item['component'] / item['digest']
            shutil.copytree(store.path(item), target)
        for rid in test_runs:
            if Path(rid).name != rid: raise ValueError('Expected a run ID')
            source = store.build / 'runs' / rid
            data = read(source / 'result.json')
            if data.get('kind') != 'test' or data.get('passed') is not True:
                raise ValueError('Release test run must be a completed passing test')
            if data.get('system') != ref:
                raise ValueError('Test run does not cover this exact system')
            shutil.copytree(source, destination / 'runs' / rid)
        files = {p.relative_to(destination).as_posix(): file_hash(p) for p in destination.rglob('*') if p.is_file()}
        write(destination / 'release.lock.json', dict(schema=1, system=ref, artifacts=closure,
              files=files, test_runs=list(test_runs), board_verified=False,
              kind='artifact-bundle', rebuild='use pinned per-component build inputs; legacy omissions remain explicit'))
        verify_export(destination)
    except BaseException:
        # Keep incomplete exports diagnosable; no complete lock is trusted without verification.
        raise
    return destination


def verify_export(destination):
    destination = Path(destination).resolve()
    lock = read(destination / 'release.lock.json')
    if lock.get('schema') != 1: raise ValueError('Unknown release lock schema')
    for name, sha in lock['files'].items():
        if file_hash(member(destination, name)) != sha: raise ValueError('Release input differs: ' + name)
    store = Store(destination)
    store.build = destination
    # Store.path normally uses store/, exported artifacts use artifacts/.
    original = store.path
    store.path = lambda ref: destination / 'artifacts' / original(ref).relative_to(destination/'store')
    ref = lock['system']
    closure = store.closure(ref)
    if sorted(closure, key=lambda x: x['digest']) != sorted(lock['artifacts'], key=lambda x: x['digest']):
        raise ValueError('Release dependency closure differs')
    store.get(ref, recursive=True)
    assembly(store, read(store.path(ref) / 'bundle.lock.json')['components'])
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file() and p.relative_to(destination).as_posix()!='release.lock.json'}
    if actual != set(lock['files']): raise ValueError('Release file inventory differs')
    for rid in lock['test_runs']:
        if Path(rid).name != rid: raise ValueError('Invalid test run ID')
        result=read(member(destination,'runs/'+rid+'/result.json'))
        if result.get('kind')!='test' or result.get('passed') is not True or result.get('system')!=ref:
            raise ValueError('Release test does not cover its system')
    return lock


def import_export(store, source):
    source = Path(source).resolve(); lock = verify_export(source)
    for ref in lock['artifacts']:
        origin = source / 'artifacts' / ref['component'] / ref['digest']
        data = read(origin / 'manifest.json')
        # Import dependencies in topological order below.
    pending = list(lock['artifacts'])
    while pending:
        progress = False
        for ref in pending[:]:
            origin = source / 'artifacts' / ref['component'] / ref['digest']
            data = read(origin / 'manifest.json')
            if all(store.path(dep).is_dir() for dep in data['dependencies'].values()):
                actual = store.publish(data['component'], data['build_key'], {n: member(origin, n) for n in data['files']},
                                       data['config'], data['dependencies'], data['version'])
                if actual != ref: raise ValueError('Import changed artifact identity')
                pending.remove(ref); progress = True
        if not progress: raise ValueError('Cyclic/missing dependency in release')
    return lock['system']


def materialize(store, ref, destination):
    """Transient legacy board-tool view; immutable store keeps shared file ownership."""
    assembly(store, read(store.path(ref) / 'bundle.lock.json')['components'])
    lock = read(store.path(ref) / 'bundle.lock.json'); parts = lock['components']
    gate = store.get(parts['gateware']); v = dict(gate['config']['validation'])
    bios = store.get(parts['bios']); boot = store.get(parts['bootloader'])
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for comp, folder in [('bios', 'firmware'), ('bootloader', 'firmware'), ('gateware', 'gateware')]:
        path = store.path(parts[comp])
        for name in store.get(parts[comp])['files']:
            if name.startswith('outputs/'):
                target = destination / folder / Path(name).name
                target.parent.mkdir(exist_ok=True); shutil.copyfile(path / name, target)
    shutil.copyfile(store.path(parts['soc-description']) / 'csr.json', destination / 'csr.json')
    for name in gate['files']:
        if name.startswith('project/'):
            target=destination/'gateware'/name[len('project/'):]
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(store.path(parts['gateware'])/name,target)
    if 'hal-sdk' in parts:
        include=store.path(parts['hal-sdk'])/'include'
        if include.is_dir():shutil.copytree(include,destination/'firmware/include',dirs_exist_ok=True)
    write(destination/'bundle.lock.json',lock)
    cpu_data=store.get(parts['cpu'])
    cpus=[n for n in cpu_data['files'] if n.startswith('outputs/') and n.endswith('.v')]
    if cpus:
        folder=destination/'inputs/cpu';folder.mkdir(parents=True,exist_ok=True)
        for name in cpu_data['files']:
            if (name.startswith('outputs/') and Path(name).suffix in ('.v','.yaml')) or name=='inputs/generator.json':
                shutil.copyfile(store.path(parts['cpu'])/name,folder/Path(name).name)
        v['cpu_verilog']=str(folder/Path(cpus[0]).name)
    else:v['cpu_verilog']=None
    v['application_source']=str(store.root/'firmware/bios/main.c')
    image = destination / 'firmware/app.img'
    w, _ = unpack_image(image.read_bytes())
    v['boot_image'] = dict(v.get('boot_image',{}), abi_tag=w[3], sha256=file_hash(image), image_bytes=image.stat().st_size,
                          flash_offset=0x200000,load_address=w[5],entry=w[6])
    v['firmware_sha256'] = file_hash(destination / 'firmware/boot.bin')
    v['firmware_bytes'] = (destination / 'firmware/boot.bin').stat().st_size
    v.setdefault('firmware_sizes', {name:{'binary_bytes':(destination/'firmware'/filename).stat().st_size}
                                   for name,filename in (('boot','boot.bin'),('app','app.bin'))})
    v['board_test'] = 'not performed for this combination'
    from scripts.versions import read_versions
    v['component_versions'] = dict(read_versions(store.root/'VERSIONS.yaml'), **v.get('component_versions',{}))
    v['component_versions'].update(bios=bios['version'],bootloader=boot['version'])
    v['build_id']='bios-'+str(bios['version'])+'+'+bios['build_key'][:12]
    v['config_sha256']=digest(lock['configuration'])
    if v['boot_mode']=='xip':
        from gateware.flash_xip import FLASH_BASE,XIP_OFFSET,XIP_SIZE
        xip=destination/'firmware/xip.bin'
        if not xip.is_file():shutil.copyfile(destination/'firmware/boot.bin',xip)
        v['xip']=dict(base=FLASH_BASE,reset_address=FLASH_BASE+XIP_OFFSET,flash_offset=XIP_OFFSET,
                      reserved_bytes=XIP_SIZE,sha256=file_hash(xip))
    v['modular_system'] = ref
    write(destination / 'validation.json', v)
    artifact_identity(destination)
    return destination
