"""Directional interface compatibility, separate from artifact identity and v1 tags."""
import re
from gateware.machines.primer20k_vexriscv.config import HARDWARE_ABI_MAJOR, INTERFACE_REVISIONS
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[4]), str(Path(__file__).resolve().parents[4] / 'scripts')]
from scripts.modular.storage import digest

CAPABILITIES = {'mmu': 0, 'fpu': 1, 'flash': 2, 'spi_lcd': 3, 'sd': 4,
                'filesystem': 5, 'video': 6, 'board_io': 7, 'ws2812': 8,
                'audio': 9, 'mic': 10, 'mic_stereo': 11, 'eth': 12, 'eth_dma': 13, 'usb': 14}


def capability_flags(names):
    unknown = set(names) - set(CAPABILITIES)
    if unknown: raise ValueError('Unknown capabilities: ' + ', '.join(sorted(unknown)))
    return sum(1 << CAPABILITIES[name] for name in set(names))


def hardware(csr, config):
    """Every consumed register is pinned; additional provider registers are allowed.

    Behavior revision is explicit, never inferred from matching register hashes.
    This contract does not modify the installed v1 loader's hash algorithm.
    """
    return dict(schema=1, platform='riscv-mini', abi_major=HARDWARE_ABI_MAJOR,
                behavior=dict(memory_system=config['memory_system'],
                              l2_policy=config['l2_policy'], boot_mode=config['boot_mode'],
                              boot_ram_address=config['boot_ram_address'], boot_ram_size_bytes=config['boot_ram_size_bytes'],
                              **{k: config[k] for k in ('sd_backend', 'sd_profile', 'usb_backend', 'audio_clock',
                                  'memory_burst_bytes', 'dma_backend', 'native_dma_clients', 'ethernet_dma_backend') if k in config}),
                # Revision owner: gateware implementing the interface. Bump on
                # semantic changes even when register addresses remain equal.
                interface_revisions=dict(INTERFACE_REVISIONS),
                registers=csr['csr_registers'], memories=csr['memories'],
                interrupts={k: v for k, v in csr['constants'].items() if k.lower().endswith('_interrupt')},
                clock_hz=config['clock_hz'], isa=config['isa'], abi=config['abi'],
                capabilities=sorted(k for k, v in config['features'].items() if v))


def requirements(provider, required=None, optional=None, *, registers=None, memories=None, interrupts=None):
    required = provider['capabilities'] if required is None else required
    optional = optional or []
    unknown = (set(required) | set(optional)) - set(CAPABILITIES)
    if unknown:
        raise ValueError('Unknown capabilities: ' + ', '.join(sorted(unknown)))
    result = {**{k: provider[k] for k in ('platform', 'abi_major', 'behavior', 'registers', 'memories', 'interrupts', 'clock_hz', 'isa', 'abi')},
              'interface_revisions': provider.get('interface_revisions', {}),
              'scope': 'configured-sdk-snapshot',
              'required': sorted(required), 'optional': sorted(optional), 'mandatory_flags': 0,
              'required_capability_flags': capability_flags(required),
              'optional_capability_flags': capability_flags(optional)}
    # A consumer may explicitly narrow its interface. Never infer this from its
    # release version or silently discard a register after a provider change.
    for section, names in (('registers', registers), ('memories', memories), ('interrupts', interrupts)):
        if names is not None:
            result[section] = {name: provider[section][name] for name in names}
            result['scope'] = 'declared-subset'
    return result


def isa_extensions(isa):
    """Subset comparison for the unversioned ISA syntax emitted by this repo."""
    match = re.fullmatch(r'rv(32|64)([iemafdqclbjtpvng]+)((?:_z[a-z0-9]+)*)', isa)
    if not match:
        raise ValueError('Unsupported ISA contract: ' + str(isa))
    extensions = set(match[2])
    if 'g' in extensions:
        extensions.remove('g'); extensions.update('imafd')
        extensions.update(('zicsr', 'zifencei'))
    extensions.update(filter(None, match[3].split('_')))
    return match[1], extensions


