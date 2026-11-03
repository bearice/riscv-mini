"""AE350 persistent boot: BIOS image in the U7 user-flash boot slot, unattended boot check.

`board bios-install` talks to a running BIOS (UART-loaded or already persistent):
it stages the loader image over TFTP and lets the BIOS check, erase, program and
read back the slot. `board verify --suite autoboot` SRAM-programs gateware and
sends nothing: the ROM must take the image from U7 and the BIOS must boot SD Linux.
"""
import json
import re
import zlib
from types import SimpleNamespace

from scripts.mini_ops.common import Operation
from scripts.mini_ops.primer20k_vexriscv.linux import server
from scripts.tools.build_records import ROOT
from scripts.mini_ops.mega138k_ae350.linux import Console, board_address, loaded, FW_PAYLOAD

IMAGE_NAME = 'BIOS.IMG'
ERASE_SECTOR = 4096
SLOT = re.compile(r'FLASH BOOT (IMAGE abi=([0-9a-f]{8}) bytes=([0-9]+) crc=([0-9a-f]{8})|BLANK|OTHER)')


def slot_state(text):
    """Parse the BIOS `flash` report; the identity line must name the boot slot."""
    text = text.decode(errors='replace')
    identity = re.search(r'FLASH jedec=([0-9a-f]{8}) bytes=([0-9]+) boot-slot=([0-9a-f]{8})\+([0-9a-f]{8})', text)
    found = SLOT.search(text)
    if not identity or not found:
        raise RuntimeError('BIOS did not report the user-flash boot slot')
    state = dict(jedec=identity.group(1), bytes=int(identity.group(2)),
                 offset=int(identity.group(3), 16), slot_bytes=int(identity.group(4), 16))
    if found.group(2):
        state.update(kind='image', abi=int(found.group(2), 16), payload_bytes=int(found.group(3)),
                     payload_crc32=found.group(4))
    else:
        state.update(kind=found.group(1).lower())
    return state


