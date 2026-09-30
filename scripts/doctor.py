"""Read-only environment check. Does not connect to or program the FPGA."""
import importlib
import importlib.metadata as metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    tools = json.loads((ROOT / '.tools.local.json').read_text())
    report = {'python': platform.python_version(), 'executable': sys.executable, 'dependencies': {}, 'tools': tools}
    failed = []
    for package, module in [
        ('migen', 'migen'), ('litex', 'litex'), ('litex-boards', 'litex_boards'),
        ('litedram', 'litedram'), ('litesdcard', 'litesdcard'), ('litespi', 'litespi'),
        ('pythondata-cpu-vexriscv', 'pythondata_cpu_vexriscv'),
        ('pythondata-software-picolibc', 'pythondata_software_picolibc'),
        ('pythondata-software-compiler-rt', 'pythondata_software_compiler_rt'),
    ]:
        try:
            importlib.import_module(module)
            dist = metadata.distribution(package)
            direct = json.loads(dist.read_text('direct_url.json') or '{}')
            report['dependencies'][package] = {'version': dist.version, 'source': direct}
            print(f'OK import {module}')
        except Exception as error:
            failed.append(f'{package}: {error}')
    for name in ('gcc', 'gowin'):
        if not tools.get(name) or not Path(tools[name]).is_file():
            failed.append(f'Required tool missing: {name}')
    if tools.get('gcc'):
        try:
            report['gcc_version'] = subprocess.check_output([tools['gcc'], '--version'], text=True).splitlines()[0]
            print(report['gcc_version'])
            library = subprocess.check_output([tools['gcc'], '-march=rv32im', '-mabi=ilp32', '-print-libgcc-file-name'], text=True).strip()
            if not Path(library).is_file():
                failed.append(f'RV32IM libgcc missing: {library}')
            report['rv32im_libgcc'] = library
        except (OSError, subprocess.CalledProcessError) as error:
            failed.append(str(error))
    if not tools.get('programmer') and not tools.get('openfpgaloader'):
        print('NOTE no programmer found; generation and compilation remain available.')
    if not tools.get('openfpgaloader'):
        print('NOTE openFPGALoader is absent; Gowin Programmer is the available programming route.')
    report['errors'] = failed
    report['gowin_license'] = 'not checked; --synthesize validates tool/license by an actual build'
    output = ROOT / 'build' / 'environment.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for error in failed:
        print(f'ERROR {error}')
    print(f'Environment report: {output}')
    return bool(failed)


if __name__ == '__main__':
    sys.exit(main())
