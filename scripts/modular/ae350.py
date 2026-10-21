"""AE350 firmware and gateware builds, including the platform ROM bootloader."""
import importlib
import subprocess
import re
from html.parser import HTMLParser
from pathlib import Path
from .storage import canonical, digest, file_hash, write, read, text_file
from .workspace import working, clear_generated, invalidate_pnr, gateware_files, cached_implementation, record_implementation, remove_file
from .environment import discover, identity, python_packages, output
from .compile import BASE_FLAGS, ROLES, ninja_build

AE350_CLOSURE = (
    'gateware/__init__.py',
    'gateware/boards/__init__.py',
    'gateware/boards/mega138k_pro/board.json',
    'gateware/boards/mega138k_pro/io_map.py',
    'gateware/devices/__init__.py',
    'gateware/devices/gpio/__init__.py',
    'gateware/devices/gpio/core.py',
    'gateware/devices/ws2812/__init__.py',
    'gateware/devices/ws2812/core.py',
    'gateware/machines/mega138k_ae350/__init__.py',
    'gateware/machines/mega138k_ae350/address_map.py',
    'gateware/machines/mega138k_ae350/clock.py',
    'gateware/machines/mega138k_ae350/cpu.py',
    'gateware/machines/mega138k_ae350/ddr3_ahb.py',
    'gateware/machines/mega138k_ae350/interconnect.py',
    'gateware/machines/mega138k_ae350/machine.json',
    'gateware/machines/mega138k_ae350/peripherals/board_io.py',
    'gateware/machines/mega138k_ae350/peripherals/board_sense.py',
    'gateware/machines/mega138k_ae350/platform.py',
    'gateware/machines/mega138k_ae350/soc.py',
    'gateware/vendor/gowin_gw5ast/ae350_soc/DDR3_TOP.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/DDR3_define.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/ddr3_1_4code_hs.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/ddr3_name.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/fifo_top_128to32.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/fifo_top_32to128.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/gw_dtcm.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/gw_dtcm_config.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/gw_itcm.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/gw_itcm_config.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/gwmc_local_param.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/gwmc_param.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/riscv_ae350_config.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/riscv_ae350_ddr3.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/riscv_ae350_flash.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/riscv_ae350_soc.v',
    'gateware/vendor/gowin_gw5ast/ae350_soc/riscv_ae350_top.v',
    'gateware/vendor/gowin_gw5ast/gowin_ddr3_400/PROVENANCE.json',
    'gateware/vendor/gowin_gw5ast/gowin_ddr3_400/ddr3_memory_interface.ipc',
    'gateware/vendor/gowin_gw5ast/gowin_ddr3_400/ddr3_memory_interface.v',
    'gateware/vendor/gowin_gw5ast/gowin_ddr3_400/gowin_pll_ddr3.ipc',
    'gateware/vendor/gowin_gw5ast/gowin_ddr3_400/gowin_pll_ddr3.v',
)

BOOTLOADER = 'firmware/bootloader/ae350'
PAYLOAD = 'firmware/diagnostics/ae350_memory'
PROFILES = ('ddr',)


def _inputs(store, hardware=False, profile='ddr'):
    names = ['scripts/mini.py', *[p.relative_to(store.root).as_posix()
                                for p in sorted((store.root / 'scripts/modular').glob('*.py'))]]
    files = {'inputs/source/' + name: store.root / name for name in names}
    missing = [name for name in AE350_CLOSURE if not (store.root / name).is_file()]
    if missing:
        raise ValueError('Missing declared AE350 input: ' + ', '.join(missing))
    files.update({'inputs/source/' + name: store.root / name for name in AE350_CLOSURE})
    trees = [BOOTLOADER]
    for tree in trees:
        for path in sorted((store.root / tree).iterdir()):
            if path.is_file():
                files['inputs/source/' + path.relative_to(store.root).as_posix()] = path
    if hardware:
        # Preserve installed Python implementation bytes, including the hard
        # macro adapter and bus/PLL logic; package version alone is insufficient.
        for name in ('migen', 'litex'):
            base = Path(importlib.import_module(name).__file__).parent
            for path in sorted(base.rglob('*.py')):
                files['inputs/python/' + name + '/' + path.relative_to(base).as_posix()] = path
    return files


