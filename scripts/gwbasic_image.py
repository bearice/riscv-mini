"""Build the GW-BASIC interpreter payload and report what it occupies."""
import argparse, json, subprocess, sys
from pathlib import Path
import bios_payload
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'firmware/apps/gwbasic'
HELPERS = ['string']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, default=ROOT / 'build/gwbasic')
    p.add_argument('--soft-float', action='store_true',
                   help='build for rv32im/ilp32 instead of the default rv32imaf/ilp32f')
    a = p.parse_args()
    argv = ['bios_payload.py', '--source-dir', str(APP), '--output-dir', str(a.output_dir)]
    for name in HELPERS:
        argv += ['--helper', name]
    if a.soft_float:
        argv.append('--soft-float')
    sys.argv = argv
    bios_payload.main()
    out = a.output_dir
    gcc = Path(json.loads((ROOT / '.tools.local.json').read_text())['gcc'])
    nm = str(gcc.parent / 'riscv-none-elf-size.exe')
    print(subprocess.check_output([nm, str(out / 'payload.elf')], text=True).strip())


if __name__ == '__main__':
    main()
