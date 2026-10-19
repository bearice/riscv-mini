"""AE350 platform board operations with immutable artifact checks."""
import json
import re
import subprocess
from pathlib import Path

from build_records import ROOT
from modular.storage import Store, read, file_hash
from mini_ops.board import Operation


def candidate(selection):
    if selection == 'current':
        raise ValueError('AE350 requires an explicit gateware build run; 20K current is not applicable')
    path = Path(selection)
    if path.is_dir():
        path /= 'result.json'
    result = read(path)
    ref = result['artifact']
    store = Store()
    manifest = store.get(ref, recursive=True)
    config = manifest['config']
    if ref['component'] != 'gateware' or config.get('machine') != 'mega138k_ae350' or not config.get('synthesize'):
        raise ValueError('Board test requires synthesized AE350 gateware')
    root = store.path(ref)
    timing = read(root / 'timing.json')
    from modular.ae350 import timing_report
    verified_timing = timing_report(root)
    if timing != verified_timing:
        raise ValueError('AE350 timing metadata differs from the implementation report')
    boot = store.get(manifest['dependencies']['bootloader'])
    return root, ref, boot['config']['build_id']


def perform(args):
    if args.operation == 'debug-scan':
        return debug_scan(args)
    if args.operation not in ('run', 'recover', 'verify'):
        raise ValueError('AE350 supports SRAM run/recover/verify only')
    root, ref, build_id = candidate(args.build)
    if not Store().get(ref, recursive=True)['config'].get('ddr_profile'):
        raise ValueError('Retired fabric ROM/SRAM monitor is unsupported; select current AE350 DDR gateware')
    from mini_ops.ae350_boot_ops import perform as perform_boot
    return perform_boot(args, root, ref, build_id)


def debug_scan(args):
    op = Operation('ae350-debug-scan')
    print('Operation logs:', op.path, flush=True)
    try:
        tools = json.loads((ROOT / '.tools.local.json').read_text(encoding='utf-8'))
        executable = Path(tools.get('openocd', 'C:/Gowin/GMD/toolchain/gowin/openocd/bin/openocd.exe'))
        if not executable.is_file():
            raise ValueError('Configure openocd in .tools.local.json')
        config = '\n'.join(['adapter driver ftdi', 'ftdi vid_pid 0x0403 0x6010',
                            'ftdi channel 0', 'ftdi layout_init 0x0008 0x000b',
                            'transport select jtag', 'adapter speed 100', 'reset_config none',
                            'jtag newtap fpga tap -irlen 8 -expected-id 0x0001081b',
                            'init', 'scan_chain', 'shutdown'])
        path = op.path / 'scan.cfg'
        path.write_text(config, encoding='utf-8')
        if args.dry_run:
            op.report.update(dry_run=True, passed=True)
            return op
        result = subprocess.run([str(executable), '-f', str(path)], capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        (op.path / 'openocd.log').write_text(output, encoding='utf-8')
        op.record('openocd', executable=str(executable), binary_sha256=file_hash(executable),
                  exit_code=result.returncode, output=output)
        print(output, flush=True)
        op.report['passed'] = result.returncode == 0
        if result.returncode:
            raise RuntimeError('OpenOCD JTAG discovery failed; inspect ' + str(op.path / 'openocd.log'))
    except Exception as error:
        op.report['error'] = str(error)
        raise
    finally:
        op.save()
    return op
