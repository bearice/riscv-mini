"""Find existing native tools; keep machine paths out of tracked configuration."""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find(executable, candidates):
    on_path = shutil.which(executable)
    if on_path:
        return str(Path(on_path).resolve())
    return next((str(p.resolve()) for p in candidates if p.is_file()), None)


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

    config = {
        'gcc': discover('gcc', 'riscv-none-elf-gcc.exe', sorted(Path('C:/').glob('xpack-riscv-none-elf-gcc-*/bin/riscv-none-elf-gcc.exe'), reverse=True), args.riscv_bin),
        'gowin': discover('gowin', 'gw_sh.exe', sorted(Path('C:/Gowin').glob('*/IDE/bin/gw_sh.exe'), reverse=True), args.gowin_bin),
        'programmer': discover('programmer', 'programmer_cli.exe', sorted(Path('C:/Gowin').glob('*/Programmer/bin/programmer_cli.exe'), reverse=True)),
        'make': discover('make', 'make.exe', [Path('C:/msys64/usr/bin/make.exe')]),
        'openfpgaloader': discover('openfpgaloader', 'openFPGALoader.exe', [Path(f'C:/msys64/{prefix}/bin/openFPGALoader.exe') for prefix in ('mingw64', 'ucrt64')]),
    }
    config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    print(f'Local tool paths saved to {config_path.name}')


if __name__ == '__main__':
    main()
