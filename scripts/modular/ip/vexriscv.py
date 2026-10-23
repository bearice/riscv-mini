"""VexRiscv recipe adapter: generator arguments come only from machine IP input."""
from pathlib import Path
from types import SimpleNamespace


def generate(definition, destination, tools, environment):
    from scripts.builders.cpu_generator import build
    options = SimpleNamespace(output_dir=Path(destination),
        vexriscv_source=Path(environment['VEXRISCV_SOURCE']) if environment.get('VEXRISCV_SOURCE') else None,
        java=Path(tools['java'][0]), sbt_launch=Path(tools['sbt-launch'][0]),
        selected_parameters=definition['parameters'],
        module_name=definition['module'])
    for key in ('relaxed_pc_calculation','compressed','pipelined_fetch'):
        setattr(options, key, definition['parameters'][key])
    build(options)
    return Path(destination) / (definition['module'] + '.v')
