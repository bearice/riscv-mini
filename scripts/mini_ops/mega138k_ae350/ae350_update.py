"""Bounded configuration-Flash update after verified bootloader installation."""
import subprocess
from pathlib import Path

from modular.storage import Store, read, file_hash
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate
from mini_ops.mega138k_ae350.ae350_bootrom import prepare, jedec_id, ERASE_BLOCK, boot_region
from gateware.machines.mega138k_ae350.boot_contract import FLASH_OFFSET


def configuration_image(text):
    """Decode the immutable, unencrypted Gowin FS rows in MSB byte order."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if '//Device: GW5AST-138' not in lines or '//Encryption: OFF' not in lines:
        raise ValueError('Select an unencrypted GW5AST-138 FS image')
    data = bytearray()
    for line in lines:
        if line.startswith('//'):
            continue
        if set(line) - set('01') or len(line) % 8:
            raise ValueError('Invalid Gowin FS byte row')
        data.extend(int(line, 2).to_bytes(len(line) // 8, 'big'))
    if not 4 <= len(data) < FLASH_OFFSET:
        raise ValueError('Configuration image exceeds the reserved Flash partition')
    return bytes(data)


def configuration_update(old, image):
    extent = ((len(image) + ERASE_BLOCK - 1) // ERASE_BLOCK) * ERASE_BLOCK
    if not image or extent > FLASH_OFFSET or len(old) != extent + ERASE_BLOCK:
        raise ValueError('Invalid configuration backup or partition extent')
    # Preserve the unused tail of the final erase block and a full next guard.
    return image + old[len(image):extent], image + old[len(image):]


def check_boot_receipt(receipt, boot):
    if (not receipt.get('passed') or not receipt.get('installed') or
            not receipt.get('guard_unchanged') or
            receipt.get('bootloader') != boot):
        raise ValueError('A matching successful bootrom installation is required')


def perform(args):
    op = Operation('ae350-update')
    hardware = None
    print('Operation logs:', op.path, flush=True)
    try:
        if not args.cable_serial or not args.bootrom_install:
            raise ValueError('AE350 update requires --cable-serial and --bootrom-install')
        if args.image or args.method != 'programmer':
            raise ValueError('AE350 update uses the selected FPGA configuration, method programmer')
        root, ref, build_id = candidate(args.build)
        store = Store()
        image, boot, flash_bytes = prepare(store, ref)
        check_boot_receipt(read(Path(args.bootrom_install)), boot)
        fs = root / 'gateware/impl/pnr/project.fs'
        configuration = configuration_image(fs.read_text())
        extent = ((len(configuration) + ERASE_BLOCK - 1) // ERASE_BLOCK) * ERASE_BLOCK
        op.report.update(artifact=ref, bootloader=boot, fs_sha256=file_hash(fs),
                         bootrom_install=str(args.bootrom_install), bytes=len(configuration),
                         offset=0, flash_written=False, sram_programmed=False)
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            op.save()
            return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350.ae350_upload import wait_loader
        from tools.boot_session import BootSession
        hardware = HardwareSession(op, port=args.port)
        uart = BootSession(hardware.open_serial(), hardware.open_log('uart.log'))
        hardware.check_fpga(args.cable_serial)
        base = [hardware.tools['openfpgaloader'], '-c', 'ft2232',
                '--ftdi-serial', args.cable_serial, '--external-flash']
        def run(name, options):
            log_path = op.path / (name + '.log')
            with log_path.open('w') as log:
                result = subprocess.run([*base, *options], stdout=log, stderr=subprocess.STDOUT,
                                        text=True, timeout=900 if name == 'install' else 360)
            output = log_path.read_text()
            if result.returncode:
                raise RuntimeError('Flash ' + name + ' failed; inspect operation log')
            return output
        boot_start, boot_bytes = boot_region(len(image), flash_bytes)
        boot_prefix = FLASH_OFFSET - boot_start
        before_boot = op.path / 'boot-neighborhood-before.bin'
        output = run('backup-boot', ['--dump-flash', '--offset', hex(boot_start),
                     '--file-size', str(boot_bytes), str(before_boot)])
        identity = jedec_id(output)
        if 1 << (identity & 0xff) != flash_bytes:
            raise RuntimeError('Detected Flash capacity differs from the selected board')
        boot_data = before_boot.read_bytes()
        if len(boot_data) != boot_bytes or boot_data[boot_prefix:boot_prefix + len(image)] != image:
            raise RuntimeError('Installed boot ROM does not match the selected image')
        backup = op.path / 'configuration-before.bin'
        output = run('backup-configuration', ['--dump-flash', '--offset', '0',
                     '--file-size', str(extent + ERASE_BLOCK), str(backup)])
        if jedec_id(output) != identity:
            raise RuntimeError('Flash identity changed during backup')
        programmed, expected = configuration_update(backup.read_bytes(), configuration)
        target = op.path / 'configuration-erase-blocks.bin'
        target.write_bytes(programmed)
        op.report.update(jedec=hex(identity), backup_sha256=file_hash(backup),
                         boot_backup_sha256=file_hash(before_boot), programmed_bytes=len(programmed),
                         image_sha256=file_hash(target), flash_written=True)
        op.save()
        print('Programming configuration Flash [0, 0x%x)' % extent, flush=True)
        run('install', ['--write-flash', '--verify', '--offset', '0', str(target)])
        after = op.path / 'configuration-after.bin'
        run('readback-configuration', ['--dump-flash', '--offset', '0',
            '--file-size', str(extent + ERASE_BLOCK), str(after)])
        if after.read_bytes() != expected:
            raise RuntimeError('Configuration readback or erase-block guard differs')
        after_boot = op.path / 'boot-neighborhood-after.bin'
        run('readback-boot', ['--dump-flash', '--offset', hex(boot_start),
            '--file-size', str(boot_bytes), str(after_boot)])
        if after_boot.read_bytes() != boot_data:
            raise RuntimeError('Configuration programming altered the bootloader neighborhood')
        op.report.update(readback_sha256=file_hash(after), boot_guard_unchanged=True,
                         configuration_guard_unchanged=True)
        op.save()
        # Explicit RELOAD, after all readbacks. No SRAM bitstream is loaded.
        hardware.port.reset_input_buffer()
        run('reload', ['--reset'])
        op.record('flash-boot', text=wait_loader(uart, build_id, 60))
        op.report.update(passed=True, qualification='configuration-readback-and-Flash-reload-boot',
                         boot_method='JTAG RELOAD from persistent Flash', physical_power_cycle=False)
        op.save()
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        op.save()
        raise
    finally:
        if hardware is not None:
            hardware.close()