def firmware(store, run, cpu, plan=False, profile='ddr'):
    if profile not in PROFILES:
        raise ValueError('Unknown AE350 profile: ' + profile)
    files = _inputs(store, profile=profile)
    hashes = {name: file_hash(path) for name, path in files.items()}
    env = discover(ROLES, store.root)
    flags = [*BASE_FLAGS, '-march=rv32ima_zicsr_zifencei', '-mabi=ilp32', '-mcmodel=medany', '-mno-relax',
             '-fno-delete-null-pointer-checks', '-Wl,--build-id=none']
    from gateware.machines.mega138k_ae350 import address_map as ae350_map
    from gateware.machines.mega138k_ae350 import address_map as ae350_map
    ae350_map.require()
    config = dict(schema=1, machine='mega138k_ae350', profile='AE350-ROM-bootloader',
                  purpose='AE350-ROM-bootloader',
                  sources=hashes, flags=flags, tools=identity(env),
                  rom_base=ae350_map.ROM_BASE, rom_bytes=ae350_map.ROM_SIZE,
                  dlm_base=ae350_map.DLM_BASE, dlm_bytes=ae350_map.DLM_SIZE,
                  results_base=ae350_map.RESULTS_BASE,
                  uart_base=0xf0300000, uart_clock_hz=ae350_map.FABRIC_HZ,
                  ddr_window_base=ae350_map.DDR_BASE, ddr_window_bytes=ae350_map.DDR_SIZE,
                  image_abi=ae350_map.image_abi(),
                  payload_base=ae350_map.DDR_CODE,
                  payload_bytes=ae350_map.DDR_CODE_MAX,
                  payload_stack_top=ae350_map.PAYLOAD_STACK_TOP,
                  local_memory=dict(ilm_base=ae350_map.ILM_BASE, ilm_bytes=ae350_map.ILM_SIZE,
                                    dlm_base=ae350_map.DLM_BASE, dlm_bytes=ae350_map.DLM_SIZE,
                                    rom_private_stack='DLM', rom_hot_code='ILM', ecc_check='disabled'),
                  qualification='compiled-not-board-tested')
    key = digest(dict(config=config, cpu=cpu))
    macro = 'MINI_AE350_BOOT_BUILD_ID'
    config = dict(config, build_id=key[:12], resolved_flags=flags, identity_macro=macro)
    found = store.lookup('bootloader', key)
    if found:
        run.step('bootloader', 'REUSE', found); return found
    if plan:
        run.step('bootloader', 'BUILD', reason='AE350 platform ROM bootloader inputs'); return None
    with working(store, run, 'mega138k_ae350', 'bootloader', key) as workspace:
        work = workspace.path
        from gateware.machines.mega138k_ae350 import address_map as ae350_map
        ae350_map.require()
        gen = work / 'generated'
        gen.mkdir(parents=True, exist_ok=True)
        text_file(gen / 'ae350_address_map.h', ae350_map.c_header())
        text_file(gen / 'ae350_map.ld', ae350_map.ld_fragment())
        text_file(gen / 'identity.h', '#define %s "%s"\n' % (macro, key[:12]))
        flags = [*flags, '-I' + str(gen), '-Wl,-L,' + str(gen)]
        bsp = store.root / BOOTLOADER
        ninja_build(work, [bsp / name for name in ('start.S', 'main.c')], flags, env, 'boot',
                    linker=bsp / 'link.ld', run=run, root=store.root, identity_header=gen / 'identity.h')
        attributes = output([*env['bindings']['target.readelf'], '-h', '-A', str(work / 'boot.elf')])
        from gateware.machines.mega138k_ae350 import address_map as ae350_map
        rom_limit = ae350_map.ROM_CODE_BYTES
        if '0x80000000' not in attributes or (work / 'boot.bin').stat().st_size > rom_limit:
            raise ValueError('Invalid AE350 ROM image entry or size (limit %d bytes)' % rom_limit)
        snapshot = {name: path.read_bytes() for name, path in files.items()}
        import hashlib
        if any(hashlib.sha256(data).hexdigest() != hashes[name] for name, data in snapshot.items()):
            raise ValueError('AE350 firmware inputs changed during build')
        snapshot.update({'outputs/boot.' + suffix: work / ('boot.' + suffix) for suffix in ('elf', 'bin', 'map')})
        snapshot['outputs/elf-attributes.txt'] = attributes.encode()
        snapshot['outputs/ae350_address_map.h'] = gen / 'ae350_address_map.h'
        snapshot['outputs/ae350_map.ld'] = gen / 'ae350_map.ld'
        snapshot['build.lock.json'] = canonical(config)
        found = store.publish('bootloader', key, snapshot, config=config, dependencies={'cpu': cpu})
        workspace.success(found)
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
    raw = (work / 'gateware/impl/pnr/project.tr').read_text(errors='replace')
    pulse_slacks = [float(v) for v in re.findall(r'MPW Summary:\s*Slack:\s*([-+\d.]+)', raw)]
    if not pulse_slacks:
        raise ValueError('Missing AE350 minimum pulse width report')
    counts['min_pulse_width_slack_ns'] = min(pulse_slacks)
    if min(pulse_slacks) < 0:
        raise ValueError('AE350 minimum pulse width failed: ' + str(counts))
    return counts


