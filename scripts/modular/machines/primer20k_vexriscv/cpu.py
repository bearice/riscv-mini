"""Validate preserved CPU inputs rather than guessing settings from file names."""
import re
import shlex
from pathlib import Path
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[4]), str(Path(__file__).resolve().parents[4] / 'scripts')]
from scripts.modular.storage import file_hash, read

PIPELINE = ('relaxed_pc_calculation', 'extra_fetch_stage', 'two_cycle_icache', 'injector_register')
VARIANT = ('mmu', 'fpu', 'dcache', 'compressed', 'bitmanip', 'single_cycle_shift', 'single_cycle_muldiv')


def locked_cpu(path):
    path = Path(path)
    metadata = path.parent / 'generator.json'
    if not metadata.is_file():
        raise ValueError('CPU requires generator.json paired with RTL: ' + str(path))
    data = read(metadata)
    pipeline = data.get('pipeline', {})
    variant = data.get('cpu_rtls', {}).get(path.stem, {})
    missing = [k for k in PIPELINE if k not in pipeline] + [k for k in VARIANT if k not in variant]
    if missing:
        raise ValueError('Incomplete CPU configuration: ' + ', '.join(missing))
    if variant.get('sha256') != file_hash(path):
        raise ValueError('CPU RTL differs from generator metadata')
    rtl = path.read_text(encoding='utf-8')
    injector = bool(re.search(r'\breg\s+(?:\[[^\]]+\]\s*)?_zz_IBusCachedPlugin_injector_', rtl))
    if pipeline['injector_register'] != injector:
        raise ValueError('CPU injector metadata disagrees with actual RTL')
    commands = [c for c in data.get('commands', []) if '--outputFile ' + path.stem in c]
    if len(commands) != 1:
        raise ValueError('CPU requires one exact generation command')
    tokens=shlex.split(commands[0]);options=dict(zip(tokens[2::2],tokens[3::2]))
    for field,flag in [('single_cycle_shift','--singleCycleShift'),('single_cycle_muldiv','--singleCycleMulDiv'),
                       ('compressed','--compressedGen'),('fpu','--fpu')]:
        if options.get(flag)!=str(variant[field]).lower():raise ValueError('CPU command disagrees with '+field)
    if (options.get('--csrPluginConfig')=='linux')!=variant['mmu']:
        raise ValueError('CPU command disagrees with MMU setting')
    if options.get('--iCacheSize')!='2048' or options.get('--dCacheSize')!='2048':
        raise ValueError('Unsupported/unrecorded CPU cache configuration')
    return dict(schema=1, rtl_sha256=file_hash(path), pipeline={k: pipeline[k] for k in PIPELINE},
                variant={k: variant[k] for k in VARIANT}, command=commands[0],
                upstream_generator_sha256=data.get('upstream_generator_sha256'),
                high_mmio_xip=data.get('high_mmio_xip', False),
                provenance=data.get('source_lock', {'status': 'legacy input; regeneration tool/source closure not fully recorded'}))
