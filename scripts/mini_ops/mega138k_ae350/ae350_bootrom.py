"""Prepare or explicitly install only the configuration Flash firmware slot at 0x600000."""
import re
import subprocess

from modular.storage import Store, file_hash, read
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate
from gateware.machines.mega138k_ae350.boot_contract import FLASH_BYTES, FLASH_OFFSET, ROM_SIZE

ERASE_BLOCK = 65536


def boot_neighborhood(old, image):
    """Preserve the entire programmer erase block, plus both neighbor guards."""
    if len(old) != 3 * ERASE_BLOCK or len(image) != ROM_SIZE:
        raise ValueError('Incomplete boot erase-block backup or image')
    expected = old[:ERASE_BLOCK] + image + old[ERASE_BLOCK + ROM_SIZE:]
    return expected[ERASE_BLOCK:2 * ERASE_BLOCK], expected


def prepare(store, ref):
    manifest = store.get(ref, recursive=True)
    if manifest['config'].get('instruction_memory') != 'SDK-Flash-U4+hard-ILM':
        raise ValueError('Select SDK Flash XIP gateware')
    boot = manifest['dependencies']['bootloader']
    layout = read(store.path(boot) / 'outputs/flash-layout.json')
    if (layout['flash'], layout['flash_bytes'], layout['offset'], layout['size'], layout['erase_bytes']) != (
            'config-U4', FLASH_BYTES, FLASH_OFFSET, ROM_SIZE, 4096):
        raise ValueError('Boot Flash partition contract mismatch')
    image = (store.path(boot) / 'outputs/boot.bin').read_bytes()
    if not 4 <= len(image) <= ROM_SIZE:
        raise ValueError('Boot ROM exceeds the reserved partition')
    return image.ljust(ROM_SIZE, b'\xff'), boot


def jedec_id(output):
    # Recognized models have a single six-digit ID. Unknown models print
    # three separate bytes, and must not pass this installation gate.
    identities = re.findall(r'JEDEC ID:\s*0x([0-9a-f]{6})\b', output, re.I)
    if not identities or len(set(identities)) != 1:
        raise ValueError('Cannot verify a unique configuration Flash JEDEC ID')
    identity = int(identities[0], 16)
    # Known 128-Mbit NORs with 4-KiB subsector erase. Do not infer erase
    # geometry from the density byte alone, or unlock protected blocks.
    if identity not in (0xef4018, 0xc84018, 0x204018):
        raise ValueError('Unqualified configuration Flash JEDEC: 0x%06x' % identity)
    return identity


def perform(args):
    op = Operation('ae350-bootrom')
    hardware = None
    print('Operation logs:', op.path, flush=True)
    try:
        root, ref, build_id = candidate(args.build)
        store = Store()
        timing = read(root / 'timing.json')
        if timing['setup'] or timing['hold'] or timing['min_pulse_width_slack_ns'] < 0:
            raise ValueError('Boot ROM installation requires passing complete STA')
        image, boot = prepare(store, ref)
        target = op.path / 'bootrom-8k.bin'
        target.write_bytes(image)
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, bootloader=boot, fs_sha256=file_hash(fs),
                         image_sha256=file_hash(target), offset=FLASH_OFFSET, bytes=ROM_SIZE,
                         flash_written=False, installed=False, prepared=True)
        if args.dry_run or not args.install:
            op.report.update(passed=True, qualification='prepared-not-installed')
            print('Prepared config U4 [0x%06x, 0x%06x); use --install for Flash writes' %
                  (FLASH_OFFSET, FLASH_OFFSET + ROM_SIZE), flush=True)
            op.save()
            return op
        if not args.cable_serial:
            raise ValueError('--install requires the FPGA cable --cable-serial')
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350.ae350_upload import wait_loader
        from tools.boot_session import BootSession
        hardware = HardwareSession(op, port=args.port, location=args.location)
        session = BootSession(hardware.open_serial(), hardware.open_log('uart.log'))
        hardware.check_fpga(args.cable_serial)
        tool = hardware.tools.get('openfpgaloader')
        if not tool:
            raise ValueError('Configure openfpgaloader for bounded Flash readback')
        base = [tool, '-c', 'ft2232', '--ftdi-serial', args.cable_serial, '--external-flash']
        def run(name, options):
            log_path = op.path / (name + '.log')
            with log_path.open('w') as log:
                result = subprocess.run([*base, *options], stdout=log, stderr=subprocess.STDOUT,
                                        text=True, timeout=180)
            output = log_path.read_text()
            if result.returncode:
                raise RuntimeError('Flash ' + name + ' failed; inspect its operation log')
            return output
        # The programmer may erase a 64-KiB block even for an 8-KiB image.
        # Read/modify/write its whole block, and guard both adjacent blocks.
        start = FLASH_OFFSET - ERASE_BLOCK
        before = op.path / 'boot-neighborhood-before.bin'
        output = run('backup', ['--dump-flash', '--offset', hex(start), '--file-size', str(3 * ERASE_BLOCK), str(before)])
        identity = jedec_id(output)
        old = before.read_bytes()
        block, expected = boot_neighborhood(old, image)
        programmed = op.path / 'boot-erase-block.bin'
        programmed.write_bytes(block)
        op.report.update(jedec=hex(identity), backup_sha256=file_hash(before))
        op.save()
        # No bulk erase, protection changes or configuration bitstream writes.
        op.report.update(flash_written=True)  # May have changed even on error.
        op.save()
        run('install', ['--write-flash', '--verify', '--offset', hex(FLASH_OFFSET), str(programmed)])
        after = op.path / 'boot-neighborhood-after.bin'
        run('readback', ['--dump-flash', '--offset', hex(start), '--file-size', str(3 * ERASE_BLOCK), str(after)])
        actual = after.read_bytes()
        if actual != expected:
            raise RuntimeError('Flash readback or guard differs; backup retained')
        op.report.update(installed=True, readback_sha256=file_hash(after), guard_unchanged=True,
                         backup_start=start, backup_bytes=len(old), programmed_bytes=len(block))
        hardware.program(fs, cable_serial=args.cable_serial)
        op.record('banner', text=wait_loader(session, build_id, 60))
        op.report.update(passed=True, qualification='boot-slot-installed-and-autonomous-loader-observed')
        op.save()
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        op.save()
        raise
    finally:
        if hardware is not None:
            hardware.close()
