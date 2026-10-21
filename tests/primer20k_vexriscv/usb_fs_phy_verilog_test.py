"""Exercise USB receive jitter and sub-clock phase offsets at 48/60 MHz."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog', required=True)
    args = parser.parse_args()
    compiler = Path(args.iverilog).resolve()
    output = ROOT / 'build/reports/checks/usb-fs-phy'
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PATH': str(compiler.parent) + os.pathsep + os.environ['PATH'],
           'TMP': str(output), 'TEMP': str(output), 'TMPDIR': str(output)}
    for frequency in (48000000, 60000000):
        for jitter in (0, 8):
            name = f'{frequency}-{jitter}'
            executable = output / f'{name}.vvp'
            subprocess.run([str(compiler), '-g2012', '-s', 'usb_fs_phy_tb',
                            f'-Pusb_fs_phy_tb.FREQ={frequency}',
                            f'-Pusb_fs_phy_tb.JITTER_NS={jitter}', '-o', str(executable),
                            str(ROOT / 'tests/primer20k_vexriscv/usb_fs_phy_tb.v'),
                            str(ROOT / 'gateware/vendor/usb_fs_phy/usb_fs_phy.v')],
                           env=env, check=True)
            result = subprocess.run([str(compiler.with_name('vvp.exe')), str(executable)],
                                    env=env, capture_output=True, text=True)
            (output / f'{name}.log').write_text(result.stdout + result.stderr, encoding='utf-8')
            print(result.stdout, end='')
            result.check_returncode()
            if 'PASS USB FS PHY' not in result.stdout:
                raise RuntimeError(f'{name}: simulation did not complete')


if __name__ == '__main__':
    main()
