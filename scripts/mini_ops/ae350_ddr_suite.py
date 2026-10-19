"""Run the DDR test payload from DDR through the JTAG launcher.

No UART is used: the ROM monitor polls a mailbox in the always-mapped SRAM, the
payload is loaded into DDR by the CPU JTAG abstract-memory path, and every test
result is mirrored into SRAM where the host reads it back.
"""
import struct
import time
from modular.storage import Store, file_hash
from mini_ops.ae350 import candidate
from mini_ops.board import Operation
from mini_ops.ae350_payload import resolve_payload, payload_file
from gateware.machines import ae350_map as amap

MAGIC = 0x5aa5c001
ACK = 0xa55a0001
RES = amap.SRAM_BASE + amap.SRAM_RESULTS
MAILBOX = amap.SRAM_BASE + amap.SRAM_MAILBOX
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
    if args.entry != amap.DDR_CODE or args.stack != amap.DDR_STACK_TOP:
        raise ValueError('Diagnostic payload entry/stack are fixed by its linker contract')
    try:
        root, ref, build_id = candidate(args.build)
        store = Store()
        manifest = store.get(ref)
        boot = store.path(manifest['dependencies']['bootloader'])
        expected_rom = (boot / 'outputs/boot.bin').read_bytes()
        payload_ref, payload_root = resolve_payload(store, getattr(args, 'payload', None))
        if store.get(payload_ref)['config'].get('purpose') != 'P2-diagnostics':
            raise ValueError('DDR suite requires the diagnostic payload, not a user application')
        payload = payload_file(payload_root).read_bytes()
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, fs_sha256=file_hash(fs), rom_build_id=build_id,
                         payload_artifact=payload_ref,
                         payload_sha256=file_hash(payload_file(payload_root)),
                         payload_bytes=len(payload), flash_written=False,
                         qualification='DDR-payload-executed-from-DDR-via-CPU-JTAG')
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            op.save()
            return op
        from mini_ops.ae350_session import HardwareSession
        hardware = HardwareSession(op, cpu_jtag_location=args.cpu_jtag_location,
                                   location=args.location)
        hardware.program(fs)
        time.sleep(0.8)
        cpu = hardware.halt()
        actual_rom = cpu.read_memory(0x80000000, len(expected_rom))
        (op.path / 'rom-readback.bin').write_bytes(actual_rom)
        if actual_rom != expected_rom:
            raise RuntimeError('ROM JTAG readback does not match the selected firmware')
        op.record('rom-match', bytes=len(expected_rom), idcode=hex(cpu.idcode))

        wr = cpu.write_word

        # parameters: seed and region; the payload reads them at CPU 0x2000
        params = [args.stress_seconds, args.seed, args.region_kb, args.flags, args.traversal_mb]
        for index, value in enumerate(params):
            wr(amap.DDR_PARAMS + 4 * index, value & 0xffffffff)
        for offset in range(0, len(payload), 4):
            wr(amap.DDR_CODE + offset, int.from_bytes(payload[offset:offset + 4].ljust(4, b'\0'), 'little'))
        for offset in range(0, 0x100, 4):     # results area + mailbox
            wr(RES + offset, 0)
        wr(MAILBOX, 0)
        wr(MAILBOX + 4, args.entry)
        wr(MAILBOX + 8, args.stack)
        wr(MAILBOX, MAGIC)                     # publish last
        op.record('load', entry=hex(args.entry), bytes=len(payload), stack=hex(args.stack))
        op.record('request', stress_seconds=args.stress_seconds, region_kb=args.region_kb,
                  seed=args.seed, flags=args.flags, traversal_mb=args.traversal_mb)
        cpu.resume()
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
            time.sleep(interval)
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
        ack = int.from_bytes(cpu.read_memory(MAILBOX, 4), 'little')
        regs = {name: hex(cpu.register(number)) for name, number in
                [('pc', 0x7b1), ('mcause', 0x342), ('mepc', 0x341), ('mtval', 0x343)]}
        wall = ended - t0
        op.record('results', ack=hex(ack), done=hex(done), checks=counts[0],
                  failures=counts[1], flags=[int(x) for x in flags], registers=regs,
                  poll=seen[:60], host_wall_seconds=round(wall, 1),
                  host_paused_seconds=round(paused, 2),
                  host_running_seconds=round(wall - paused, 1),
                  poll_interval=interval)
        if ack != ACK or done != DONE or counts[1] != 0:
            raise RuntimeError('DDR payload did not pass; inspect results')
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
                      excludes='parameters, full runtime image through bss, stack')
        if args.flags & 4:
            observer = struct.unpack('<8I', cpu.read_memory(RES + 0x400, 32))
            op.record('observer', read_counts=list(observer[:4]), write_counts=list(observer[4:7]),
                      errors=observer[7], transport='coherent DDR-to-sys handshake snapshot')
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
