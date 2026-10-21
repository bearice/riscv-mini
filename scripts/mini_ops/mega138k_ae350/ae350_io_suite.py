"""Run the AE350 board I/O suite and judge its digital contract.

The receipt records that LED, button and WS2812 color were not observed.
Upload success is not a suite pass. SRAM only.
"""
import time

from modular.storage import Store, file_hash
from mini_ops.mega138k_ae350.ae350 import candidate
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file
from mini_ops.mega138k_ae350.ae350_io_contract import DONE, WORDS, judge, observe, SCOPE
from gateware.machines.mega138k_ae350 import address_map as amap


def perform(args):
    op = Operation('ae350-io-suite')
    print('Operation logs:', op.path, flush=True)
    hardware = None
    if not (args.poll_interval > 0 and 0 < args.timeout <= 3600):
        raise ValueError('Invalid I/O suite polling budget')
    try:
        root, ref, build_id = candidate(args.build)
        store = Store()
        manifest = store.get(ref)
        boot = store.get(manifest['dependencies']['bootloader'])
        if boot['config'].get('image_abi') != amap.image_abi():
            raise ValueError('Selected hardware uses a different address ABI')
        payload_ref, payload_root = resolve_payload(store, getattr(args, 'payload', None))
        payload = payload_file(payload_root).read_bytes()
        import scripts.mini_ops.mega138k_ae350.ae350_image
        image = ae350_image.build(payload)
        (op.path / 'image.bin').write_bytes(image)
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, fs_sha256=file_hash(fs), rom_build_id=build_id,
                         payload_artifact=payload_ref, payload_sha256=file_hash(payload_file(payload_root)),
                         payload_bytes=len(payload), flash_written=False, scope=SCOPE,
                         physical_observation='not-recorded',
                         qualification='I/O digital contract over UART; physical I/O is separate')
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            op.save()
            return op
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from tools.boot_session import BootSession
        from mini_ops.mega138k_ae350.ae350_upload import wait_loader
        hardware = HardwareSession(op, port=args.port, cpu_jtag_location=args.cpu_jtag_location,
                                   location=args.location)
        session = BootSession(hardware.open_serial(), hardware.open_log('uart.log'))
        hardware.program(fs)
        op.record('banner', text=wait_loader(session, build_id, min(args.timeout, 30)))
        session.upload(image, load=ae350_image.LOAD, abi=ae350_image.abi_tag())
        cpu = hardware.open_cpu()
        started = time.monotonic()
        deadline = started + args.timeout
        polls = []
        stage = 0
        while True:
            time.sleep(args.poll_interval)
            cpu.halt()
            stage = int.from_bytes(cpu.read_memory(amap.RESULTS_BASE, 4), 'little')
            polls.append({'t': round(time.monotonic() - started, 2), 'stage': hex(stage)})
            if stage == DONE or time.monotonic() >= deadline:
                break
            cpu.resume()
        raw = cpu.read_memory(amap.RESULTS_BASE, WORDS * 4)
        words = [int.from_bytes(raw[i:i + 4], 'little') for i in range(0, len(raw), 4)]
        trap_raw = cpu.read_memory(amap.RESULTS_BASE + amap.TRAP_RECORD * 4, amap.TRAP_RECORD_WORDS * 4)
        trap = [int.from_bytes(trap_raw[i:i + 4], 'little') for i in range(0, len(trap_raw), 4)]
        pc = cpu.register(0x7b1)
        mcause = cpu.register(0x342)
        failures = judge(words, pc, mcause, amap.DDR_CODE, len(payload), trap)
        op.record('results', completion=hex(stage), polls=polls[:40],
                  pc=hex(pc), mcause=hex(mcause), trap=[hex(item) for item in trap], **observe(words))
        op.record('assertions', failures=failures, passed=not failures)
        if failures:
            raise RuntimeError('I/O suite failed: ' + '; '.join(failures))
        hardware.close()
        hardware = None
        op.report.update(passed=True, phase='io-suite-digital', physical_observation='not-recorded')
        op.save()
        print('PASS I/O digital contract; physical observation is not recorded', flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        op.save()
        raise
    finally:
        if hardware is not None:
            hardware.close()
