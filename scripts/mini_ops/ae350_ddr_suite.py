"""Load diagnostics through the normal UART loader; JTAG only observes DLM results."""
import struct
import time
from modular.storage import Store, file_hash
from mini_ops.ae350 import candidate
from mini_ops.board import Operation
from mini_ops.ae350_payload import resolve_payload, payload_file, application_parameters
from gateware.machines import ae350_map as amap

RES = amap.RESULTS_BASE
DONE = 0xd0000002


def perform(args):
    op = Operation('ae350-ddr-suite')
    print('Operation logs:', op.path, flush=True)
    cpu = None
    hardware = None
    if not (0 <= args.stress_seconds <= 3600 and 1 <= args.region_kb <= 1024
            and 0 <= args.seed <= 0xffffffff and 0 <= args.flags <= 15
            and 0 <= args.traversal_mb <= 1024 and args.poll_interval > 0
            and 0 < args.timeout <= 3600):
        raise ValueError('Invalid DDR suite duration, region, seed, flags or polling budget')
    try:
        root, ref, build_id = candidate(args.build)
        store = Store()
        manifest = store.get(ref)
        boot = store.get(manifest['dependencies']['bootloader'])
        if boot['config'].get('image_abi') != amap.image_abi():
            raise ValueError('Selected hardware uses a different address ABI')
        payload_ref, payload_root = resolve_payload(store, getattr(args, 'payload', None))
        if store.get(payload_ref)['config'].get('purpose') != 'P2-diagnostics':
            raise ValueError('DDR suite requires the diagnostic payload, not a user application')
        params = dict(stress_seconds=args.stress_seconds, seed=args.seed, region_kb=args.region_kb,
                      flags=args.flags, traversal_mb=args.traversal_mb)
        payload = application_parameters(payload_root, params)
        import ae350_image
        import hashlib
        image = ae350_image.build(payload)
        (op.path / 'image.bin').write_bytes(image)
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, fs_sha256=file_hash(fs), rom_build_id=build_id,
                         payload_artifact=payload_ref,
                         payload_sha256=file_hash(payload_file(payload_root)),
                         payload_bytes=len(payload), flash_written=False,
                         configured_payload_sha256=hashlib.sha256(payload).hexdigest(),
                         qualification='DDR-payload-loaded-over-UART; JTAG-observation-only')
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            op.save()
            return op
        from mini_ops.ae350_session import HardwareSession
        hardware = HardwareSession(op, port=args.port, cpu_jtag_location=args.cpu_jtag_location,
                                   location=args.location)
        from boot_upload import BootSession
        from mini_ops.ae350_upload import wait_loader
        session = BootSession(hardware.open_serial(), hardware.open_log('uart.log'))
        hardware.program(fs)
        op.record('banner', text=wait_loader(session, build_id, min(args.timeout, 30)))
        session.upload(image, load=ae350_image.LOAD, abi=ae350_image.abi_tag())
        op.record('load', entry=hex(amap.DDR_CODE), bytes=len(payload), stack=hex(amap.PAYLOAD_STACK_TOP))
        op.record('request', stress_seconds=args.stress_seconds, region_kb=args.region_kb,
                  seed=args.seed, flags=args.flags, traversal_mb=args.traversal_mb)
        cpu = hardware.open_cpu()
        t0 = time.monotonic()
        deadline = t0 + args.timeout
        seen = []
        done = 0
        paused = 0.0
        # Poll infrequently: every halt pauses the CPU, so a frequent poll would
        # inflate a long stress run's wall time.  The paused total is recorded so
        # the host-measured duration can be corrected.
        interval = args.poll_interval
        while True:
            next_poll = time.monotonic() + interval
            while time.monotonic() < next_poll:
                chunk = hardware.port.read(hardware.port.in_waiting)
                if chunk:
                    session.note(chunk)
                time.sleep(min(.05, max(0, next_poll - time.monotonic())))
            before = time.monotonic()
            cpu.halt()
            # The CPU is paused until resume() returns; count that whole span.
            resume_at = before
            status = int.from_bytes(cpu.read_memory(RES, 4), 'little')
            done = int.from_bytes(cpu.read_memory(RES + 0x208, 4), 'little')
            seen.append({'t': round(time.monotonic() - t0, 2), 'status': hex(status),
                         'done': hex(done),
                         'test': int.from_bytes(cpu.read_memory(RES + 0x104, 4), 'little'),
                         'beat': int.from_bytes(cpu.read_memory(RES + 0x10c, 4), 'little'),
                         'pc': hex(cpu.register(0x7b1))})
            if done == DONE or time.monotonic() >= deadline:
                break
            cpu.resume()
            paused += time.monotonic() - resume_at
        ended = time.monotonic()
        paused += ended - before
        counts = struct.unpack('<2I', cpu.read_memory(RES + 0x200, 8))
        flags = struct.unpack('<32I', cpu.read_memory(RES + 4, 128))
        regs = {name: hex(cpu.register(number)) for name, number in
                [('pc', 0x7b1), ('mcause', 0x342), ('mepc', 0x341), ('mtval', 0x343)]}
        wall = ended - t0
        op.record('results', done=hex(done), checks=counts[0],
                  failures=counts[1], flags=[int(x) for x in flags], registers=regs,
                  poll=seen[:60], host_wall_seconds=round(wall, 1),
                  host_paused_seconds=round(paused, 2),
                  host_running_seconds=round(wall - paused, 1),
                  poll_interval=interval)
        performance = struct.unpack('<10I', cpu.read_memory(RES + 0x180, 40))
        op.record('performance', sample_cycles=[dict(write=performance[i], read=performance[i+1], copy=performance[i+2])
                  for i in (0, 3, 6)], bytes_per_sample=65536,
                  dependent_load_cycles=performance[9], dependent_load_hops=1024,
                  configured_core_hz=800000000, measured_core_hz=None,
                  scope='raw measurements; not functional PASS checks or calibrated bandwidth')
        stress = struct.unpack('<4I', cpu.read_memory(RES + 0x1b0, 16))
        op.record('stress', requested_seconds=args.stress_seconds, iterations=stress[0],
                  errors=stress[1], start_mcycle=stress[2], end_mcycle=stress[3],
                  timing='cycle budget at configured core clock; host wall covers the entire suite')
        if args.flags & 2:
            coverage = struct.unpack('<4I', cpu.read_memory(RES + 0x440, 16))
            op.record('coverage', blocks=coverage[0], covered_bytes=coverage[1],
                      errors=coverage[2], limit=coverage[3], words_per_block=8 if args.flags & 8 else 2,
                      excludes='full runtime image through bss; stack/results in DLM')
        if args.flags & 4:
            observer = struct.unpack('<10I', cpu.read_memory(RES + 0x400, 40))
            op.record('observer', read_counts=list(observer[:4]), write_counts=list(observer[4:7]),
                      errors=observer[7], write_control=hex(observer[8]), read_control=hex(observer[9]),
                      transport='ILM measurement; coherent DDR-to-sys handshake snapshot')
        if done != DONE or counts[0] == 0 or counts[1] != 0 or int(regs['mcause'], 16) != 0:
            raise RuntimeError('DDR payload did not pass; inspect results and uart.log')
        hardware.close()
        hardware = None
        op.report.update(passed=True)
        op.save()
        print('PASS DDR payload checks=%d failures=%d' % counts, flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        op.save()
        raise
    finally:
        if hardware is not None:
            hardware.close()
