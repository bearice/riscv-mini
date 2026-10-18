"""Explicit AE350 P1 SRAM-only board qualification with immutable artifact checks."""
import json
import re
import subprocess
import time
from pathlib import Path

from build_records import ROOT
from boot_upload import programmer_succeeded
from modular.storage import Store, read, file_hash
from programmer_cable import resolve_location
from mini_ops.board import Operation


def candidate(selection):
    if selection == 'current':
        raise ValueError('AE350 P1 requires an explicit gateware build run; 20K current is not applicable')
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
    if timing != dict(setup=0, hold=0):
        raise ValueError('AE350 candidate must have setup/hold 0/0')
    boot = store.get(manifest['dependencies']['bootloader'])
    return root, ref, boot['config']['build_id']


def perform(args):
    if args.operation == 'debug-scan':
        return debug_scan(args)
    if args.operation not in ('run', 'recover', 'verify'):
        raise ValueError('AE350 P1 supports SRAM run/recover/verify only')
    if args.operation == 'verify' and args.suite != 'firmware':
        raise ValueError('AE350 P1 only supports the firmware verification suite')
    if getattr(args, 'image', None) or getattr(args, 'mic', False) or getattr(args, 'soak_seconds', 0):
        raise ValueError('AE350 P1 does not support legacy application/peripheral overrides')
    op = Operation('ae350-' + args.operation)
    print('Operation logs:', op.path, flush=True)
    try:
        root, ref, build_id = candidate(args.build)
        fs = root / 'gateware/impl/pnr/project.fs'
        expected = file_hash(fs)
        op.report.update(machine='mega138k_ae350', artifact=ref, fs_sha256=expected,
                         rom_build_id=build_id, flash_written=False, timing=dict(setup=0, hold=0))
        if args.dry_run:
            op.report.update(dry_run=True, passed=True)
            return op
        tools = json.loads((ROOT / '.tools.local.json').read_text(encoding='utf-8'))
        programmer = tools['programmer']
        location = resolve_location(programmer, args.location, log_path=op.path / 'cables.log')
        base = [programmer, '--cable-index', '4', '--location', location, '--frequency', '2MHz']
        scan = subprocess.run([*base, '--scan'], capture_output=True, text=True, timeout=30)
        (op.path / 'scan.log').write_text(scan.stdout + scan.stderr, encoding='utf-8')
        if scan.returncode or not re.search(r'ID:\s*0x0001081[Bb]', scan.stdout) or '1 device(s) found' not in scan.stdout:
            raise ValueError('JTAG scan does not identify one compatible GW5AS/GW5AST 138 device')
        op.record('jtag_identity', idcode='0x0001081B', location=location,
                  note='ID is shared by GW5AS/GW5AST B/C; package and speed grade remain unconfirmed')
        import serial
        with serial.Serial(args.port, 115200, timeout=.1) as port, (op.path / 'uart.log').open('wb') as log:
            def receive(token, timeout=12):
                data = bytearray()
                deadline = time.monotonic() + timeout
                while token not in data and time.monotonic() < deadline:
                    chunk = port.read(4096)
                    data.extend(chunk); log.write(chunk); log.flush()
                if token not in data:
                    raise RuntimeError('AE350 UART timeout waiting for ' + repr(token) + ': ' + data[-512:].decode(errors='replace'))
                return bytes(data)

            def program(name):
                if file_hash(fs) != expected:
                    raise ValueError('AE350 bitstream changed since preflight')
                port.reset_input_buffer()
                command = [*base, '--device', 'GW5AST-138B', '--operation_index', '2', '--fsFile', str(fs)]
                print('SRAM program:', name, flush=True)
                result = subprocess.run(command, capture_output=True, text=True, timeout=120)
                (op.path / (name + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
                if not programmer_succeeded(result):
                    raise RuntimeError('SRAM programming failed; inspect ' + str(op.path / (name + '.log')))
                op.record(name, command=command, programmer_passed=True)
                if getattr(args, 'cpu_jtag_location', None) is not None:
                    from mini_ops.ae350_jtag import restart
                    store = Store()
                    boot = store.get(ref)['dependencies']['bootloader']
                    binary = (store.path(boot) / 'outputs/boot.bin').read_bytes()
                    debug = restart(args.cpu_jtag_location,
                                    Path(programmer).with_name('ftd2xx.dll'), binary,
                                    port.reset_input_buffer)
                    op.record(name + '-cpu-jtag-restart', location=args.cpu_jtag_location, **debug)
                banner = receive(b'mcycle=')
                banner += receive(b'\r\n') if not banner.endswith(b'\r\n') else b''
                if ('build=' + build_id).encode() not in banner or b'DDR=off' not in banner:
                    raise RuntimeError('AE350 UART build identity does not match selected ROM')
                op.record(name + '-boot', output=banner.decode(errors='replace'))
                print(banner.decode(errors='replace'), flush=True)

            program('sram')
            cycles = []
            for _ in range(3):
                port.write(b's'); port.flush()
                reply = receive(b'\r\n')
                match = re.search(rb'misa=([0-9a-f]{8}) mcycle=([0-9a-f]{8})', reply)
                if not match:
                    raise RuntimeError('Malformed AE350 status: ' + repr(reply))
                cycles.append(int(match[2], 16))
                time.sleep(.1)
            if any(a == b for a, b in zip(cycles, cycles[1:])):
                raise RuntimeError('AE350 cycle counter did not advance')
            op.record('status', misa=match[1].decode(), cycles=cycles, frequency_measured=False)
            marker = b'AE350-P1-ECHO-0123456789\r\n'
            port.write(marker); port.flush()
            receive(marker)
            op.record('uart_echo', passed=True)
            port.write(b'e'); port.flush()
            trap = receive(b'HALTED; recover with JTAG/reset\r\n')
            if b'TRAP mcause=00000003' not in trap:
                raise RuntimeError('AE350 ebreak trap cause differs: ' + repr(trap))
            op.record('ebreak', output=trap.decode(errors='replace'))
            program('recovery-sram')
        op.report.update(passed=True, device_identity='SRAM programmed and ROM build ID matched over UART',
                         qualification='P1-ROM-UART-trap-recovery',
                         remaining=['timer-IRQ', 'CPU-frequency-measurement-and-stress', 'DDR'])
        print('PASS:', op.path / 'result.json', flush=True)
    except Exception as error:
        op.report['error'] = str(error)
        raise
    finally:
        op.save()
    return op


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
