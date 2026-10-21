"""Upload an AE350 boot image to the ROM loader over UART.

Reuses the 20K client (`boot_upload.BootSession`) for the banner handshake, the
packet framing (`boot_image.packet`) and the acknowledgements, and the 20K
image rules (`boot_image.pack_image`/`unpack_image`) with the AE350 load address.
JTAG is used only to restart the ROM entry and to read where the CPU ended up.
"""
import json
import time

from modular.storage import Store, file_hash
from mini_ops.common import Operation
from gateware.machines.mega138k_ae350 import address_map as amap
from mini_ops.ae350 import candidate
from tools.boot_session import BootSession
import ae350_image


from mini_ops.ae350_payload import resolve_payload, payload_file

# The AE350 ROM identity banner, matched through the shared 20K client.
BANNER = rb'(?:^|\n)(riscv-mini AE350 ROM [0-9a-f]+\r\nPOST [0-9a-f]+\r\n(?:DDR INIT OK|ERR DDR INIT; DLM/JTAG only)\r\nBL> )$'


def wait_loader(session, build_id, timeout):
    text = session.fresh_banner(timeout, pattern=BANNER).decode('ascii', 'replace')
    if 'POST 600d0000' not in text or ('ROM ' + build_id) not in text:
        raise RuntimeError('ROM POST/build identity mismatch: ' + text[-240:])
    return text