def bios_install(args):
    op = Operation('ae350-bios-install')
    try:
        if not args.port or not args.build or not args.payload or args.timeout <= 0:
            raise ValueError('Explicit --port, --build, --payload and positive --timeout required')
        from modular.storage import Store
        from gateware.machines.mega138k_ae350 import address_map as amap
        from mini_ops.mega138k_ae350 import ae350_image
        from mini_ops.mega138k_ae350.ae350 import candidate, hardware_abi
        from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file
        store = Store()
        _, ref, boot_id = candidate(args.build)
        abi = hardware_abi(store, ref)
        payload_ref, payload_root = resolve_payload(store, args.payload, expected_abi=abi)
        payload = payload_file(payload_root).read_bytes()
        image = ae350_image.build(payload, abi)
        if len(image) > amap.USER_FLASH_BOOT_BYTES or len(image) > amap.BIOS_STAGING_BYTES:
            raise ValueError('BIOS image exceeds the user-flash boot slot')
        path = op.path / IMAGE_NAME
        path.write_bytes(image)
        crc = zlib.crc32(image)
        board_ip = board_address(args.host_ip, getattr(args, 'board_ip', None))
        op.report.update(artifact=ref, rom_build_id=boot_id, payload_artifact=payload_ref, image_abi=abi,
                         image_bytes=len(image), image_crc32='%08x' % crc, payload_crc32='%08x' % zlib.crc32(payload),
                         slot_offset=amap.USER_FLASH_BOOT_OFFSET, flash_written=False,
                         network=dict(host_ip=args.host_ip, board_ip=board_ip))
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            return op
        import serial
        with serial.Serial(args.port, 115200, timeout=.1) as port, (op.path/'bios-uart.log').open('wb') as log:
            console = Console(port, log, args.timeout)
            console.prompt()
            before = slot_state(console.command('flash'))
            op.report['slot_before'] = before
            if before['offset'] != amap.USER_FLASH_BOOT_OFFSET or before['slot_bytes'] != amap.USER_FLASH_BOOT_BYTES:
                raise RuntimeError('Running BIOS uses a different user-flash boot slot')
            # The sectors the install erases; a blank header alone does not prove they are unused.
            erase = -(-len(image) // ERASE_SECTOR) * ERASE_SECTOR
            reply = console.command('flash crc %x %x' % (amap.USER_FLASH_BOOT_OFFSET, erase))
            found = re.search(rb'==> ([0-9a-f]{8})', reply)
            if not found:
                raise RuntimeError('BIOS did not report the boot-slot CRC')
            before.update(erase_bytes=erase, erase_crc32=found.group(1).decode())
            if before['kind'] == 'blank' and int(before['erase_crc32'], 16) != zlib.crc32(b'\xff' * erase):
                before['kind'] = 'other'
            if before['kind'] == 'other' and not args.replace_other:
                raise RuntimeError('User-flash boot slot holds non-image data; --replace-other overwrites it')
            console.network(board_ip, args.host_ip)
            with server(SimpleNamespace(op=op), args, path, IMAGE_NAME):
                reply = console.command('tftp %08x %s' % (amap.BIOS_STAGING_BASE, IMAGE_NAME))
            if b'TFTP OK' not in reply or ('Bytes transferred = %d' % len(image)).encode() not in reply:
                raise RuntimeError('TFTP length mismatch: '+IMAGE_NAME)
            if ('==> %08x' % crc).encode() not in console.command('crc32 %08x %x' % (amap.BIOS_STAGING_BASE, len(image))).lower():
                raise RuntimeError('Staged BIOS image CRC mismatch')
            op.report['flash_written'] = True  # May have changed even on error.
            op.save()
            reply = console.command('flash install %08x %x' % (amap.BIOS_STAGING_BASE, len(image)))
            if ('FLASH INSTALL OK bytes=%d crc=%08x' % (len(image), crc)).encode() not in reply:
                raise RuntimeError('BIOS did not confirm the installed image')
            after = slot_state(console.command('flash'))
            op.report['slot_after'] = after
            if (after['kind'], after.get('abi'), after.get('payload_bytes'), after.get('payload_crc32')) != (
                    'image', abi, len(payload), '%08x' % zlib.crc32(payload)):
                raise RuntimeError('Boot slot header differs from the installed image')
        op.report.update(passed=True, installed=True, qualification='user-flash-slot-installed-and-read-back')
        print('PASS AE350 BIOS installed in U7 boot slot (%d bytes, crc %08x)' % (len(image), crc), flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        raise
    finally:
        op.save()
        print('Operation logs:', op.path, flush=True)


def autoboot(args):
    """SRAM gateware, then observe ROM -> U7 BIOS -> SD -> OpenSBI -> Linux without UART input."""
    op = Operation('ae350-autoboot')
    hardware = None
    try:
        if not args.port or not args.cable_serial:
            raise ValueError('Explicit --port and --cable-serial required')
        from gateware.description import resolve_machine
        from gateware.machines.mega138k_ae350.address_map import AddressMap
        from mini_ops.mega138k_ae350.ae350 import candidate
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        root, ref, boot_id = candidate(args.build)
        layout = AddressMap(resolve_machine('mega138k_ae350'))
        layout.require()
        op.report.update(artifact=ref, rom_build_id=boot_id, flash_written=False)
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            return op
        hardware = HardwareSession(op, port=args.port, location=args.location)
        port = hardware.open_serial()
        console = Console(port, hardware.open_log('uart.log'), args.timeout)
        port.reset_input_buffer()
        hardware.program(root / 'gateware/impl/pnr/project.fs', cable_serial=args.cable_serial)
        boot = console.until(b'linux-mini> ')
        text = boot.decode(errors='replace')
        # A BIOS that starts fw_payload.bin hands the Image and DTB loads to U-Boot `sdboot`.
        uboot = 'LOAD OK %s ' % FW_PAYLOAD in text
        order = ['riscv-mini AE350 ROM ' + boot_id, 'POST 600d0000', 'USER FLASH BOOT', 'SYSTEM READY',
                 'AE350 BIOS READY', 'AUTOBOOT SD']
        if uboot:
            version = json.loads((ROOT/'firmware/uboot/ae350/source.json').read_text())['version'].lstrip('v')
            order += ['LOAD OK ' + FW_PAYLOAD, 'OpenSBI', 'U-Boot ' + version, 'Hit any key to stop autoboot',
                      'CRC32 for', 'Starting kernel']
        else:
            order += ['LOAD OK Image', 'OpenSBI']
        position = 0
        for marker in order:
            position = text.find(marker, position)
            if position < 0:
                raise RuntimeError('Autoboot evidence missing or out of order: ' + marker)
        op.report['sd_files'] = loaded(text, layout)
        if getattr(args, 'sd_install_result', None):
            from modular.storage import read
            expected = read(args.sd_install_result)['sd_files']
            if op.report['sd_files'] != expected:
                raise RuntimeError('SD boot files differ from the recorded installation')
        console.userspace(op, boot, layout, uboot)
        chain = 'U7 BIOS -> SD -> OpenSBI -> U-Boot -> Linux' if uboot else 'U7 BIOS -> SD -> Linux'
        op.report.update(passed=True, boot_chain=chain,
                         qualification='unattended-rom-user-flash-sd-%slinux' % ('uboot-' if uboot else ''))
        print('PASS AE350 autoboot: ROM %s -> %s userspace' % (boot_id, chain), flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        raise
    finally:
        if hardware is not None:
            hardware.close()
        op.save()
        print('Operation logs:', op.path, flush=True)
