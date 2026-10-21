"""Private workers dispatched by mini; no standalone CLI."""
import os
import sys
import subprocess
from pathlib import Path
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.modular.storage import ROOT

def perform(argv):
    if argv[:1] == ['action']:
        from scripts.modular.storage import read
        from scripts.modular.environment import process_environment
        action = read(argv[1])
        for name in action.get('remove_before', []):
            Path(name).unlink(missing_ok=True)
        return subprocess.run(action['argv'], cwd=action['cwd'], env=process_environment(action['environment'])).returncode
    if argv[:1] == ['rebuild-one']:
        from scripts.modular.storage import Store, Run, read
        from scripts.modular.machines.primer20k_vexriscv.system import import_export
        from scripts.modular.machines.primer20k_vexriscv.firmware import build_component
        store=Store(ROOT);ref=import_export(store,Path(argv[1]));name=argv[2]
        parts=read(store.path(ref)/'bundle.lock.json')['components']
        run=Run('test','source-rebuild',root=ROOT)
        expected=store.get(parts[name])['config']['build']
        actual=build_component(store,run,name,parts['soc-description'],
                               boot=parts['bootloader'] if name=='bios' else None,
                               sdk=parts['hal-sdk'] if name=='bios' else None,expected=expected,force=True)
        if actual!=parts[name]:raise ValueError('Source replay output differs from lock')
        run.finish(system=ref,artifact=actual);return 0
    if argv[:1] == ["rtl"]:
        from scripts.modular.machines.primer20k_vexriscv.rtl import worker
        return worker(Path(argv[1]))
    raise ValueError("Unknown internal worker")