def incremental_seed(store, previous, config, board, mode):
    """Only a verified successful artifact can seed a mutable work area."""
    if mode == 'full':
        return None
    ref = previous.get('artifact') if previous else None
    if ref:
        manifest = store.get(ref)
        old = manifest['config']
        compatible = (ref['component'] == 'gateware' and old.get('synthesize') and
                      all(old.get(k) == config[k] for k in ('tools', 'ddr_profile', 'core_hz', 'bus_hz', 'data_memory_clock_hz')))
        if compatible:
            board_input = store.path(ref) / 'inputs/source/gateware/boards/mega138k_pro/board.json'
            if not board_input.is_file():
                compatible = False
            else:
                old_board = read(board_input)
                compatible = all(old_board[k] == board[k] for k in ('reference_device', 'reference_part'))
        member = 'gateware/impl/pnr/project.' + ('p' if mode == 'place' else 'pr')
        if compatible and member in manifest['files']:
            timing_report(store.path(ref))
            return dict(artifact=ref, member=member, sha256=manifest['files'][member],
                        mode='place' if mode == 'place' else 'pnr')
    if mode != 'auto':
        raise ValueError('No compatible successful PnR baseline in this workspace; first build with --pnr-mode full')
    return None


def implementation_flow(text, seed):
    flow = dict(requested=seed['mode'] if seed else 'full', actual=seed['mode'] if seed else 'full')
    warnings = [line.strip() for line in text.splitlines()
                if 'WARN' in line and ('matching' in line.lower() or 'incremental' in line.lower())]
    if warnings:
        flow['warnings'] = warnings
    match = re.search(r'Placement matching\(([\d.]+)\).*?threshold value\(([\d.]+)\)', text)
    if match:
        flow.update(placement_match_percent=float(match[1]), minimum_match_percent=float(match[2]))
    if 'the default placement flow will be run' in text:
        flow.update(actual='full', fallback='Gowin rejected incremental placement matching')
    return flow


