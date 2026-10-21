"""Build the GW-BASIC interpreter payload and report what it occupies."""
import json, subprocess, sys
from pathlib import Path
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / 'scripts')]
from scripts.builders import payload as bios_payload
from scripts.tools.riscv_toolchain import executable as tool_executable
ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'firmware/apps/gwbasic'
HELPERS = ['string']


def build(a, run=None):
    from types import SimpleNamespace
    bios_payload.build(SimpleNamespace(source=None, source_dir=APP, output_dir=a.output_dir,
        include_dir=[], define=[], cflag=[], extra_source=[], helper=HELPERS,
        soft_float=a.soft_float, optimize='-Os', libc='none'), run=run)
    out = a.output_dir
    from scripts.modular.environment import discover
    gcc = Path(discover(['target.cc'])['bindings']['target.cc'][0])
    nm = str(gcc.parent / tool_executable('riscv-none-elf-size'))
    print(subprocess.check_output([nm, str(out / 'payload.elf')], text=True).strip())
