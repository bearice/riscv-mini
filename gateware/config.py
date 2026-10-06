"""CPU capabilities and storage profiles used by all build entry points."""
from dataclasses import replace
from pathlib import Path
import hashlib
import json

SD_PROFILES = ('none', 'spi', 'lite', 'full')


def storage_profile(profile, backend, features, overrides=None):
    if profile is not None and backend is not None:
        raise ValueError('Use --sd-profile or legacy --sd-backend, not both')
    explicit_profile = profile is not None or backend is not None
    profile = profile or ('spi' if backend == 'spi' else 'lite')
    if profile not in SD_PROFILES:
        raise ValueError('Unknown SD profile')
    if profile == 'none':
        if any((overrides or {}).get(name) is True for name in ('sd','filesystem')):
            raise ValueError('SD none conflicts with explicitly enabled SD/filesystem')
        features = replace(features, sd=False, filesystem=False)
    elif explicit_profile and (overrides or {}).get('sd') is not False:
        features = replace(features, sd=True)
    elif not features.sd:
        if profile != 'lite' and (overrides or {}).get('sd') is False:
            raise ValueError('Explicit SD profile conflicts with --without-sd')
        profile = 'none'
    return profile, features


def cpu_capabilities(path):
    rtl = Path(path).read_text(encoding='utf-8')
    capabilities = {'mmu':'MmuPlugin' in rtl, 'fpu':'FpuPlugin' in rtl,
                    'dcache':'module DataCache' in rtl, 'compressed':False, 'bitmanip':[]}
    metadata = Path(path).parent/'generator.json'
    if metadata.is_file():
        record = json.loads(metadata.read_text()).get('cpu_rtls', {}).get(Path(path).stem)
        if record:
            if record['sha256'] != hashlib.sha256(Path(path).read_bytes()).hexdigest():
                raise ValueError('CPU RTL no longer matches generator metadata')
            capabilities.update(compressed=record.get('compressed', False), bitmanip=record.get('bitmanip', []))
    return capabilities


def cpu_isa(features, capabilities):
    isa = 'rv32im'+('a' if features.mmu or features.fpu else '')+('f' if features.fpu else '')
    if capabilities.get('compressed'):isa += 'c'
    extensions = [name.lower() for name in capabilities.get('bitmanip', [])]
    if any(name not in ('zba','zbb','zbs') for name in extensions):raise ValueError('Unsupported B subset')
    return isa+'_'+ '_'.join([*extensions, 'zicsr', 'zifencei'])


def cpu_configuration(features, overrides, path=None, variant=None):
    if path is None:
        if features.mmu or features.fpu:
            raise ValueError('Enabled MMU/FPU requires matching CPU RTL; use --cpu-rtl-dir or --cpu-verilog')
        if variant not in (None,'lite'):
            raise ValueError('Non-lite CPU requires explicit CPU RTL and capability metadata')
        return features, 'lite', False
    capabilities = cpu_capabilities(path)
    for name in ('mmu','fpu'):
        explicit = overrides.get(name)
        if explicit is not None and explicit != capabilities[name]:
            raise ValueError(f'--{name} configuration disagrees with CPU RTL {path}')
    features = replace(features, mmu=capabilities['mmu'], fpu=capabilities['fpu'])
    return features, ('linux' if features.mmu else 'full'), capabilities['dcache']


def cpu_filename(mmu, fpu):
    return 'VexRiscv_' + ('MmuFpu' if mmu and fpu else 'Mmu' if mmu else 'Fpu' if fpu else 'Base') + '.v'


def pll_count(features, usb_backend, audio_clock):
    count = 1 + int(features.video) + int(features.usb)
    count += int(features.usb and usb_backend=='ohci')
    return count