def rtl(store, run, cpu, boot, board, component, plan=False, synthesize=False, profile='ddr', pnr_mode='full'):
    if profile not in PROFILES:
        raise ValueError('Unknown AE350 profile: ' + profile)
    files = _inputs(store, hardware=True, profile=profile)
    hashes = {name: file_hash(path) for name, path in files.items()}
    from gateware.machines.mega138k_ae350 import address_map as ae350_map
    from .ddr_inputs import DDR_PROFILE
    from gateware.machines.mega138k_ae350 import address_map as ae350_map
    config = dict(schema=1, machine='mega138k_ae350', profile='AE350-DDR3-' + DDR_PROFILE['name'],
                  sources=hashes, cpu_integration='AE350-vendor-ITCM-wrapper',
                  instruction_memory='vendor-ITCM-bootstrap+hard-ILM', data_memory='hard-DLM+DDR3-IP',
                  fabric_bus='native-AHB-burst+async-FIFO', data_memory_clock_hz=ae350_map.DATA_HZ,
                  tools=identity(discover(['python', 'gowin'], store.root)), packages=python_packages(),
                  core_hz=800000000, bus_hz=ae350_map.FABRIC_HZ, ddr_profile=DDR_PROFILE,
                  rom_bytes=ae350_map.ROM_SIZE, fabric_sram_bytes=0,
                  ddr_ck_mhz=400,
                  synthesize=synthesize,
                  qualification='PnR-not-board-tested' if synthesize else 'generated-not-synthesized-not-board-tested')
    deps = {'cpu': cpu, 'bootloader': boot}
    config['pnr_mode'] = pnr_mode if synthesize else 'full'
    input_key = digest(dict(config=config, dependencies=deps))
    found = cached_implementation(store, component, input_key)
    if found:
        run.step(component, 'REUSE', found); return found
    if plan:
        run.step(component, 'BUILD', reason='Vendor ITCM/hard-local-memory/DDR3 inputs'); return None
    from gateware.machines.mega138k_ae350.soc import DDRSoC
    raw = (store.path(boot) / 'outputs/boot.bin').read_bytes()
    raw += bytes((-len(raw)) % 4)
    rom = [int.from_bytes(raw[n:n + 4], 'little') for n in range(0, len(raw), 4)]
    with working(store, run, 'mega138k_ae350', component, input_key) as workspace:
        work = workspace.path
        found = cached_implementation(store, component, input_key)
        if found:
            workspace.success(found, implementation=synthesize)
            run.step(component, 'REUSE', found); return found
        seed = incremental_seed(store, workspace.implementation, config, board, pnr_mode) if synthesize else None
        config.update(input_key=input_key, incremental_pnr=seed)
        if seed:
            deps['pnr-baseline'] = seed['artifact']
        key = digest(dict(config=config, dependencies=deps))
        # Generated sources must not accumulate after module removal. Vendor
        # intermediates remain available in impl across attempts.
        clear_generated(work, ('ae350-ip',))
        gate = work / 'gateware'
        for path in gate.glob('*'):
            if path.is_file():
                remove_file(path)
        from .ddr_inputs import DDR_PROFILE
        init = work / 'itcm-init'
        init.mkdir(parents=True, exist_ok=True)
        image = raw.ljust(32768, b'\0')
        for lane in range(4):
            (init / ('itcm%d' % lane)).write_text(
                ''.join('%02x\n' % byte for byte in image[lane::4]), encoding='ascii')
        ip_out = work / 'ae350-ip'
        ip_out.mkdir(parents=True, exist_ok=True)
        vendor = store.root / 'gateware/vendor/gowin_gw5ast/ae350_soc'
        # Vendor wrapper/netlist selects memories through these generated
        # includes, as in the reference project. Keep all includes beside
        # the encrypted sources so no stale installation config can win.
        for name in ('riscv_ae350_top.v', 'riscv_ae350_soc.v', 'gw_itcm.v', 'gw_dtcm.v',
                     'riscv_ae350_config.v', 'gw_dtcm_config.v'):
            (ip_out / name).write_bytes((vendor / name).read_bytes())
        (ip_out / 'gw_itcm_config.v').write_text(
            '`define ITCM_SIZE 13\n`define ITCM_INIT_PATH "%s"\n' % init.as_posix(), encoding='utf-8')
        soc = DDRSoC(board, soc_sources_dir=ip_out)
        if seed:
            baseline = work / ('baseline.' + ('p' if seed['mode'] == 'place' else 'pr'))
            baseline.write_bytes((store.path(seed['artifact']) / seed['member']).read_bytes())
            if file_hash(baseline) != seed['sha256']:
                raise ValueError('Incremental PnR seed changed while copying')
            option = 'inc_place' if seed['mode'] == 'place' else 'inc_pnr'
            soc.platform.toolchain.options[option] = '{' + baseline.as_posix() + '}'
        run.result['incremental_pnr'] = seed or dict(mode='full')
        run.save()
        soc.platform.build(soc, build_dir=str(work / 'gateware'), run=False, build_name='mini_ae350_ddr')
        from gateware.machines.mega138k_ae350 import address_map as ae350_map
        ae350_map.require()
        write(work / 'memory-map.json', dict(
            memories=dict(rom=dict(base=ae350_map.ROM_BASE, size=ae350_map.ROM_SIZE,
                                   implementation='vendor-ITCM'),
                          ilm=dict(base=ae350_map.ILM_BASE, size=ae350_map.ILM_SIZE),
                          dlm=dict(base=ae350_map.DLM_BASE, size=ae350_map.DLM_SIZE,
                                   results=ae350_map.RESULTS_BASE,
                                   stack_top=ae350_map.ROM_STACK_TOP),
                          ddr=dict(base=ae350_map.DDR_BASE, size=DDR_PROFILE['window_bytes'],
                                   dram_geometry=DDR_PROFILE['geometry'],
                                   offsets=dict(ram_offset=0, reserved_top_bytes=0),
                                   cache_line_bytes=32)),
            windows=[dict(name=n, base=b, size=sz, status=st) for n, b, sz, st in ae350_map.WINDOWS],
            ddr_profile=DDR_PROFILE))
        if synthesize:
            env = discover(['gowin'], store.root)
            from .gowin import run_with_license_retry
            def attempt(log_path):
                invalidate_pnr(work)
                with log_path.open('w', encoding='utf-8') as log:
                    return subprocess.run([*env['bindings']['gowin'], 'run.tcl'], cwd=work / 'gateware',
                                          stdout=log, stderr=subprocess.STDOUT).returncode
            returncode, final_log = run_with_license_retry(attempt, run.path)
            import shutil
            shutil.copyfile(final_log, work / 'gowin.log')
            shutil.copyfile(work / 'gowin.log', run.path / 'gowin.log')
            run.result['gowin_attempts'] = read(run.path / 'gowin-attempts.json')
            flow = implementation_flow((work / 'gowin.log').read_text(errors='replace'), seed)
            run.result['implementation_flow'] = flow
            run.save()
            write(work / 'pnr-flow.json', flow)
            if flow.get('fallback'):
                print('Gowin incremental fallback:', flow, flush=True)
            if returncode:
                raise ValueError('AE350 Gowin failed (exit %s); inspect %s' % (returncode, run.path / 'gowin-attempts.json'))
            bitstream = work / 'gateware/impl/pnr/project.fs'
            if not bitstream.is_file():
                raise ValueError('AE350 Gowin did not produce a bitstream; inspect ' + str(work / 'gowin.log'))
            write(work / 'timing.json', timing_report(work))
        generated = {p.relative_to(work).as_posix(): p for p in gateware_files(work, synthesize)}
        generated.update({p.relative_to(work).as_posix(): p for directory in (init, ip_out)
                          for p in directory.rglob('*') if p.is_file()})
        if synthesize:
            generated.update({'gowin.log': work / 'gowin.log', 'timing.json': work / 'timing.json',
                              'pnr-flow.json': work / 'pnr-flow.json'})
            generated['gowin-attempts.json'] = run.path / 'gowin-attempts.json'
            generated.update({'logs/' + p.name: p for p in run.path.glob('gowin-attempt-*.log')})
        generated['memory-map.json'] = work / 'memory-map.json'
        import hashlib
        snapshot = {name: path.read_bytes() for name, path in files.items()}
        if any(hashlib.sha256(data).hexdigest() != hashes[name] for name, data in snapshot.items()):
            raise ValueError('AE350 RTL inputs changed during generation')
        generated.update(snapshot)
        generated['build.lock.json'] = canonical(config)
        found = store.publish(component, key, generated, config=config, dependencies=deps)
        record_implementation(store, component, input_key, found)
        workspace.success(found, implementation=synthesize)
    run.step(component, 'BUILD', found); return found


