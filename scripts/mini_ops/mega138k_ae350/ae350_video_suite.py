"""SRAM-load an explicit HDMI candidate and verify its SVO test card via V4L2."""
import hashlib
import json
import os
import re
from pathlib import Path
import selectors
import shutil
import subprocess
import time

from mini_ops.common import Operation
from mini_ops.mega138k_ae350.ae350 import candidate
from modular.storage import file_hash

WIDTH, HEIGHT = 320, 240
# Centers inside the six solid rectangles, in original 640x480 coordinates.
PANELS = (
    ('red', 176, 112, (1, 0, 0)), ('green', 176, 240, (0, 1, 0)),
    ('blue', 176, 368, (0, 0, 1)), ('cyan', 432, 112, (0, 1, 1)),
    ('magenta', 432, 240, (1, 0, 1)), ('yellow', 432, 368, (1, 1, 0)),
)


def inspect_frame(frame):
    """Judge channel order and orientation without requiring exact capture gain."""
    if len(frame) != WIDTH * HEIGHT * 3:
        raise ValueError('incomplete RGB frame')
    colors, failures = {}, []
    for name, x, y, active in PANELS:
        x, y = x // 2, y // 2
        rgb = [round(sum(frame[(yy * WIDTH + xx) * 3 + channel]
                         for yy in range(y - 1, y + 2)
                         for xx in range(x - 1, x + 2)) / 9, 2)
               for channel in range(3)]
        colors[name] = rgb
        hot = min(rgb[i] for i in range(3) if active[i])
        cold = max(rgb[i] for i in range(3) if not active[i])
        if hot < 15 or cold > max(8, hot * 0.55):
            failures.append(name)
    return colors, failures


