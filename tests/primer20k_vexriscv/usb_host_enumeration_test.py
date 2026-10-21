"""Run TinyUSB enumeration against the real Ultra HCD with register mocks."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default='C:/msys64/ucrt64/bin/gcc.exe')
    parser.add_argument('--build-dir', required=True, type=Path,
                        help='Full Ultra USB build providing generated headers')
    args = parser.parse_args()
    compiler = Path(args.cc).resolve()
    output = ROOT / 'build/reports/checks/usb-host-enumeration'
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PATH': str(compiler.parent) + os.pathsep + os.environ['PATH'],
           'TMP': str(output), 'TEMP': str(output), 'TMPDIR': str(output)}
    usb = ROOT / 'firmware/vendor/tinyusb/src'
    sources = [ROOT / 'tests/primer20k_vexriscv/usb_host_enumeration_test.c', ROOT / 'firmware/hal/src/hcd_ultra.c',
               *[usb / name for name in ('tusb.c', 'host/hub.c', 'class/hid/hid_host.c', 'common/tusb_fifo.c')]]
    executable = output / 'enumeration.exe'
    subprocess.run([str(compiler), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-ffunction-sections', '-fdata-sections', '-DUSB_ULTRA_TEST',
                    '-I', str(ROOT / 'firmware/hal/include'), '-I', str(usb),
                    '-I', str(args.build_dir.resolve() / 'software/include'),
                    *map(str, sources), '-Wl,--gc-sections', '-o', str(executable)],
                   env=env, check=True)
    result = subprocess.run([str(executable)], env=env, capture_output=True, text=True)
    (output / 'enumeration.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    print(result.stdout, end='')
    result.check_returncode()


if __name__ == '__main__':
    main()