def payload(store, run, cpu, args, profile, plan=False):
    """Diagnostic/user payload as its own artifact.

    It depends on the CPU and on the address contract, never on the ROM, so
    changing the diagnostics rebuilds only this artifact and the image.
    """
    if profile != 'ddr':
        raise ValueError('AE350 payloads require --ae350-profile ddr')
    from gateware.machines.mega138k_ae350 import address_map as ae350_map
    ae350_map.require()
    files = {name: path for name, path in _inputs(store, profile='ddr').items()
             if name.startswith('inputs/source/scripts/')}
    runtime = store.root / 'firmware/bsp/mega138k_ae350_payload'
    for path in sorted(runtime.iterdir()):
        if path.is_file():
            files['inputs/payload/runtime/' + path.name] = path
    units = ['runtime/' + path.name for path in sorted(runtime.iterdir())
             if path.suffix in ('.c', '.S')]
    source = getattr(args, 'source', None)
    directory = getattr(args, 'source_dir', None)
    if source is not None and directory is not None:
        raise ValueError('Choose --source or --source-dir, not both')
    diagnostic = source is None and directory is None
    if getattr(args, 'libc', 'none') != 'none':
        raise ValueError('AE350 payloads currently support freestanding --libc none only')
    def project(label, paths, base, compile_all=False):
        for path in paths:
            key = label + '/' + path.relative_to(base).as_posix()
            files['inputs/payload/' + key] = path
            if compile_all and path.suffix in ('.c', '.S'):
                units.append(key)
    if diagnostic:
        source = store.root / PAYLOAD / 'main.c'
    if directory is not None:
        directory = Path(directory).resolve()
        project('application', sorted(p for p in directory.rglob('*')
                if p.is_file() and p.suffix in ('.c', '.S', '.h')), directory, True)
    else:
        source = Path(source).resolve()
        if not source.is_file() or source.suffix not in ('.c', '.S'):
            raise ValueError('--source must be an existing C or assembly source')
        project('application', [source, *sorted(source.parent.glob('*.h'))], source.parent, True)
    for index, path in enumerate(getattr(args, 'extra_source', [])):
        path = Path(path).resolve()
        project('extra%d' % index, [path], path.parent, True)
    for index, directory in enumerate(getattr(args, 'include_dir', [])):
        directory = Path(directory).resolve()
        project('include%d' % index, sorted(p for p in directory.rglob('*') if p.is_file()), directory)
    for helper in getattr(args, 'helper', []):
        if helper != 'string':
            raise ValueError('Unsupported AE350 helper: ' + helper)
        path = store.root / 'firmware/drivers/string.c'
        project('helper', [path], path.parent, True)
    if not any(unit.startswith('application/') for unit in units):
        raise ValueError('No application translation units were selected')
    files['inputs/source/gateware/machines/mega138k_ae350/address_map.py'] = store.root / 'gateware/machines/mega138k_ae350/address_map.py'
    files['inputs/source/gateware/boards/mega138k_pro/io_map.py'] = store.root / 'gateware/boards/mega138k_pro/io_map.py'
    files['inputs/source/gateware/boards/mega138k_pro/board.json'] = (
        store.root / 'gateware/boards/mega138k_pro/board.json')
    hashes = {name: file_hash(path) for name, path in files.items()}
    env = discover(ROLES, store.root)
    optimize = getattr(args, 'optimize', '-Os')
    if optimize not in ('-O0', '-O1', '-O2', '-O3', '-Os', '-Oz', '-Og'):
        raise ValueError('Unsupported optimization level')
    extra_flags = getattr(args, 'cflag', [])
    if any(flag.startswith(('-march', '-mabi', '-T', '-Wl', '-I', '-include', '-imacros', '-isystem', '-iquote', '@')) for flag in extra_flags):
        raise ValueError('Use declared source/include options; ISA and linker overrides are unsupported')
    flags = [*[flag for flag in BASE_FLAGS if not flag.startswith('-O')], optimize,
             '-march=rv32ima_zicsr_zifencei', '-mabi=ilp32', '-mcmodel=medany', '-mno-relax',
             '-fno-delete-null-pointer-checks', '-Wl,--build-id=none',
             *['-D' + value for value in getattr(args, 'define', [])], *extra_flags]
    config = dict(schema=1, machine='mega138k_ae350', purpose='AE350-memory-diagnostics' if diagnostic else 'AE350-application',
                  binary='outputs/payload.bin', translation_units=units, sources=hashes, tools=identity(env), flags=flags,
                  entry=ae350_map.DDR_CODE, max_bytes=ae350_map.DDR_CODE_MAX,
                  stack_top=ae350_map.PAYLOAD_STACK_TOP, image_abi=ae350_map.image_abi(),
                  parameters='outputs/parameters.json' if diagnostic else None,
                  qualification='compiled-not-board-tested')
    key = digest(dict(config=config, cpu=cpu))
    config = dict(config, build_id=key[:12])
    found = store.lookup('payload', key)
    if found:
        run.step('payload', 'REUSE', found); return found
    if plan:
        run.step('payload', 'BUILD', reason='AE350 memory diagnostic payload'); return None
    with working(store, run, 'mega138k_ae350', 'payload', key) as workspace:
        work = workspace.path
        gen = work / 'generated'
        gen.mkdir(parents=True, exist_ok=True)
        text_file(gen / 'ae350_address_map.h', ae350_map.c_header())
        text_file(gen / 'ae350_map.ld', ae350_map.ld_fragment())
        flags = [*flags, '-I' + str(gen), '-Wl,-L,' + str(gen)]
        text_file(gen / 'identity.h', '#define MINI_APP_BUILD_ID "%s"\n' % key[:12])
        staged = work / 'source'
        expected_sources = {name.removeprefix('inputs/payload/') for name in files if name.startswith('inputs/payload/')}
        for path in staged.rglob('*'):
            if path.is_file() and path.relative_to(staged).as_posix() not in expected_sources:
                path.unlink()
        for name, path in files.items():
            if name.startswith('inputs/payload/'):
                destination = staged / name.removeprefix('inputs/payload/')
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.is_file() or destination.read_bytes() != path.read_bytes():
                    destination.write_bytes(path.read_bytes())
        flags += ['-I' + str(staged / 'application')]
        flags += ['-I' + str(staged / 'runtime')]
        flags += ['-I' + str(staged / ('include%d' % i)) for i in range(len(getattr(args, 'include_dir', [])))]
        # Most payloads do not use the display identity. Avoid invalidating
        # runtime objects merely because an unrelated source changed the key.
        shared_identity = any(b'MINI_APP_BUILD_ID' in path.read_bytes()
                              for name, path in files.items() if name.startswith('inputs/payload/')
                              and path.suffix not in ('.c', '.S'))
        identity_sources = {staged / name for name in units
                            if shared_identity or b'MINI_APP_BUILD_ID' in (staged / name).read_bytes()}
        ninja_build(work, [staged / name for name in units], flags, env, 'payload',
                    linker=staged / 'runtime/link.ld', run=run, root=store.root,
                    identity_header=gen / 'identity.h', identity_sources=identity_sources)
        attrs = output([*env['bindings']['target.readelf'], '-h', '-A', str(work / 'payload.elf')])
        size = (work / 'payload.bin').stat().st_size
        if '0x%x' % ae350_map.DDR_CODE not in attrs or size > ae350_map.DDR_CODE_MAX:
            raise ValueError('Invalid AE350 payload entry or size')
        snapshot = {name: path.read_bytes() for name, path in files.items()}
        import hashlib
        if any(hashlib.sha256(data).hexdigest() != hashes[name] for name, data in snapshot.items()):
            raise ValueError('AE350 payload inputs changed during build')
        snapshot.update({'outputs/payload.' + suffix: work / ('payload.' + suffix) for suffix in ('elf', 'bin', 'map')})
        if diagnostic:
            symbols = output([*env['bindings']['target.readelf'], '-sW', str(work / 'payload.elf')])
            match = re.search(r'\b([0-9a-fA-F]+)\s+20\s+OBJECT\s+GLOBAL\s+DEFAULT\s+\d+\s+mini_app_parameters\b', symbols)
            if not match:
                raise ValueError('Diagnostic application did not export its parameter block')
            offset = int(match[1], 16) - ae350_map.DDR_CODE
            if not 0 <= offset <= size - 20:
                raise ValueError('Diagnostic parameters are outside the uploaded image')
            snapshot['outputs/parameters.json'] = canonical(dict(offset=offset, size=20, format='5I-le',
                fields=['stress_seconds', 'seed', 'region_kb', 'flags', 'traversal_mb']))
        snapshot['outputs/elf-attributes.txt'] = attrs.encode()
        snapshot['build.lock.json'] = canonical(config)
        found = store.publish('payload', key, snapshot, config=config, dependencies={'cpu': cpu})
        workspace.success(found)
    run.step('payload', 'BUILD', found); return found


