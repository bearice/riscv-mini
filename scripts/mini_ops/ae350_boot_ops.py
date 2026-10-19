"""AE350 memory run/recover/verify: one machine-selected implementation.

The public entry points are the ordinary ``board run``/``recover``/``verify``
commands; JTAG and the serial port are internal transports and the diagnostics
are a *suite*, so no stage-specific top-level operation is needed for normal use.
"""
import argparse
import math
import re
from dataclasses import dataclass

# Named, validated request: nothing touches hardware until this passes.
SUITES = ('firmware', 'boot', 'loader')


@dataclass(frozen=True)
class RunRequest:
    """Operation intent, validated before any hardware session is acquired."""
    operation: str
    build: str
    port: str | None = None
    cpu_jtag_location: int | None = None
    payload: str | None = None
    location: int | None = None
    repeat: int | None = None
    self_test: bool = False
    timeout: float | None = None
    suite: str | None = None
    dry_run: bool = False

    def __post_init__(self):
        # The shared board CLI uses strings (including "auto") for locations.
        # Normalize once at the request boundary, not inside transport code.
        if self.location is not None:
            value = None if str(self.location).lower() == 'auto' else int(self.location)
            object.__setattr__(self, 'location', value)

    def validate(self):
        problems = []
        if self.operation not in ('run', 'recover', 'verify'):
            problems.append('unknown operation %r' % (self.operation,))
        if not self.build:
            problems.append('--build is required')
        if self.repeat is not None and self.repeat < 1:
            problems.append('--repeat must be at least 1')
        if self.timeout is not None and (not math.isfinite(self.timeout) or self.timeout <= 0):
            problems.append('--timeout must be positive')
        if self.suite is not None and self.suite not in SUITES:
            problems.append('unsupported suite %r (choose from %s)' % (self.suite, ', '.join(SUITES)))
        needs_cpu = (self.operation in ('recover', 'verify')) or bool(self.self_test) or (self.repeat or 1) > 1
        if needs_cpu and not self.cpu_jtag_location:
            problems.append('this request needs --cpu-jtag-location')
        if self.port is not None and not re.fullmatch(r'COM[1-9][0-9]*', str(self.port).upper()):
            problems.append('--port must name a COM port')
        for name in ('location', 'cpu_jtag_location'):
            value = getattr(self, name)
            if value is not None and value <= 0:
                problems.append('--' + name.replace('_', '-') + ' must be positive')
        if problems:
            raise ValueError('invalid board request: ' + '; '.join(problems))
        return self


def perform(args, root, ref, build_id):
    """Dispatch a boot board operation; ``root`` is the gateware artifact dir."""
    if getattr(args, 'image', None) or getattr(args, 'mic', False) or getattr(args, 'soak_seconds', 0):
        raise ValueError('AE350 memory uses --payload artifact selection; legacy image/mic/soak overrides are unsupported')
    request = RunRequest(
        operation=args.operation, build=args.build, port=args.port,
        cpu_jtag_location=getattr(args, 'cpu_jtag_location', None),
        payload=getattr(args, 'payload', None), repeat=getattr(args, 'repeat', None),
        location=getattr(args, 'location', None),
        self_test=getattr(args, 'self_test', False), timeout=getattr(args, 'timeout', None),
        suite=getattr(args, 'suite', None), dry_run=getattr(args, 'dry_run', False)).validate()
    if request.suite and request.suite not in ('firmware', 'boot', 'loader'):
        raise ValueError('unsupported suite')
    if request.operation == 'recover':
        return _recover(request, root, ref, build_id)
    if request.operation == 'verify' and request.suite == 'loader':
        return _loader_suite(request, root, ref, build_id)
    if request.operation == 'verify' and request.suite == 'boot':
        return _loader_suite(request, root, ref, build_id, full=False)
    # run and verify/firmware both upload and require execution evidence.
    return _upload(request, root, ref, build_id,
                   self_test=(request.self_test or (request.operation == 'verify' and request.suite == 'firmware')),
                   repeat=request.repeat or 1)


def _engine_args(request, self_test=False, repeat=1):
    ns = argparse.Namespace(
        build=request.build,
        payload=request.payload, port=request.port or 'COM9',
        cpu_jtag_location=request.cpu_jtag_location, location=request.location,
        timeout=request.timeout or 30, repeat=repeat, self_test=self_test,
        dry_run=request.dry_run, operation=request.operation, suite=request.suite)
    return ns


def _upload(request, root, ref, build_id, self_test, repeat):
    from mini_ops.ae350_upload import perform_upload
    return perform_upload(_engine_args(request, self_test, repeat), selected=(root, ref, build_id))


def _loader_suite(request, root, ref, build_id, full=True):
    from mini_ops.ae350_upload import perform_upload
    return perform_upload(_engine_args(request, self_test=full or request.self_test, repeat=1), selected=(root, ref, build_id))


def _recover(request, root, ref, build_id):
    """Reload the ROM and confirm it comes up, without uploading anything."""
    from mini_ops.ae350_session import HardwareSession
    from mini_ops.board import Operation
    from boot_upload import BootSession
    from modular.storage import Store
    import time

    op = Operation('ae350-recover')
    session = HardwareSession(op, request.port or 'COM9', request.cpu_jtag_location,
                              location=request.location, dry_run=request.dry_run)
    try:
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, rom_build_id=build_id, flash_written=False)
        if request.dry_run:
            op.report.update(passed=True, dry_run=True); op.save(); return op
        session.open_serial()
        session.program(fs)
        uart = BootSession(session.port, session.open_log('uart.log'))
        store = Store()
        boot = store.get(ref)['dependencies']['bootloader']
        session.restart_rom(expected_rom=(store.path(boot) / 'outputs/boot.bin').read_bytes())
        time.sleep(0.05)
        text = uart.fresh_banner(request.timeout or 30,
                                 pattern=__import__('mini_ops.ae350_upload', fromlist=['BANNER']).BANNER)
        op.record('banner', text=text.decode('ascii', 'replace'))
        ok = ('ROM ' + build_id) in text.decode('ascii', 'replace')
        op.report.update(passed=ok, phase='rom-recovered' if ok else 'identity-mismatch')
        op.save()
        if not ok:
            raise RuntimeError('recovered ROM identity mismatch')
        session.close()
        session = None
        print('PASS AE350 ROM recovered', flush=True)
        return op
    finally:
        if session is not None:
            session.close()
