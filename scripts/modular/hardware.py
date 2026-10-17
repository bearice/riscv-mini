"""Independent SoC description and gateware generation with explicit locked flags."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from .storage import ROOT, canonical, digest, file_hash, read, write, workspace_lock
from .environment import discover, identity, python_packages
from .firmware import source_files
from .contracts import hardware


def generate_soc(store, run, base_soc, cpu_ref, plan=False, validation=None):
    if store.get(cpu_ref)['config'].get('kind') == 'hard':
        raise ValueError('Hard CPU requires its machine RTL backend; cannot generate VexRiscv RTL')
    v = validation if validation is not None else store.get(base_soc)['config']['validation']
    inputs = source_files(store.root, ['gateware', 'gateware/board.json', 'scripts/modular/rtl.py', 'scripts/boot_image.py',
                                      'scripts/build_records.py', 'scripts/build_recipe.py', 'scripts/versions.py',
                                      'scripts/modular/hardware.py', 'scripts/modular/contracts.py', 'scripts/modular/storage.py'])
    env = discover(['python'], store.root)
    options = dict(v)
    config = dict(schema=1, options=options, sources=inputs, environment=identity(env), packages=python_packages())
    key = digest(dict(config=config, cpu=cpu_ref))
    found = store.lookup('soc-description', key)
    if found:
        run.step('soc-description', 'REUSE', found); return found
    if plan:
        run.step('soc-description', 'BUILD', reason='gateware source/configuration changed'); return None
    work = store.build / 'work/soc-description' / key
    with workspace_lock(work):
        cpu_manifest = store.get(cpu_ref)
        names = [n for n in cpu_manifest['files'] if n.startswith('outputs/') and n.endswith('.v')]
        cpu_path = store.path(cpu_ref) / names[0] if names else None
        from .rtl import generate
        from gateware.features import Features
        # The CPU is consumed by exact digest. Place matching metadata next to it.
        if cpu_path and 'inputs/generator.json' in cpu_manifest['files']:
            folder = work / 'cpu'; folder.mkdir(exist_ok=True)
            shutil.copyfile(cpu_path, folder / cpu_path.name)
            shutil.copyfile(store.path(cpu_ref) / 'inputs/generator.json', folder / 'generator.json')
            for name in names:
                yaml = name[:-2] + '.yaml'
                if yaml in cpu_manifest['files']: shutil.copyfile(store.path(cpu_ref) / yaml, folder / Path(yaml).name)
            cpu_path = folder / cpu_path.name
        scratch = work / 'tmp'; scratch.mkdir(exist_ok=True)
        old = {n: os.environ.get(n) for n in ('TEMP', 'TMP', 'TMPDIR')}
        try:
            os.environ.update({n: str(scratch) for n in old})
            generate(work, features=Features(**v['features']), sd_backend=v['sd_backend'],
                     hierarchical=v['verilog_mode'] != 'flat', usb_backend=v['usb_backend'],
                     cpu_variant=v['cpu_variant'], cpu_verilog=cpu_path, sd_profile=v['sd_profile'],
                     audio_clock=v['audio_clock'], deep_verilog=v['rtl_hierarchy']=='deep',
                     place_option=v['place_option'], route_option=v['route_option'], l2_size=v['l2_size_bytes'],
                     rom_size=v['rom_size_bytes'] or 8192, boot_mode=v['boot_mode'])
        finally:
            for name, value in old.items():
                if value is None: os.environ.pop(name, None)
                else: os.environ[name] = value
        csr = read(work / 'csr.json')
        from scripts.boot_image import abi_tag
        cfg = dict(validation=v, contract=hardware(csr, v), legacy_abi_tag=abi_tag(csr), build=config)
        files = {'csr.json': work / 'csr.json', 'build.lock.json': canonical(config)}
        # A complete generated source tree, not only top-level Verilog.
        for folder in ('gateware', 'software/include'):
            for p in (work / folder).rglob('*'):
                if p.is_file(): files[p.relative_to(work).as_posix()] = p
        if source_files(store.root, ['gateware', 'gateware/board.json', 'scripts/modular/rtl.py', 'scripts/boot_image.py',
                                    'scripts/build_records.py', 'scripts/build_recipe.py', 'scripts/versions.py',
                                    'scripts/modular/hardware.py', 'scripts/modular/contracts.py', 'scripts/modular/storage.py']) != inputs:
            raise ValueError('Hardware inputs changed during generation')
        ref = store.publish('soc-description', key, files, config=cfg, dependencies={'cpu': cpu_ref})
    run.step('soc-description', 'BUILD', ref); return ref


def build_gateware(store, run, soc, boot, plan=False, synthesize=True):
    soc_data = store.get(soc); v = soc_data['config']['validation']
    env = discover(['python', *(['gowin'] if synthesize else [])], store.root)
    config = dict(schema=1, options=v, environment=identity(env), packages=python_packages(), synthesize=synthesize,
                  sources=source_files(store.root, ['gateware', 'gateware/board.json', 'scripts/modular/rtl.py', 'scripts/mini.py', 'scripts/modular/worker.py', 'scripts/modular/hardware.py']))
    deps = {'soc-description': soc}
    if v['boot_mode'] == 'rom': deps['bootloader'] = boot
    key = digest(dict(config=config, dependencies=deps))
    found = store.lookup('gateware', key)
    if found:
        run.step('gateware', 'REUSE', found); return found
    if plan:
        run.step('gateware', 'BUILD', reason='hardware/ROM/vendor options changed'); return None
    work = store.build / 'work/gateware' / key
    with workspace_lock(work):
        # Generate final RTL using the same explicit CPU and hardware config.
        cpu = soc_data['dependencies']['cpu']; cm = store.get(cpu)
        cpus = [n for n in cm['files'] if n.startswith('outputs/') and n.endswith('.v')]
        cpu_path = None
        if cpus:
            folder = work / 'cpu'; folder.mkdir(exist_ok=True)
            cpu_path = folder / Path(cpus[0]).name
            shutil.copyfile(store.path(cpu) / cpus[0], cpu_path)
            if 'inputs/generator.json' in cm['files']: shutil.copyfile(store.path(cpu) / 'inputs/generator.json', folder / 'generator.json')
            for name in cm['files']:
                if name.startswith('outputs/') and name.endswith('.yaml'): shutil.copyfile(store.path(cpu) / name, folder / Path(name).name)
        request = work / 'rtl-request.json'
        write(request, dict(validation=v, cpu=str(cpu_path) if cpu_path else None,
                            output=str(work), rom=str(store.path(boot) / 'outputs/boot.bin'), synthesize=synthesize))
        command = [sys.executable, str(store.root / 'scripts/mini.py'), '_worker', 'rtl', str(request)]
        process = dict(os.environ)
        if synthesize: process['PATH'] = str(Path(env['bindings']['gowin'][0]).parent) + os.pathsep + process.get('PATH','')
        with (run.path / 'gateware.log').open('w', encoding='utf-8') as log:
            proc = subprocess.Popen(command, cwd=store.root, env=process, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                log.write(line); log.flush()
                if any(word in line for word in ('ERROR', 'Error', 'Stage', 'Routing', 'Finished', 'Synthesis')): print(line, end='', flush=True)
            if proc.wait(): raise RuntimeError('Gateware generation failed; see ' + str(run.path / 'gateware.log'))
        report = dict(v, synthesis_requested=synthesize, board_test='not performed', component_versions={})
        if synthesize:
            from .rtl import pnr_report
            timing, resources = pnr_report(work)
            if any(timing.values()): raise ValueError('Timing violations: ' + str(timing))
            report.update(timing_violated_endpoints=timing, resources=resources, bitstream_sha256=file_hash(work/'gateware/riscv_mini.fs'))
        files = {'build.lock.json': canonical(config)}
        for path in (work / 'gateware').rglob('*'):
            if path.is_file(): files['project/' + path.relative_to(work/'gateware').as_posix()] = path
        if synthesize: files['outputs/riscv_mini.fs'] = work/'gateware/riscv_mini.fs'
        ref = store.publish('gateware', key, files, config=dict(build=config, validation=report), dependencies=deps)
    run.step('gateware', 'BUILD', ref); return ref