def check(provider, consumer):
    problems = []
    if consumer.get('mandatory_flags', 0):
        problems.append('unknown mandatory format flags')
    known = capability_flags(CAPABILITIES)
    required_flags = consumer.get('required_capability_flags', 0)
    if required_flags < 0 or required_flags & ~known:
        problems.append('unknown required capability flags')
    if required_flags & ~capability_flags(provider['capabilities']):
        problems.append('required capability flags unavailable')
    for name in ('platform', 'abi_major', 'clock_hz', 'abi'):
        if provider.get(name) != consumer.get(name):
            problems.append(name + ' differs')
    for name, value in consumer['behavior'].items():
        if provider['behavior'].get(name) != value:
            problems.append('behavior.' + name + ' differs')
    try:
        px, pe = isa_extensions(provider['isa']); cx, ce = isa_extensions(consumer['isa'])
        if px != cx or not ce <= pe: problems.append('ISA requirements unavailable')
    except ValueError as error:
        problems.append(str(error))
    for name, revision in consumer.get('interface_revisions', {}).items():
        if provider.get('interface_revisions', {}).get(name) != revision:
            problems.append('interface revision differs: ' + name)
    for section in ('registers', 'memories', 'interrupts'):
        for name, value in consumer[section].items():
            if provider[section].get(name) != value:
                problems.append(section + '.' + name + ' missing or changed')
    missing = set(consumer['required']) - set(provider['capabilities'])
    unknown = set(consumer['required']) - set(CAPABILITIES)
    if unknown: problems.append('unknown capabilities: ' + ', '.join(sorted(unknown)))
    if missing:
        problems.append('missing capabilities: ' + ', '.join(sorted(missing)))
    return dict(compatible=not problems, problems=problems,
                unavailable_optional=sorted(set(consumer['optional']) - set(provider['capabilities'])),
                unavailable_optional_flags=consumer.get('optional_capability_flags', 0) & ~capability_flags(provider['capabilities']),
                provider_digest=digest(provider), requirement_digest=digest(consumer))


def assembly(store, parts):
    """Validate every selected dependency edge before publishing a system.

    Gateware embeds a specific SoC and ROM. Static linkage consumes a specific
    SDK archive. Software may target a compatible replacement SoC, but the v1
    loader still requires an exact image tag; interface compatibility cannot
    override that on-wire restriction.
    """
    manifests = {name: store.get(ref, recursive=True) for name, ref in parts.items()}
    for manifest in manifests.values():
        if manifest['config'].get('kind') == 'hard' and manifest['config'].get('qualification') == 'description-only':
            raise ValueError('P0 hard CPU description cannot be assembled into a runnable system')
    reports = []
    for name, manifest in manifests.items():
        if manifest['component'] != name:
            raise ValueError(f'{name}: wrong component type {manifest["component"]}')
        for dependency, expected in manifest['dependencies'].items():
            if dependency not in parts:
                raise ValueError(f'{name} requires missing component {dependency}')
            actual = parts[dependency]
            if actual != expected:
                # Only executable/archive -> hardware is directional. All
                # embedded, generated and static-link edges remain exact.
                directional = dependency == 'soc-description' and name in ('bios', 'hal-sdk', 'bootloader')
                req = manifest['config'].get('requires')
                if not directional or not req:
                    raise ValueError(f'{name} -> {dependency}: selected artifact differs from dependency lock')
            if dependency == 'soc-description' and name in ('bios', 'hal-sdk', 'bootloader'):
                req = manifest['config'].get('requires')
                if req:
                    result = check(manifests[dependency]['config']['contract'], req)
                    reports.append(dict(consumer=name, provider=dependency, **result))
                    if not result['compatible']:
                        raise ValueError(f'{name} -> {dependency}: ' + '; '.join(result['problems']))
    if 'bios' in parts and 'bootloader' in parts:
        from scripts.tools.boot_image import unpack_image
        boot = manifests['bootloader']
        boot_soc = store.get(boot['dependencies']['soc-description'])
        expected_tag = boot['config'].get('image_abi_tag', boot_soc['config']['legacy_abi_tag'])
        image = store.path(parts['bios']) / 'outputs/app.img'
        unpack_image(image.read_bytes(), expected_tag)
        reports.append(dict(consumer='bootloader', provider='bios', format='mini-image-v1',
                            image_abi_tag=expected_tag, compatible=True))
    return reports