def build(store, run, args, cpu, board, plan):
    profile = getattr(args, 'ae350_profile', None) or 'ddr'
    if args.target == 'payload':
        artifact = payload(store, run, cpu, args, profile, plan)
        run.finish(plan=plan, artifact=artifact, hardware_qualified=False,
                   qualification='compiled-not-board-tested')
        return artifact
    implemented = bool(getattr(args, 'synthesize', False) and args.target == 'gateware')
    boot = firmware(store, run, cpu, plan, profile)
    artifact = boot
    if args.target != 'bootloader' and boot:
        artifact = rtl(store, run, cpu, boot, board, args.target, plan, getattr(args, 'synthesize', False), profile,
                       getattr(args, 'pnr_mode', 'full'))
    elif args.target != 'bootloader':
        run.step(args.target, 'BUILD', reason='requires the platform bootloader output')
    if not plan and artifact:
        write(store.build / 'refs/machines/mega138k_ae350/candidates' / (run.id + '.json'),
              dict(machine='mega138k_ae350', artifact=artifact, run=run.id,
                   qualification='PnR-not-board-qualified' if implemented else 'host-generated-only'))
    remaining = ['physical-part-and-board-revisions', *([] if implemented else ['PnR']),
                 'JTAG-UART-board-validation', 'timer-IRQ-verification', 'CPU-frequency-characterization']
    remaining = ['physical-part-and-board-revisions', *([] if implemented else ['PnR']),
                 'JTAG-UART-board-validation', 'memory-board-verification',
                 'timer-IRQ-verification', 'CPU-frequency-characterization']
    run.finish(plan=plan, artifact=artifact, ready_for_execution=implemented and not plan, backend_available=True,
               qualification='PnR-not-board-qualified' if implemented else 'host-generated-only', hardware_qualified=False,
               remaining=remaining)
    return artifact
