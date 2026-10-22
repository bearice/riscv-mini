"""Independent SoC description and gateware generation with explicit locked flags."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[4]), str(Path(__file__).resolve().parents[4] / 'scripts')]
from scripts.modular.storage import ROOT, canonical, digest, file_hash, source_bytes, SOURCE_HASH_FORMAT, read, write, workspace_lock
from scripts.modular.environment import discover, identity, python_packages
from scripts.modular.machines.primer20k_vexriscv.contracts import hardware
from scripts.modular.workspace import working, clear_generated, invalidate_pnr, gateware_files, remove_file


# Stage A closure: this backend is the allowlist for the 20K RTL. Every path is
# repo-relative and must exist; a missing entry fails the build instead of
# silently shrinking the key. No other machine's files may appear here.
PRIMER20K_CLOSURE = (
    'gateware/__init__.py',
    'gateware/boards/__init__.py',
    'gateware/boards/primer20k_dock3713/board.json',
    'gateware/devices/__init__.py',
    'gateware/devices/audio/__init__.py',
    'gateware/devices/audio/core.py',
    'gateware/devices/audio/clock.py',
    'gateware/devices/gpio/__init__.py',
    'gateware/devices/gpio/core.py',
    'gateware/devices/microphone/__init__.py',
    'gateware/devices/microphone/core.py',
    'gateware/devices/video/__init__.py',
    'gateware/devices/video/core.py',
    'gateware/devices/ws2812/__init__.py',
    'gateware/devices/ws2812/core.py',
    'gateware/machines/primer20k_vexriscv/__init__.py',
    'gateware/machines/primer20k_vexriscv/bus.py',
    'gateware/machines/primer20k_vexriscv/config.py',
    'gateware/machines/primer20k_vexriscv/constraints.py',
    'gateware/machines/primer20k_vexriscv/csr_layout.py',
    'gateware/machines/primer20k_vexriscv/ddr_boot.py',
    'gateware/machines/primer20k_vexriscv/features.py',
    'gateware/machines/primer20k_vexriscv/flash_xip.py',
    'gateware/machines/primer20k_vexriscv/memory/gw2_ddr.py',
    'gateware/machines/primer20k_vexriscv/memory/native_dma.py',
    'gateware/machines/primer20k_vexriscv/memory/ports.py',
    'gateware/machines/primer20k_vexriscv/memory/shared_l2.py',
    'gateware/machines/primer20k_vexriscv/memory_map.py',
    'gateware/machines/primer20k_vexriscv/peripherals/audio.py',
    'gateware/machines/primer20k_vexriscv/peripherals/audio_clock.py',
    'gateware/machines/primer20k_vexriscv/peripherals/board_io.py',
    'gateware/machines/primer20k_vexriscv/peripherals/ethernet.py',
    'gateware/machines/primer20k_vexriscv/peripherals/ethernet_dma.py',
    'gateware/machines/primer20k_vexriscv/peripherals/ethernet_ring.py',
    'gateware/machines/primer20k_vexriscv/peripherals/microphone.py',
    'gateware/machines/primer20k_vexriscv/peripherals/sd.py',
    'gateware/machines/primer20k_vexriscv/peripherals/sd_dma.py',
    'gateware/machines/primer20k_vexriscv/peripherals/sddma.py',
    'gateware/machines/primer20k_vexriscv/peripherals/usb.py',
    'gateware/machines/primer20k_vexriscv/peripherals/usb_ultra.py',
    'gateware/machines/primer20k_vexriscv/peripherals/video.py',
    'gateware/machines/primer20k_vexriscv/rtl.py',
    'gateware/machines/primer20k_vexriscv/soc.py',
    'gateware/vendor/gowin_gw2a/gw2ddrphy.py',
    'gateware/vendor/litesdcard/LICENSE.litedram',
    'gateware/vendor/litesdcard/sdcore.py',
    'gateware/vendor/usb_fs_phy/LICENSE',
    'gateware/vendor/usb_fs_phy/usb_fs_phy.v',
    'gateware/vendor/usb_host/LICENSE',
    'gateware/vendor/usb_host/src_v/usbh_crc16.v',
    'gateware/vendor/usb_host/src_v/usbh_crc5.v',
    'gateware/vendor/usb_host/src_v/usbh_fifo.v',
    'gateware/vendor/usb_host/src_v/usbh_host.v',
    'gateware/vendor/usb_host/src_v/usbh_host_defs.v',
    'gateware/vendor/usb_host/src_v/usbh_sie.v',
    'gateware/vendor/vexriscv/VexRiscv_Mmu.v',
    'gateware/vendor/vexriscv/VexRiscv_Mmu.yaml',
    'gateware/vendor/vexriscv/generator.json',
    'scripts/primer20k_vexriscv/boot_image.py',
    'scripts/tools/build_recipe.py',
    'scripts/tools/build_records.py',
    'scripts/mini.py',
    'scripts/modular/compile.py',
    'scripts/modular/machines/__init__.py',
    'scripts/modular/machines/primer20k_vexriscv/contracts.py',
    'scripts/modular/environment.py',
    'scripts/modular/gowin.py',
    'scripts/modular/machines/primer20k_vexriscv/hardware.py',
    'scripts/modular/machines/primer20k_vexriscv/rtl.py',
    'scripts/modular/storage.py',
    'scripts/modular/worker.py',
    'scripts/modular/workspace.py',
    'scripts/tools/versions.py',
)


def source_closure(root):
    missing = [name for name in PRIMER20K_CLOSURE if not (root / name).is_file()]
    if missing:
        raise ValueError('Missing declared 20K input: ' + ', '.join(missing))
    return {name: 'source' for name in PRIMER20K_CLOSURE}


def source_files(root):
    from scripts.modular.storage import source_file_hash
    return {name: source_file_hash(root / name) for name in source_closure(root)}


def generate_soc(store, run, base_soc, cpu_ref, plan=False, validation=None):
    if store.get(cpu_ref)['config'].get('kind') == 'hard':
        raise ValueError('Hard CPU requires its machine RTL backend; cannot generate VexRiscv RTL')
    v = validation if validation is not None else store.get(base_soc)['config']['validation']
    inputs = source_files(store.root)
    env = discover(['python'], store.root)
    options = dict(v)
    config = dict(schema=1, options=options, sources=inputs, source_hash_format=SOURCE_HASH_FORMAT,
                  environment=identity(env), packages=python_packages())
    key = digest(dict(config=config, cpu=cpu_ref))
    found = store.lookup('soc-description', key)
    if found:
        run.step('soc-description', 'REUSE', found); return found
    if plan:
        run.step('soc-description', 'BUILD', reason='gateware source/configuration changed'); return None
    with working(store, run, 'primer20k_vexriscv', 'soc-description', key) as workspace:
        work = workspace.path
        clear_generated(work, ('cpu', 'gateware', 'software/include'))
        cpu_manifest = store.get(cpu_ref)
        names = [n for n in cpu_manifest['files'] if n.startswith('outputs/') and n.endswith('.v')]
        cpu_path = store.path(cpu_ref) / names[0] if names else None
        from scripts.modular.machines.primer20k_vexriscv.rtl import generate
        from gateware.machines.primer20k_vexriscv.features import Features
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
        from scripts.primer20k_vexriscv.boot_image import abi_tag
        cfg = dict(validation=v, contract=hardware(csr, v), legacy_abi_tag=abi_tag(csr), build=config)
        files = {'csr.json': work / 'csr.json', 'build.lock.json': canonical(config)}
        # A complete generated source tree, not only top-level Verilog.
        for folder in ('gateware', 'software/include'):
            for p in (work / folder).rglob('*'):
                if p.is_file(): files[p.relative_to(work).as_posix()] = source_bytes(p)
        if source_files(store.root) != inputs:
            raise ValueError('Hardware inputs changed during generation')
        ref = store.publish('soc-description', key, files, config=cfg, dependencies={'cpu': cpu_ref})
        workspace.success(ref)
    run.step('soc-description', 'BUILD', ref); return ref


def build_gateware(store, run, soc, boot, plan=False, synthesize=True):
    soc_data = store.get(soc); v = soc_data['config']['validation']
    env = discover(['python', *(['gowin'] if synthesize else [])], store.root)
    config = dict(schema=1, options=v, environment=identity(env), packages=python_packages(), synthesize=synthesize,
                  source_hash_format=SOURCE_HASH_FORMAT, sources=source_files(store.root))
    deps = {'soc-description': soc}
    if v['boot_mode'] == 'rom': deps['bootloader'] = boot
    key = digest(dict(config=config, dependencies=deps))
    found = store.lookup('gateware', key)
    if found:
        run.step('gateware', 'REUSE', found); return found
    if plan:
        run.step('gateware', 'BUILD', reason='hardware/ROM/vendor options changed'); return None
    with working(store, run, 'primer20k_vexriscv', 'gateware', key) as workspace:
        work = workspace.path
        clear_generated(work, ('cpu', 'software/include'))
        for path in (work / 'gateware').glob('*'):
            if path.is_file():
                remove_file(path)
        if synthesize:
            invalidate_pnr(work)
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
        def attempt(log_path):
            if synthesize:
                invalidate_pnr(work)
            with log_path.open('w', encoding='utf-8') as log:
                proc = subprocess.Popen(command, cwd=store.root, env=process, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
                for line in proc.stdout:
                    log.write(line); log.flush()
                    if any(word in line for word in ('ERROR', 'Error', 'Stage', 'Routing', 'Finished', 'Synthesis', 'License', 'license')):
                        print(line, end='', flush=True)
                return proc.wait()
        from scripts.modular.gowin import run_with_license_retry
        returncode, final_log = run_with_license_retry(attempt, run.path, prefix='gateware',
                                                      delays=(5, 15, 30) if synthesize else ())
        shutil.copyfile(final_log, run.path / 'gateware.log')
        run.result['gowin_attempts'] = read(run.path / 'gateware-attempts.json')
        run.save()
        if returncode:
            raise RuntimeError('Gateware generation failed; see ' + str(run.path / 'gateware-attempts.json'))
        report = dict(v, synthesis_requested=synthesize, board_test='not performed', component_versions={})
        if synthesize:
            from scripts.modular.machines.primer20k_vexriscv.rtl import pnr_report
            timing, resources = pnr_report(work)
            if any(timing.values()): raise ValueError('Timing violations: ' + str(timing))
            report.update(timing_violated_endpoints=timing, resources=resources, bitstream_sha256=file_hash(work/'gateware/riscv_mini.fs'))
        files = {'build.lock.json': canonical(config)}
        files['gateware-attempts.json'] = run.path / 'gateware-attempts.json'
        files.update({'logs/' + p.name: p for p in run.path.glob('gateware-attempt-*.log')})
        for path in gateware_files(work, synthesize):
            relative = path.relative_to(work/'gateware')
            files['project/' + relative.as_posix()] = path if 'impl' in relative.parts else source_bytes(path)
        if synthesize: files['outputs/riscv_mini.fs'] = work/'gateware/riscv_mini.fs'
        ref = store.publish('gateware', key, files, config=dict(build=config, validation=report), dependencies=deps)
        workspace.success(ref)
    run.step('gateware', 'BUILD', ref); return ref
