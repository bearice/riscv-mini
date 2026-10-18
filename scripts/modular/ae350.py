"""P1 host builds: integer ROM monitor and DDR-independent AE350 elaboration."""
import importlib
import subprocess
import re
from html.parser import HTMLParser
from pathlib import Path
from .storage import canonical, digest, file_hash, workspace_lock, write
from .environment import discover, identity, python_packages, output
from .firmware import BASE_FLAGS, ROLES, ninja_build

BSP = 'firmware/bsp/mega138k_ae350'


def _inputs(store, hardware=False):
    names = ['scripts/mini.py', *[p.relative_to(store.root).as_posix()
                                for p in sorted((store.root / 'scripts/modular').glob('*.py'))]]
    files = {'inputs/source/' + name: store.root / name for name in names}
    for path in sorted((store.root / BSP).iterdir()):
        if path.is_file():
            files['inputs/source/' + path.relative_to(store.root).as_posix()] = path
    if hardware:
        for path in sorted((store.root / 'gateware/machines').glob('*.py')):
            name = path.relative_to(store.root).as_posix()
            files['inputs/source/' + name] = path
        # Preserve installed Python implementation bytes, including the hard
        # macro adapter and bus/PLL logic; package version alone is insufficient.
        for name in ('migen', 'litex'):
            base = Path(importlib.import_module(name).__file__).parent
            for path in sorted(base.rglob('*.py')):
                files['inputs/python/' + name + '/' + path.relative_to(base).as_posix()] = path
    return files


def firmware(store, run, cpu, plan=False):
    files = _inputs(store)
    hashes = {name: file_hash(path) for name, path in files.items()}
    env = discover(ROLES, store.root)
    flags = [*BASE_FLAGS, '-march=rv32ima_zicsr_zifencei', '-mabi=ilp32', '-mcmodel=medany', '-mno-relax',
             '-fno-delete-null-pointer-checks', '-Wl,--build-id=none']
    config = dict(schema=1, machine='mega138k_ae350', purpose='P1-ROM-monitor',
                  sources=hashes, flags=flags, tools=identity(env),
                  rom_base=0x80000000, rom_bytes=8192, sram_base=0, sram_bytes=8192,
                  uart_base=0xf0300000, uart_clock_hz=100000000, qualification='compiled-not-board-tested')
    key = digest(dict(config=config, cpu=cpu))
    flags = [*flags, '-DMINI_P1_BUILD_ID="' + key[:12] + '"']
    config = dict(config, build_id=key[:12], resolved_flags=flags)
    found = store.lookup('bootloader', key)
    if found:
        run.step('bootloader', 'REUSE', found); return found
    if plan:
        run.step('bootloader', 'BUILD', reason='P1 integer ROM monitor inputs'); return None
    work = store.build / 'work/ae350-bootloader' / key
    with workspace_lock(work):
        ninja_build(work, [store.root / BSP / name for name in ('start.S', 'main.c')], flags, env, 'boot',
                    linker=store.root / BSP / 'link.ld', run=run, root=store.root)
        attributes = output([*env['bindings']['target.readelf'], '-h', '-A', str(work / 'boot.elf')])
        if '0x80000000' not in attributes or (work / 'boot.bin').stat().st_size > 8192:
            raise ValueError('Invalid AE350 ROM image entry or size')
        snapshot = {name: path.read_bytes() for name, path in files.items()}
        import hashlib
        if any(hashlib.sha256(data).hexdigest() != hashes[name] for name, data in snapshot.items()):
            raise ValueError('AE350 firmware inputs changed during build')
        snapshot.update({'outputs/boot.' + suffix: work / ('boot.' + suffix) for suffix in ('elf', 'bin', 'map')})
        snapshot['build.lock.json'] = canonical(config)
        snapshot['outputs/elf-attributes.txt'] = attributes.encode()
        found = store.publish('bootloader', key, snapshot, config=config, dependencies={'cpu': cpu})
    run.step('bootloader', 'BUILD', found); return found


def timing_report(work):
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(); self.parts = []
        def handle_data(self, data):
            self.parts.append(data.strip())
    parser = Text()
    parser.feed((work / 'gateware/impl/pnr/project_tr_content.html').read_text(errors='replace'))
    content = ' '.join(parser.parts)
    counts = {}
    for kind in ('Setup', 'Hold'):
        match = re.search(r'Numbers of ' + kind + r' Violated Endpoints\s+(\d+)', content)
        if not match:
            raise ValueError('Missing AE350 ' + kind + ' timing summary')
        counts[kind.lower()] = int(match[1])
    if any(counts.values()):
        raise ValueError('AE350 timing failed: ' + str(counts))
    return counts