def perform_upload(args, selected=None):
    op = Operation('ae350-upload')
    print('Operation logs:', op.path, flush=True)
    hardware = None
    log = None
    if args.repeat < 1 or args.timeout <= 0:
        raise ValueError('repeat and timeout must be positive')
    if (args.self_test or args.repeat > 1) and not args.cpu_jtag_location:
        raise ValueError('self-test and repeated uploads require --cpu-jtag-location')
    try:
        root, ref, build_id = selected if selected is not None else candidate(args.build)
        store = Store()
        boot_ref = store.get(ref)['dependencies']['bootloader']
        if store.get(boot_ref)['config'].get('image_abi') != amap.image_abi():
            raise ValueError('Selected hardware uses a different address ABI')
        payload_ref, payload_root = resolve_payload(store, getattr(args, 'payload', None))
        payload = payload_file(payload_root).read_bytes()
        image = ae350_image.build(payload)
        (op.path / 'image.bin').write_bytes(image)
        op.report.update(artifact=ref, rom_build_id=build_id, payload_artifact=payload_ref,
                         requested_operation=getattr(args, 'operation', 'run'),
                         suite=getattr(args, 'suite', None),
                         payload_sha256=file_hash(payload_file(payload_root)),
                         payload_bytes=len(payload), image_bytes=len(image),
                         image_sha256=file_hash(op.path / 'image.bin'),
                         abi=hex(ae350_image.abi_tag()), flash_written=False)
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            op.save()
            return op
        from mini_ops.ae350_session import HardwareSession
        hardware = HardwareSession(op, args.port, args.cpu_jtag_location, args.location)
        hardware.open_serial()
        log = hardware.open_log('uart.log')
        session = BootSession(hardware.port, log)
        hardware.program(root / 'gateware/impl/pnr/project.fs')

        def restart():
            boot = store.get(ref)['dependencies']['bootloader']
            hardware.restart_rom(expected_rom=(store.path(boot) / 'outputs/boot.bin').read_bytes())

        def wait_banner():
            return wait_loader(session, build_id, args.timeout)

        try:
            # No JTAG on the success path: the port is already open, so the ROM's
            # own reset banner is captured directly.
            op.record('banner', text=wait_banner())
            if args.self_test:
                _negative_cases(session, image, payload, args, op, restart, wait_banner)
            uploads = []
            for round_index in range(args.repeat):
                if round_index or args.self_test:
                    restart(); time.sleep(0.05); wait_banner()   # re-entry only
                result = session.upload(image, load=ae350_image.LOAD, abi=ae350_image.abi_tag())
                uploads.append(result.decode('ascii', 'replace')[-40:])
            op.record('upload', phase='uploaded-and-handoff', rounds=uploads,
                      payload_bytes=len(payload), image_bytes=len(image), repeat=args.repeat)
        finally:
            log.close()


        if args.cpu_jtag_location:
            time.sleep(0.3)
            cpu = hardware.halt()
            pc = cpu.register(0x7b1)
            mcause = cpu.register(0x342)
            sr = [int.from_bytes(cpu.read_memory(amap.RESULTS_BASE + 4 * i, 4), 'little') for i in range(16)]
            running_in_payload = ae350_image.LOAD <= pc < ae350_image.LOAD + len(payload)
            op.record('cpu', pc=hex(pc), mcause=hex(mcause), results=[hex(v) for v in sr],
                      phase='executing' if (running_in_payload and mcause == 0) else 'handoff-only')
            if not (running_in_payload and mcause == 0):
                raise RuntimeError('payload did not execute: pc=%#x mcause=%#x' % (pc, mcause))
        phase = 'uploaded-executed' if args.cpu_jtag_location else 'uploaded-and-handoff'
        hardware.close()
        hardware = None
        # passed means the image executed (PC in range, mcause 0), not that a
        # payload's own checks succeeded. Suite judgments live in their operations.
        op.report.update(passed=True, phase=phase)
        op.save()
        print('PASS AE350 UART ' + phase, flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        op.save()
        raise
    finally:
        if log is not None and not log.closed:
            log.close()
        if hardware is not None:
            hardware.close()



def _negative_cases(session, image, payload, args, op, restart, wait_banner):
    """Rejection paths, each from a freshly restarted ROM (independent state)."""
    import struct
    import zlib
    from tools.boot_image import HEADER, CHUNK, packet

    cases = []

    def expect(markers, timeout=None):
        data = bytearray()
        deadline = time.monotonic() + (timeout or min(args.timeout, 20))
        while time.monotonic() < deadline:
            chunk = session.port.read(1)
            if not chunk:
                continue
            data.extend(chunk); session.note(chunk)
            for marker in markers:
                if data.endswith(marker):
                    return marker, bytes(data)
        return None, bytes(data)

    def begin(header):
        restart(); wait_banner()
        session.port.write(b'u'); session.port.flush()
        expect([b'READY HEADER\r\n'])
        session.port.write(header); session.port.flush()

    # ABI mismatch with a valid header CRC: the ROM must refuse it.
    words_abi = list(struct.unpack('<12I', image[:HEADER.size]))
    words_abi[3] ^= 0xffffffff
    words_abi[11] = zlib.crc32(struct.pack('<11I', *words_abi[:11]))
    bad_abi = bytearray(image); bad_abi[:HEADER.size] = struct.pack('<12I', *words_abi)
    begin(bytes(bad_abi[:HEADER.size]))
    marker, _ = expect([b'ERR ABI\r\n', b'READY DATA\r\n'])
    cases.append(dict(case='image_abi', expected='ERR ABI',
                      got=(marker or b'(none)').decode('ascii', 'replace')))

    bad = bytearray(image); bad[44] ^= 0xff
    begin(bytes(bad[:HEADER.size]))
    marker, _ = expect([b'ERR HEADER\r\n', b'READY DATA\r\n'])
    cases.append(dict(case='header_crc', expected='ERR HEADER',
                      got=(marker or b'(none)').decode('ascii', 'replace')))

    words = list(struct.unpack('<12I', image[:HEADER.size]))
    words[5] = words[6] = 0x00100000
    words[11] = zlib.crc32(struct.pack('<11I', *words[:11]))
    bad2 = bytearray(image); bad2[:HEADER.size] = struct.pack('<12I', *words)
    begin(bytes(bad2[:HEADER.size]))
    marker, _ = expect([b'ERR RANGE\r\n', b'READY DATA\r\n'])
    cases.append(dict(case='load_range', expected='ERR RANGE',
                      got=(marker or b'(none)').decode('ascii', 'replace')))

    begin(image[:HEADER.size])
    expect([b'READY DATA\r\n'])
    session.port.write(packet(0, payload[:CHUNK])); session.port.flush()
    expect([b'K' + struct.pack('<I', 0)], 5)
    started = time.monotonic()
    marker, _ = expect([b'ERR UPLOAD\r\n'], max(60, args.timeout))
    cases.append(dict(case='truncated', expected='ERR UPLOAD',
                      got=(marker or b'(none)').decode('ascii', 'replace'),
                      seconds=round(time.monotonic() - started, 1)))

    bad3 = bytearray(image); bad3[HEADER.size + 16] ^= 0xff
    body = bytes(bad3[HEADER.size:])          # the corrupted payload must be sent
    begin(bytes(bad3[:HEADER.size]))
    marker, _ = expect([b'READY DATA\r\n'])
    if marker == b'READY DATA\r\n':
        for sequence, start in enumerate(range(0, len(body), CHUNK)):
            session.port.write(packet(sequence, body[start:start + CHUNK])); session.port.flush()
            expect([b'K' + struct.pack('<I', sequence)], 10)
        marker, _ = expect([b'ERR CRC\r\n'], max(30, args.timeout))
    cases.append(dict(case='payload_crc', expected='ERR CRC',
                      got=(marker or b'(none)').decode('ascii', 'replace')))

    op.record('negative', cases=cases)
    failures = [c for c in cases if c['expected'] not in c['got']]
    if failures:
        raise RuntimeError('Loader rejection paths failed: ' + json.dumps(failures))
