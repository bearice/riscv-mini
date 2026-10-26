"""Prepare or explicitly install only the configuration Flash firmware slot at 0x600000."""
import re
import subprocess

from modular.storage import Store, file_hash, read
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate
from gateware.machines.mega138k_ae350.boot_contract import FLASH_OFFSET, region_size

ERASE_BLOCK = 65536


def boot_neighborhood(old, image, flash_bytes):
    """Preserve the entire programmer erase block, plus both neighbor guards."""
    start, length = boot_region(len(image), flash_bytes)
    extent = ((len(image) + ERASE_BLOCK - 1) // ERASE_BLOCK) * ERASE_BLOCK
    prefix = FLASH_OFFSET - start
    if len(old) != length:
        raise ValueError('Incomplete boot erase-block backup or image')
    expected = old[:prefix] + image + old[prefix + len(image):]
    return expected[prefix:prefix + extent], expected


def boot_region(image_bytes, flash_bytes):
    if not 4 <= image_bytes <= region_size(flash_bytes):
        raise ValueError('Boot image exceeds the Flash firmware region')
    extent = ((image_bytes + ERASE_BLOCK - 1) // ERASE_BLOCK) * ERASE_BLOCK
    start = FLASH_OFFSET - ERASE_BLOCK
    end = min(flash_bytes, FLASH_OFFSET + extent + ERASE_BLOCK)
    return start, end - start


def prepare(store, ref):
    manifest = store.get(ref, recursive=True)
    if manifest['config'].get('instruction_memory') != 'SDK-Flash-U4+hard-ILM':
        raise ValueError('Select SDK Flash XIP gateware')
    boot = manifest['dependencies']['bootloader']
    layout = read(store.path(boot) / 'outputs/flash-layout.json')
    flash_bytes = layout['flash_bytes']
    # New artifacts export board geometry in both manifests. Historical
    # artifacts retain their producer's geometry in flash-layout.json.
    if (layout['flash'], layout['offset'], layout['erase_bytes']) != (
            'config-U4', FLASH_OFFSET, 4096) or flash_bytes != manifest['config'].get('boot_flash_bytes', flash_bytes):
        raise ValueError('Boot Flash partition contract mismatch')
    size = layout['size']
    if type(size) is not int or not 4 <= size <= region_size(flash_bytes):
        raise ValueError('Boot Flash partition contract mismatch')
    image = (store.path(boot) / 'outputs/boot.bin').read_bytes()
    if not 4 <= len(image) <= size:
        raise ValueError('Boot ROM exceeds the reserved partition')
    if layout.get('image_bytes', len(image)) != len(image):
        raise ValueError('Boot image length differs from its metadata')
    return image, boot, flash_bytes


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
        image, boot, flash_bytes = prepare(store, ref)
        target = op.path / 'bootrom.bin'
        target.write_bytes(image)
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, bootloader=boot, fs_sha256=file_hash(fs),
                         image_sha256=file_hash(target), offset=FLASH_OFFSET, bytes=len(image),
                         flash_written=False, installed=False, prepared=True)
        if args.dry_run or not args.install:
            op.report.update(passed=True, qualification='prepared-not-installed')
            print('Prepared config U4 [0x%06x, 0x%06x); use --install for Flash writes' %
                  (FLASH_OFFSET, FLASH_OFFSET + len(image)), flush=True)
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
                                        text=True, timeout=900)
            output = log_path.read_text()
            if result.returncode:
                raise RuntimeError('Flash ' + name + ' failed; inspect its operation log')
            return output
        # Preserve all touched erase blocks and available neighboring guards.
        start, backup_bytes = boot_region(len(image), flash_bytes)
        before = op.path / 'boot-neighborhood-before.bin'
        output = run('backup', ['--dump-flash', '--offset', hex(start), '--file-size', str(backup_bytes), str(before)])
        identity = jedec_id(output)
        if 1 << (identity & 0xff) != flash_bytes:
            raise RuntimeError('Detected Flash capacity differs from the selected board')
        old = before.read_bytes()
        block, expected = boot_neighborhood(old, image, flash_bytes)
        programmed = op.path / 'boot-erase-block.bin'
        programmed.write_bytes(block)
        op.report.update(jedec=hex(identity), backup_sha256=file_hash(before))
        op.save()
        # No bulk erase, protection changes or configuration bitstream writes.
        op.report.update(flash_written=True)  # May have changed even on error.
        op.save()
        run('install', ['--write-flash', '--verify', '--offset', hex(FLASH_OFFSET), str(programmed)])
        after = op.path / 'boot-neighborhood-after.bin'
        run('readback', ['--dump-flash', '--offset', hex(start), '--file-size', str(backup_bytes), str(after)])
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