def rtl(store, run, cpu, boot, board, component, plan=False, synthesize=False):
    files = _inputs(store, hardware=True)
    hashes = {name: file_hash(path) for name, path in files.items()}
    config = dict(schema=1, machine='mega138k_ae350', profile='P1-DDR-independent', sources=hashes,
                  cpu_integration='AE350_SOC-direct-macro', instruction_memory='fabric-ROM-AHB',
                  data_memory='fabric-SRAM-AHB', fabric_bus='native-AHB-only', data_memory_clock_hz=100000000,
                  tools=identity(discover(['python', 'gowin'], store.root)), packages=python_packages(),
                  core_hz=800000000, bus_hz=100000000, ddr=False, rom_bytes=8192, sram_bytes=8192,
                  synthesize=synthesize,
                  qualification='PnR-not-board-tested' if synthesize else 'generated-not-synthesized-not-board-tested')
    deps = {'cpu': cpu, 'bootloader': boot}
    key = digest(dict(config=config, dependencies=deps))
    found = store.lookup(component, key)
    if found:
        run.step(component, 'REUSE', found); return found
    if plan:
        run.step(component, 'BUILD', reason='P1 ROM/SRAM/AE350 RTL and implementation inputs'); return None
    from gateware.machines.mega138k_ae350 import MinimalSoC
    raw = (store.path(boot) / 'outputs/boot.bin').read_bytes()
    raw += bytes((-len(raw)) % 4)
    rom = [int.from_bytes(raw[n:n + 4], 'little') for n in range(0, len(raw), 4)]
    work = store.build / 'work/ae350-rtl' / key
    with workspace_lock(work):
        soc = MinimalSoC(board, rom)
        soc.platform.build(soc, build_dir=str(work / 'gateware'), run=False, build_name='mini_ae350')
        write(work / 'memory-map.json', dict(memories=dict(rom=dict(base=0x80000000, size=8192),
                                                         sram=dict(base=0, size=8192))))
        if synthesize:
            env = discover(['gowin'], store.root)
            with (work / 'gowin.log').open('w', encoding='utf-8') as log:
                result = subprocess.run([*env['bindings']['gowin'], 'run.tcl'], cwd=work / 'gateware',
                                        stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise ValueError('AE350 Gowin failed; inspect ' + str(work / 'gowin.log'))
            bitstream = work / 'gateware/impl/pnr/project.fs'
            if not bitstream.is_file():
                raise ValueError('AE350 Gowin did not produce a bitstream; inspect ' + str(work / 'gowin.log'))
            write(work / 'timing.json', timing_report(work))
        generated = {p.relative_to(work).as_posix(): p for p in (work / 'gateware').rglob('*') if p.is_file()}
        if synthesize:
            generated.update({'gowin.log': work / 'gowin.log', 'timing.json': work / 'timing.json'})
        generated['memory-map.json'] = work / 'memory-map.json'
        import hashlib
        snapshot = {name: path.read_bytes() for name, path in files.items()}
        if any(hashlib.sha256(data).hexdigest() != hashes[name] for name, data in snapshot.items()):
            raise ValueError('AE350 RTL inputs changed during generation')
        generated.update(snapshot)
        generated['build.lock.json'] = canonical(config)
        found = store.publish(component, key, generated, config=config, dependencies=deps)
    run.step(component, 'BUILD', found); return found


def build(store, run, args, cpu, board, plan):
    implemented = bool(getattr(args, 'synthesize', False) and args.target == 'gateware')
    boot = firmware(store, run, cpu, plan)
    artifact = boot
    if args.target != 'bootloader' and boot:
        artifact = rtl(store, run, cpu, boot, board, args.target, plan, getattr(args, 'synthesize', False))
    elif args.target != 'bootloader':
        run.step(args.target, 'BUILD', reason='requires the P1 ROM monitor output')
    if not plan and artifact:
        write(store.build / 'refs/machines/mega138k_ae350/candidates' / (run.id + '.json'),
              dict(machine='mega138k_ae350', artifact=artifact, run=run.id,
                   qualification='PnR-not-board-qualified' if implemented else 'host-generated-only'))
    run.finish(plan=plan, artifact=artifact, ready_for_execution=implemented and not plan, backend_available=True,
               qualification='PnR-not-board-qualified' if implemented else 'host-generated-only', hardware_qualified=False,
               remaining=['physical-part-and-board-revisions', *([] if implemented else ['PnR']), 'JTAG-UART-board-validation',
                          'timer-IRQ-verification', 'CPU-frequency-characterization'])
    return artifact
