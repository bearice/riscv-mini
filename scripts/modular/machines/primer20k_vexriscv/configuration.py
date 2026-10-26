"""Resolve one hardware configuration for every build caller."""
from pathlib import Path
from gateware.machines.primer20k_vexriscv.features import Features
from gateware.machines.primer20k_vexriscv.config import storage_profile, cpu_configuration, cpu_capabilities, cpu_isa, pll_count, vendored_cpu_path
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[4]), str(Path(__file__).resolve().parents[4] / 'scripts')]
from scripts.modular.storage import canonical, digest, source_bytes
from scripts.modular.machines.primer20k_vexriscv.cpu import locked_cpu
from scripts.modular.machines.primer20k_vexriscv.contracts import hardware


def configuration_arguments(v, app, cpu, purpose, synthesize=False):
    """Reproduction captures source JSON, never a second list of RTL overrides."""
    from gateware.description import load_machine
    snapshot = v.get('machine_snapshot')
    if snapshot is None:
        raise ValueError('Legacy configuration has no source JSON snapshot; reproduce using its preserved checkout')
    board, machine = load_machine('primer20k_vexriscv')
    if snapshot['board'] != board or snapshot['machine'] != machine:
        raise ValueError('Selected hardware differs from source JSON; restore its recorded JSON before rebuilding')
    args = ['build', 'system', '--machine', machine['id'], '--source', str(Path(app).resolve()), '--purpose', purpose]
    if synthesize: args.append('--synthesize')
    return args


def resolve(store, run, args, base=None, plan=False):
    """Resolve the selected JSON snapshot into the actual RTL build parameters."""
    from gateware.description import resolve_machine, from_snapshot
    saved = getattr(args, 'resolved_machine_snapshot', None)
    machine = from_snapshot(saved) if saved is not None else resolve_machine('primer20k_vexriscv', store.root)
    settings = machine.rtl
    features = Features(**settings.features)
    sd = settings.sd_profile
    variant = settings.cpu_variant
    if not settings.cpu_verilog:
        raise ValueError('CPU IP has not been resolved from machine.json')
    cpu_path = (store.root / settings.cpu_verilog).resolve()
    if cpu_path:
        lock = locked_cpu(cpu_path)
        lock['ip_identity'] = machine.artifacts['cpu']['identity']
        capabilities = cpu_capabilities(cpu_path)
        checked, expected, _ = cpu_configuration(features, features.as_dict(), cpu_path, variant)
        if expected != variant or checked != features:
            raise ValueError('machine.json CPU variant/features disagree with selected RTL')
        files = {'outputs/' + cpu_path.name: source_bytes(cpu_path),
                 'inputs/generator.json': source_bytes(cpu_path.parent / 'generator.json'),
                 'cpu.lock.json': canonical(lock)}
        if cpu_path.with_suffix('.yaml').is_file():
            files['outputs/' + cpu_path.with_suffix('.yaml').name] = source_bytes(cpu_path.with_suffix('.yaml'))
        cpu_ref = store.lookup('cpu', digest(lock))
        if not plan and not cpu_ref:
            cpu_ref = store.publish('cpu', digest(lock), files, config=lock,
                dependencies={'ip': dict(machine.artifacts['cpu']['artifact'])})
    else:
        cpu_configuration(features, features.as_dict(), None, variant)
        capabilities = dict(mmu=False, fpu=False, dcache=False, compressed=False, bitmanip=[])
        from scripts.modular.environment import python_packages
        lock = dict(builtin_variant=variant, capabilities=capabilities, packages=python_packages())
        cpu_ref = store.lookup('cpu', digest(lock))
        if not plan and not cpu_ref:
            cpu_ref = store.publish('cpu', digest(lock), {'cpu.lock.json': canonical(lock)}, config=lock)
    mode = machine.boot.mode
    hierarchy = settings.rtl_hierarchy
    profile = 'full' if features == Features.resolve('full') else 'minimal'
    v = dict(profile=profile, features=features.as_dict(), cpu_variant=variant,
        cpu_capabilities=capabilities, isa=cpu_isa(features, capabilities), abi=machine.cpu.abi,
        boot_mode=mode, rom_size_bytes=machine.boot.rom_size_bytes,
        flash_size_bytes=machine.board.resources[machine.devices.flash].size_bytes,
        sram_size_bytes=0, l2_size_bytes=settings.l2_size_bytes,
        boot_ram_address=0x007ff000, boot_ram_size_bytes=4096, boot_ram_backend='pinned-l2',
        memory_system='shared-writeback', l2_policy='write-back', memory_burst_bytes=16,
        memory_port_widths=dict(wishbone=32, video=16, sd=32, **({'eth':32} if features.eth_dma else {})),
        sd_profile=sd, sd_backend='native' if sd in ('lite', 'full') else 'spi' if sd=='spi' else 'none',
        usb_backend=settings.usb_backend, audio_clock=settings.audio_clock,
        clock_hz=machine.clocks.sys, ddr_clock_hz=machine.clocks.sys * 2,
        verilog_mode='flat' if hierarchy=='flat' else 'hierarchical', rtl_hierarchy=hierarchy,
        netlist_hierarchy=None if hierarchy=='flat' else 'flatten',
        place_option=settings.place_option, route_option=settings.route_option)
    v['rom_size_bytes'] = v['rom_size_bytes'] if mode=='rom' else 0
    v['expected_plls'] = pll_count(features, v['usb_backend'], v['audio_clock'])
    v['dma_backend'] = 'native' if features.sd and sd=='lite' else 'wishbone'
    v['native_dma_clients'] = (['sd'] if features.sd and sd=='lite' else []) + (['eth'] if features.eth_dma else [])
    v['ethernet_dma_backend'] = 'native-descriptor-ring' if features.eth_dma else 'pio'
    v['machine_snapshot'] = __import__('json').loads(machine.snapshot)
    run.result['configuration'] = v; run.save()
    return v, cpu_ref
