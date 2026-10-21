"""Load diagnostics through the normal UART loader; JTAG only observes DLM results."""
import struct
import time
from modular.storage import Store, file_hash
from mini_ops.mega138k_ae350.ae350 import candidate
from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file, application_parameters
from gateware.machines.mega138k_ae350 import address_map as amap

RES = amap.RESULTS_BASE
DONE = 0xd0000002


class StressTimer:
    """Host timestamps of firmware phase markers, independent of mcycle."""
    def __init__(self):
        self.buffer = b''
        self.start = self.end = None

    def observe(self, chunk, now):
        self.buffer = (self.buffer + chunk)[-4096:]
        if self.start is None and b'STRESS BEGIN\r\n' in self.buffer:
            self.start = now
        if self.end is None and b'STRESS END\r\n' in self.buffer:
            self.end = now

    def result(self, pauses):
        if self.start is None or self.end is None:
            return dict(source='UART phase markers', available=False)
        paused = sum(max(0, min(b, self.end) - max(a, self.start)) for a, b in pauses)
        return dict(source='UART phase markers / host monotonic', available=True,
                    wall_seconds=self.end - self.start, paused_seconds=paused,
                    running_seconds=self.end - self.start - paused,
                    timestamp_resolution_seconds=.05,
                    scope='marker receive times; UART latency and polling quantization included')


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
        if store.get(payload_ref)['config'].get('purpose') != 'AE350-memory-diagnostics':
            raise ValueError('DDR suite requires the diagnostic payload, not a user application')
        params = dict(stress_seconds=args.stress_seconds, seed=args.seed, region_kb=args.region_kb,
                      flags=args.flags, traversal_mb=args.traversal_mb)
        payload = application_parameters(payload_root, params)
        import scripts.mini_ops.mega138k_ae350.ae350_image
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
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        hardware = HardwareSession(op, port=args.port, cpu_jtag_location=args.cpu_jtag_location,
                                   location=args.location)
        from tools.boot_session import BootSession
        from mini_ops.mega138k_ae350.ae350_upload import wait_loader
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
        pauses = []
        stress_timer = StressTimer()
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
                    stress_timer.observe(chunk, time.monotonic())
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
            resumed = time.monotonic()
            paused += resumed - resume_at
            pauses.append((resume_at, resumed))
        ended = time.monotonic()
        paused += ended - before
        pauses.append((before, ended))
        chunk = hardware.port.read(hardware.port.in_waiting)
        if chunk:
            session.note(chunk)
            stress_timer.observe(chunk, time.monotonic())
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
        dependent_samples = struct.unpack('<3I', cpu.read_memory(RES + 0x480, 12))
        op.record('performance', sample_cycles=[dict(write=performance[i], read=performance[i+1], copy=performance[i+2])
                  for i in (0, 3, 6)], bytes_per_sample=65536,
                  dependent_load_cycles=performance[9], dependent_load_hops=1024,
                  dependent_load_sample_cycles=list(dependent_samples),
                  cache='I/D disabled', access='32-bit scalar volatile',
                  code_location='DDR', stack_location='DLM',
                  configured_core_hz=800000000, measured_core_hz=None,
                  scope='raw measurements; not functional PASS checks or calibrated bandwidth')
        stress = struct.unpack('<4I', cpu.read_memory(RES + 0x1b0, 16))
        stress_high = struct.unpack('<2I', cpu.read_memory(RES + 0x1a8, 8))
        benchmark = struct.unpack('<57I', cpu.read_memory(RES + 0x4bc, 228))
        if benchmark[0] != 0xbec00001:
            raise RuntimeError('Diagnostic benchmark schema mismatch')
        samples = []
        for index in range(18):
            cycles, reads, writes = benchmark[1 + index * 3:4 + index * 3]
            samples.append(dict(cache='uncached' if index < 9 else 'write-back-cold',
                repeat=(index % 9) // 3, operation=('write', 'read', 'copy')[index % 3],
                cycles=cycles, ui_reads=reads, ui_writes=writes, bytes=65536))
        op.record('bandwidth', samples=samples, integrity_errors=benchmark[56],
                  code_location='ILM', stack_location='DLM', access='32-bit scalar volatile',
                  cold_before_each_sample=True, timed_writeback=True,
                  counter_width=16, schema=hex(benchmark[0]), checksum=benchmark[55])
        if benchmark[56]:
            raise RuntimeError('Bandwidth copy integrity failed')
        op.record('stress', requested_seconds=args.stress_seconds, iterations=stress[0],
                  errors=stress[1], start_mcycle=stress[2], end_mcycle=stress[3],
                  start_mcycle64=(stress_high[0] << 32) | stress[2],
                  end_mcycle64=(stress_high[1] << 32) | stress[3],
                  host_phase=stress_timer.result(pauses),
                  timing='configured-clock cycle budget; actual stress duration is host_phase, not suite wall')
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
