"""Find existing native tools; keep machine paths out of tracked configuration."""
import argparse
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find(executable, candidates):
    on_path = shutil.which(executable)
    if on_path:
        return str(Path(on_path).resolve())
    return next((str(p.resolve()) for p in candidates if p.is_file()), None)


def executable(windows_name, unix_name):
    return windows_name if os.name == 'nt' else unix_name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gowin-bin', default='')
    parser.add_argument('--riscv-bin', default='')
    args = parser.parse_args()
    config_path = ROOT / '.tools.local.json'
    previous = json.loads(config_path.read_text()) if config_path.exists() else {}

    def discover(name, filename, candidates, override=''):
        if override:
            candidate = Path(override) / filename
            if not candidate.is_file():
                raise SystemExit(f'Missing executable: {candidate}')
            return str(candidate.resolve())
        if previous.get(name) and Path(previous[name]).is_file():
            return previous[name]
        return find(filename, candidates)

    if os.name == 'nt':
        gowin_candidates = sorted(Path('C:/Gowin').glob('*/IDE/bin/gw_sh.exe'), reverse=True)
        programmer_candidates = sorted(Path('C:/Gowin').glob('*/Programmer/bin/programmer_cli.exe'), reverse=True)
        make_candidates = [Path('C:/msys64/usr/bin/make.exe')]
        openfpgaloader_candidates = [Path(f'C:/msys64/{prefix}/bin/openFPGALoader.exe') for prefix in ('mingw64', 'ucrt64')]
        gcc_candidates = sorted(Path('C:/').glob('xpack-riscv-none-elf-gcc-*/bin/riscv-none-elf-gcc.exe'), reverse=True)
    else:
        gowin_home = Path(os.environ.get('GOWIN_HOME', Path.home() / 'Gowin_V1.9.12.04_linux'))
        gowin_candidates = [gowin_home / 'IDE/bin/gw_sh', Path('/opt/gowin/IDE/bin/gw_sh')]
        programmer_candidates = [gowin_home / 'Programmer/bin/programmer_cli', Path('/opt/gowin/Programmer/bin/programmer_cli')]
        make_candidates = []
        openfpgaloader_candidates = []
        gcc_candidates = []

    config = {
        'gcc': discover('gcc', executable('riscv-none-elf-gcc.exe', 'riscv-none-elf-gcc'), gcc_candidates, args.riscv_bin),
        'gowin': discover('gowin', executable('gw_sh.exe', 'gw_sh'), gowin_candidates, args.gowin_bin),
        'programmer': discover('programmer', executable('programmer_cli.exe', 'programmer_cli'), programmer_candidates),
        'make': discover('make', executable('make.exe', 'make'), make_candidates),
        'openfpgaloader': discover('openfpgaloader', executable('openFPGALoader.exe', 'openFPGALoader'), openfpgaloader_candidates),
    }
    config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    print(f'Local tool paths saved to {config_path.name}')


if __name__ == '__main__':
    main()
