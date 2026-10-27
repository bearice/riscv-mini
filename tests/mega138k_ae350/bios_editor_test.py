"""Run the existing BIOS editor scenarios against the AE350 editor source."""
from pathlib import Path
import importlib.util
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('editor_scenarios',ROOT/'tests/primer20k_vexriscv/bios_line_editor_test.py')
scenarios=importlib.util.module_from_spec(spec);spec.loader.exec_module(scenarios)
source=(ROOT/'firmware/bsp/mega138k_ae350/bios/editor.c').read_text()
body=source.split('void bios_editor(void) {',1)[1].rsplit('}',1)[0]
body=body.replace('bios_command(line)','command(line)')
with tempfile.TemporaryDirectory(prefix='ae350-editor-') as tmp:
    path=Path(tmp);unit=path/'test.c';unit.write_text(scenarios.PREFIX+body+scenarios.SUFFIX)
    subprocess.run([shutil.which('cc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function',str(unit),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True,timeout=10)
