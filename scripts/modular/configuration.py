"""Resolve one hardware configuration for every build caller."""
from pathlib import Path
from gateware.features import Features
from gateware.config import storage_profile, cpu_configuration, cpu_capabilities, cpu_isa, pll_count
from .storage import canonical, digest
from .cpu import locked_cpu
from .contracts import hardware


HARDWARE_OPTIONS = ('profile', 'cpu_verilog', 'cpu_variant', 'sd_profile', 'usb_backend',
                    'audio_clock', 'boot_mode', 'rom_size', 'l2_size', 'place_option',
                    'route_option', 'rtl_hierarchy')


def add_arguments(parser):
    parser.add_argument('--profile', choices=('minimal', 'full'), help='Start from a complete profile instead of selected hardware')
    parser.add_argument('--cpu-verilog', type=Path, help='Explicit CPU RTL with paired generation metadata')
    parser.add_argument('--cpu-variant', choices=('lite', 'full', 'linux'))
    parser.add_argument('--feature', action='append', default=[], metavar='NAME=on|off')
    parser.add_argument('--sd-profile', choices=('none', 'spi', 'lite', 'full'))
    parser.add_argument('--usb-backend', choices=('ohci', 'ultra'))
    parser.add_argument('--audio-clock', choices=('dds', 'legacy'))
    parser.add_argument('--boot-mode', choices=('rom', 'xip'))
    parser.add_argument('--rom-size', type=int, choices=(4096, 8192))
    parser.add_argument('--l2-size', type=int, choices=(4096, 8192, 16384))
    parser.add_argument('--place-option', type=int, choices=range(5))
    parser.add_argument('--route-option', type=int, choices=range(3))
    parser.add_argument('--rtl-hierarchy', choices=('flat', 'blocks', 'deep'))


def changed(args):
    return bool(getattr(args,'feature',[]) or any(getattr(args, key,None) is not None for key in HARDWARE_OPTIONS))


def configuration_arguments(v, app, cpu, purpose, synthesize=False):
    """The sole argv serializer, used by releases and matrix builds too."""
    args = ['build', 'system', '--profile', v['profile'], '--source', str(Path(app).resolve()), '--purpose',purpose,
            '--cpu-variant', v['cpu_variant'], '--boot-mode', v['boot_mode'],
            '--rom-size', str(v['rom_size_bytes'] or 8192), '--l2-size', str(v['l2_size_bytes']),
            '--sd-profile', v['sd_profile'], '--usb-backend', v['usb_backend'],
            '--audio-clock', v['audio_clock'], '--place-option', str(v['place_option']),
            '--route-option', str(v['route_option']), '--rtl-hierarchy', v.get('rtl_hierarchy', 'blocks')]
    if cpu: args += ['--cpu-verilog', str(Path(cpu).resolve())]
    for name, enabled in sorted(v['features'].items()):
        args += ['--feature', name + '=' + ('on' if enabled else 'off')]
    if synthesize: args.append('--synthesize')
    return args