class ConsolePattern:
    """Compare rendered text masks with one-pixel tolerance for MJPEG/scaling."""
    def __init__(self, rgb565):
        if len(rgb565) != 640 * 480 * 2:
            raise ValueError('expected console frame must be raw 640x480 RGB565 little endian')
        self.expected = set()
        for y in range(HEIGHT):
            for x in range(WIDTH):
                sums = [0, 0, 0]
                for yy in (y * 2, y * 2 + 1):
                    for xx in (x * 2, x * 2 + 1):
                        off = (yy * 640 + xx) * 2
                        value = rgb565[off] | (rgb565[off + 1] << 8)
                        sums[0] += ((value >> 11) & 31) * 255 // 31
                        sums[1] += ((value >> 5) & 63) * 255 // 63
                        sums[2] += (value & 31) * 255 // 31
                if max(sums) > 64 * 4:
                    self.expected.add(y * WIDTH + x)
        if len(self.expected) < 100:
            raise ValueError('expected console frame has too few foreground pixels')
        self.expanded = self.expand(self.expected)
        on = sorted(self.expected)
        off = sorted(set(range(WIDTH * HEIGHT)) - self.expanded)
        self.on_samples = on[::max(1, len(on) // 128)]
        self.off_samples = off[::max(1, len(off) // 128)]

    @staticmethod
    def expand(points):
        result = set()
        for point in points:
            y, x = divmod(point, WIDTH)
            for yy in range(max(0, y - 1), min(HEIGHT, y + 2)):
                for xx in range(max(0, x - 1), min(WIDTH, x + 2)):
                    result.add(yy * WIDTH + xx)
        return result

    def inspect(self, frame, full=False):
        if full:
            observed = {i for i in range(WIDTH * HEIGHT) if max(frame[i * 3:i * 3 + 3]) > 64}
            precision = len(observed & self.expanded) / max(1, len(observed))
            recall = len(self.expected & self.expand(observed)) / len(self.expected)
            metrics = dict(precision=round(precision, 5), recall=round(recall, 5))
            return metrics, [] if precision >= 0.90 and recall >= 0.90 else ['console-mask']
        on = sum(any(max(frame[p * 3:p * 3 + 3]) > 32 for p in self.expand({i}))
                 for i in self.on_samples) / len(self.on_samples)
        off = sum(max(frame[i * 3:i * 3 + 3]) <= 64 for i in self.off_samples) / max(1, len(self.off_samples))
        return dict(foreground=round(on, 5), background=round(off, 5)), [] if on >= 0.9 and off >= 0.95 else ['console-samples']


def _ppm(path, frame):
    path.write_bytes(('P6\n%d %d\n255\n' % (WIDTH, HEIGHT)).encode() + frame)


def timestamp_stats(log, fps, warmup=5):
    base=re.search(r'config in time_base:\s*(\d+)/(\d+)',log)
    scale=(int(base[1])/int(base[2])) if base and int(base[2]) else None
    times=[]
    for line in log.splitlines():
        if 'showinfo' not in line:
            continue
        match=re.search(r'\bn:\s*\d+\s+pts:\s*(-?\d+).*?\bpts_time:\s*([-+\d.eE]+)',line)
        if match:
            try:times.append(int(match[1])*scale if scale is not None else float(match[2]))
            except ValueError:pass
    if not times:return dict(available=False)
    times=[value for value in times if value-times[0]>=warmup]
    if len(times)<2:return dict(available=False)
    gaps=[b-a for a,b in zip(times,times[1:])]
    return dict(available=True,frames=len(times),window_seconds=round(times[-1]-times[0],6),
                max_gap_ms=round(max(gaps)*1000,3),nonmonotonic=sum(gap<=0 for gap in gaps),
                estimated_missing_frames=sum(max(0,round(gap*fps)-1) for gap in gaps if gap>1.5/fps),
                timestamp_basis='integer PTS/time_base' if scale is not None else 'printed pts_time',
                method='delivered USB frame PTS gaps after warmup; not HDMI input frame enumeration')


def capture(op, device, seconds, ffmpeg, console=None, stop_event=None, input_format='yuyv422', fps=30):
    """Inspect every delivered USB frame; save samples, counters and decoder log."""
    warmup = 5
    if input_format not in ('yuyv422', 'mjpeg') or fps not in (30, 60):
        raise ValueError('capture requires yuyv422/mjpeg and 30/60 fps')
    op.report['usb_capture_mode'] = dict(width=640, height=480, fps=fps, format=input_format)
    command = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'info',
               '-f', 'v4l2', '-input_format', input_format, '-video_size', '640x480',
               '-framerate', str(fps), '-i', device, '-t', str(seconds + warmup),
               '-vf', 'showinfo,scale=320:240:flags=area', '-pix_fmt', 'rgb24',
               '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1']
    op.record('capture-command', command=command)
    start = last_data = time.monotonic()
    frames = bad = total = 0
    first_good = last_good = None
    samples = []
    size = WIDTH * HEIGHT * 3
    pending = bytearray()
    proc = None
    try:
        with (op.path / 'ffmpeg.log').open('wb') as log, selectors.DefaultSelector() as selector:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError('video capture cancelled')
                now = time.monotonic()
                if now - start > seconds + warmup + 30:
                    raise TimeoutError('video capture exceeded its wall-clock deadline')
                if not selector.select(timeout=1):
                    if now - last_data > 10:
                        raise TimeoutError('no video bytes for 10 seconds')
                    continue
                chunk = os.read(proc.stdout.fileno(), 1 << 20)
                if not chunk:
                    break
                last_data = time.monotonic()
                pending.extend(chunk)
                while len(pending) >= size:
                    frame = bytes(pending[:size])
                    del pending[:size]
                    total += 1
                    now = time.monotonic()
                    if now - start < warmup:
                        continue
                    frames += 1
                    colors, errors = (console.inspect(frame, full=frames == 1 or frames % (fps // 2) == 0)
                                      if console else inspect_frame(frame))
                    sample = dict(frame=frames, elapsed=round(now - start, 3), colors=colors,
                                  errors=errors, sha256=hashlib.sha256(frame).hexdigest())
                    if frames == 1:
                        _ppm(op.path / 'first.ppm', frame)
                    if errors:
                        bad += 1
                        if bad == 1:
                            _ppm(op.path / 'first-bad.ppm', frame)
                            samples.append(sample)
                    else:
                        if first_good is None:
                            first_good = now
                        last_good = now
                    if frames == 1 or frames % 300 == 0:
                        samples.append(sample)
                    if frames % (fps * 30) == 0:
                        op.report.update(captured_frames=frames, pattern_errors=bad,
                                         elapsed_seconds=round(now - start, 2))
                        op.save()
                        print('HDMI capture: %d frames, %d pattern errors, %.1fs' %
                              (frames, bad, now - start), flush=True)
                    if bad >= 30:
                        raise RuntimeError('30 captured frames failed the test-card pattern')
            code = proc.wait(timeout=10)
            if code:
                raise RuntimeError('ffmpeg failed with exit code %d; inspect ffmpeg.log' % code)
            if pending:
                raise RuntimeError('truncated last RGB frame')
            observed = 0 if first_good is None else last_good - first_good
            if observed < max(0, seconds - 1) or frames < seconds * fps * 0.9:
                raise RuntimeError('insufficient capture duration/frame count: %.3fs/%d' % (observed, frames))
            if bad:
                raise RuntimeError('%d delivered USB frames failed the test-card pattern' % bad)
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            proc.stdout.close()
        (op.path / 'frame-samples.json').write_text(json.dumps(samples, indent=2) + '\n')
        op.report.update(captured_frames=frames, total_usb_frames=total, pattern_errors=bad,
                         elapsed_seconds=round(time.monotonic() - start, 3))
        if (op.path/'ffmpeg.log').is_file():
            op.report['usb_timestamps']=timestamp_stats((op.path/'ffmpeg.log').read_text(errors='replace'),fps,warmup)
        for name in ('first', 'first-bad'):
            source = op.path / (name + '.ppm')
            if source.is_file():
                subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                                '-i', str(source), '-frames:v', '1', str(op.path / (name + '.png'))],
                               check=True, timeout=15)


def perform(args):
    op = Operation('ae350-video-suite')
    hardware = None
    print('Operation logs:', op.path, flush=True)
    try:
        if not 1 <= args.soak_seconds <= 3600:
            raise ValueError('--soak-seconds must be in 1..3600')
        root, ref, build_id = candidate(args.build)
        payload = getattr(args, 'payload', None)
        console_mode = getattr(args, 'pattern', 'panels') == 'console'
        expected = getattr(args, 'expected_frame', None)
        if console_mode and expected is None:
            raise ValueError('console comparison requires --expected-frame')
        if not console_mode and expected is not None:
            raise ValueError('--expected-frame requires --pattern console')
        if payload and not getattr(args, 'cpu_jtag_location', None):
            raise ValueError('payload verification requires --cpu-jtag-location')
        console = None
        if console_mode:
            raw = Path(expected).read_bytes()
            console = ConsolePattern(raw)
            (op.path / 'expected.rgb565').write_bytes(raw)
            op.report['expected_frame_sha256'] = hashlib.sha256(raw).hexdigest()
        image = None
        if payload:
            from modular.storage import Store
            from mini_ops.mega138k_ae350.ae350_payload import resolve_payload, payload_file
            from mini_ops.mega138k_ae350 import ae350_image
            store = Store()
            boot = store.get(store.get(ref)['dependencies']['bootloader'])
            from gateware.machines.mega138k_ae350 import address_map as amap
            abi = boot['config']['image_abi']
            payload_ref, payload_root = resolve_payload(store, payload, expected_abi=abi)
            if store.get(payload_ref)['config']['image_abi'] != boot['config']['image_abi']:
                raise ValueError('payload ABI differs from the selected gateware bootloader')
            payload_data = payload_file(payload_root).read_bytes()
            image = ae350_image.build(payload_data, abi)
            op.report.update(payload_artifact=payload_ref, payload_sha256=file_hash(payload_file(payload_root)))
        expected_source = root / 'inputs/source/gateware/devices/video/hdmi/svo_tcard.v'
        if not expected_source.is_file():
            expected_source = root / 'inputs/source/gateware/machines/mega138k_ae350/peripherals/video.py'
        if not expected_source.is_file():
            raise ValueError('selected gateware has no archived HDMI test-card source')
        fs = root / 'gateware/impl/pnr/project.fs'
        op.report.update(artifact=ref, fs_sha256=file_hash(fs), rom_build_id=build_id,
                         flash_written=False, capture_device=args.capture_device,
                         expected_pattern='BIOS console mask' if console_mode else '640x480 six RGB/CMY rectangles',
                         pattern_source_sha256=file_hash(expected_source),
                         usb_capture_mode=dict(width=640, height=480,
                             fps=getattr(args, 'capture_fps', 30),
                             format=getattr(args, 'capture_format', 'yuyv422')),
                         hdmi_input_timing='not independently exposed by this UVC capture path',
                         scope='every delivered USB frame: RGB order/orientation and six panel colors; '
                               'static pattern cannot detect repeated frames; edges require image inspection')
        if console_mode:
            op.report['scope'] = ('every USB frame: foreground/background samples; '
                                  'text-mask precision/recall with one-pixel scaling tolerance twice per second')
        if args.dry_run:
            op.report.update(passed=True, dry_run=True)
            return op
        ffmpeg, v4l = shutil.which('ffmpeg'), shutil.which('v4l2-ctl')
        if not ffmpeg or not v4l:
            raise ValueError('ffmpeg and v4l2-ctl required in PATH')
        info = subprocess.run([v4l, '-d', args.capture_device, '--all', '--list-formats-ext'],
                              text=True, capture_output=True, check=True, timeout=15)
        (op.path / 'capture-device.txt').write_text(info.stdout + info.stderr)
        from mini_ops.mega138k_ae350.ae350_session import HardwareSession
        from mini_ops.mega138k_ae350.ae350_upload import wait_loader
        from tools.boot_session import BootSession
        hardware = HardwareSession(op, port=args.port, location=args.location,
                                   cpu_jtag_location=getattr(args, 'cpu_jtag_location', None))
        session = BootSession(hardware.open_serial(), hardware.open_log('uart.log'))
        hardware.program(fs)
        op.record('banner', text=wait_loader(session, build_id, 30))
        if image is not None:
            session.upload(image, load=ae350_image.LOAD, abi=abi)
        capture(op, args.capture_device, args.soak_seconds, ffmpeg, console=console,
                input_format=getattr(args, 'capture_format', 'yuyv422'),
                fps=getattr(args, 'capture_fps', 30))
        if image is not None:
            from gateware.machines.mega138k_ae350 import address_map as amap
            cpu = hardware.halt()
            raw = cpu.read_memory(amap.RESULTS_BASE, 128)
            words = [int.from_bytes(raw[i:i + 4], 'little') for i in range(0, len(raw), 4)]
            op.record('payload-results', words=words)
            trap = cpu.read_memory(amap.RESULTS_BASE + amap.TRAP_RECORD * 4, amap.TRAP_RECORD_WORDS * 4)
            pc = cpu.register(0x7b1)
            mcause = cpu.register(0x342)
            op.record('payload-execution', pc=hex(pc), mcause=hex(mcause), trap=trap.hex())
            if any(trap) or mcause or not amap.DDR_CODE <= pc < amap.DDR_CODE + len(payload_data):
                raise RuntimeError('video payload trap/PC execution check failed')
            raw_csr=cpu.read_memory(amap.VIDEO_BASE,0x7c)
            csr_words=[int.from_bytes(raw_csr[i:i+4],'little') for i in range(0,len(raw_csr),4)]
            csr={name:csr_words[off//4] for name,off in amap.VIDEO_REGS.items()}
            op.record('video-csr',values=csr,id=hex(csr_words[0]),abi=hex(csr_words[1]))
            if csr_words[:3]!=[amap.VIDEO_ID,amap.ABI_VERSION,amap.VIDEO_CAP] or not (csr['STATUS']&1):
                raise RuntimeError('video identity/active status check failed')
            if any(csr[name] for name in ('UNDERFLOWS','ADDRESS_ERROR','TIMEOUT_ERROR','MISSING','ERRORS','REQUEST_OVERFLOW')):
                raise RuntimeError('video hardware error counters were nonzero')
            if csr['READS']<args.soak_seconds*58*19200:
                raise RuntimeError('insufficient continuous video DDR reads')
            if words[0] != (0xc6101001 if console_mode else 0xc6100001) or words[1]:
                raise RuntimeError('video payload did not pass: stage=%08x error=%08x' % (words[0], words[1]))
            if not console_mode:
                if words[31] != 0x56490001 or words[2:5] != [100, 100, 3] or not words[5] or words[6:8] != [1, 1]:
                    raise RuntimeError('video suite schema/flip/restart/negative/CPU coverage missing')
                if any(words[i] for i in (9, 10, 11, 12, 13, 18)) or not words[16] or not words[17]:
                    raise RuntimeError('video scanout counters failed')
        op.report.update(passed=True, phase='captured-console' if console_mode else 'captured-test-card')
        print('PASS HDMI console capture' if console_mode else 'PASS HDMI test card capture', flush=True)
        return op
    except Exception as error:
        op.report.update(passed=False, error=str(error))
        raise
    finally:
        if hardware is not None:
            hardware.close()
        op.save()