def resolve(store, run, args, base=None, plan=False):
    """Return resolved options and exact CPU; no mutable defaults after this point."""
    overrides = {}
    for flag in args.feature:
        name, separator, value = flag.partition('='); name = name.replace('-', '_')
        if not separator or name not in Features.names() or value not in ('on', 'off'):
            raise ValueError('Expected --feature NAME=on|off: ' + flag)
        if name in overrides: raise ValueError('Duplicate feature override: ' + name)
        overrides[name] = value == 'on'
    previous = store.get(base['soc-description'])['config']['validation'] if base and not args.profile else {}
    profile = args.profile or previous.get('profile', 'minimal')
    features = Features(**previous['features']) if previous else Features.resolve(profile, {})
    features = Features.resolve(profile, {**features.as_dict(), **overrides})
    default_sd=previous.get('sd_profile') or ('lite' if features.sd else 'none')
    if default_sd=='none' and features.sd:default_sd='lite'
    sd, features = storage_profile(args.sd_profile or default_sd, None, features, overrides)
    cpu_path = args.cpu_verilog
    cpu_ref = base['cpu'] if previous else None
    if cpu_path:
        cpu_path = cpu_path.resolve(); lock = locked_cpu(cpu_path)
        features, variant, _ = cpu_configuration(features, overrides, cpu_path, args.cpu_variant)
        capabilities = cpu_capabilities(cpu_path)
        files = {'outputs/' + cpu_path.name: cpu_path, 'inputs/generator.json': cpu_path.parent / 'generator.json',
                 'cpu.lock.json': canonical(lock)}
        if cpu_path.with_suffix('.yaml').is_file(): files['outputs/' + cpu_path.with_suffix('.yaml').name] = cpu_path.with_suffix('.yaml')
        cpu_ref=store.lookup('cpu',digest(lock))
        if not plan and not cpu_ref: cpu_ref = store.publish('cpu', digest(lock), files, config=lock)
    elif previous:
        capabilities = previous['cpu_capabilities']; variant = args.cpu_variant or previous['cpu_variant']
        if any(getattr(features, name) != bool(capabilities[name]) for name in ('mmu', 'fpu')):
            raise ValueError('Changing CPU capabilities requires --cpu-verilog with complete metadata')
        if variant != previous['cpu_variant']: raise ValueError('Changing CPU adapter requires --cpu-verilog')
    else:
        features, variant, _ = cpu_configuration(features, overrides, None, args.cpu_variant)
        capabilities = dict(mmu=False, fpu=False, dcache=False, compressed=False, bitmanip=[])
        from .environment import python_packages
        lock = dict(builtin_variant=variant, capabilities=capabilities, packages=python_packages())
        cpu_ref=store.lookup('cpu',digest(lock))
        if not plan and not cpu_ref: cpu_ref = store.publish('cpu', digest(lock), {'cpu.lock.json': canonical(lock)}, config=lock)
    def value(name, field, default):
        selected = getattr(args, name)
        return selected if selected is not None else previous.get(field, default)
    mode = value('boot_mode', 'boot_mode', 'rom')
    if mode == 'xip' and not features.flash: raise ValueError('XIP requires Flash')
    hierarchy = value('rtl_hierarchy', 'rtl_hierarchy', 'blocks')
    v = dict(previous, profile=profile, features=features.as_dict(), cpu_variant=variant,
        cpu_capabilities=capabilities, isa=cpu_isa(features, capabilities), abi='ilp32',
        boot_mode=mode, rom_size_bytes=value('rom_size', 'rom_size_bytes', 8192) or 8192,
        sram_size_bytes=0, l2_size_bytes=value('l2_size', 'l2_size_bytes', 4096),
        boot_ram_address=0x007ff000, boot_ram_size_bytes=4096, boot_ram_backend='pinned-l2',
        memory_system='shared-writeback', l2_policy='write-back', memory_burst_bytes=16,
        memory_port_widths=dict(wishbone=32, video=16, sd=32, **({'eth':32} if features.eth_dma else {})),
        sd_profile=sd, sd_backend='native' if sd in ('lite', 'full') else 'spi' if sd=='spi' else 'none',
        usb_backend=value('usb_backend', 'usb_backend', 'ultra'),
        audio_clock=value('audio_clock', 'audio_clock', 'dds'), clock_hz=60000000, ddr_clock_hz=120000000,
        verilog_mode='flat' if hierarchy=='flat' else 'hierarchical', rtl_hierarchy=hierarchy,
        netlist_hierarchy=None if hierarchy=='flat' else 0,
        place_option=value('place_option', 'place_option', 2), route_option=value('route_option', 'route_option', 2))
    v['rom_size_bytes'] = v['rom_size_bytes'] if mode=='rom' else 0
    v['expected_plls'] = pll_count(features, v['usb_backend'], v['audio_clock'])
    v['dma_backend'] = 'native' if features.sd and sd=='lite' else 'wishbone'
    v['native_dma_clients'] = (['sd'] if features.sd and sd=='lite' else []) + (['eth'] if features.eth_dma else [])
    v['ethernet_dma_backend'] = 'native-descriptor-ring' if features.eth_dma else 'pio'
    run.result['configuration'] = v; run.save()
    return v, cpu_ref
