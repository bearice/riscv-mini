"""Drive the DOOM payload's UART debug console: pause, dump a frame, read timings.

The payload listens for single-character commands on the UART it already owns
(`firmware/apps/doom/dg_debug.c`); this tool sends one and collects the answer.
A frame dump comes back as base64 between a `DBGDUMP` header and a `DBGEND`
CRC32 trailer, and is written both as raw bytes and as a PNG, so a frame taken
off the panel can be compared pixel by pixel against the QEMU reference from
`scripts/doom_sim.py`.

Opening the port does not reset the board (same as `mini.py board status`), which
is the point: the game keeps running while you inspect it.  A dump holds the
payload's game clock, so a 15 s transfer is not a 15 s warp.

    python scripts/doom_debug.py --command timing
    python scripts/doom_debug.py --command frame          # pause, dump, PNG
    python scripts/doom_debug.py --command frame --no-pause --resume
    python scripts/doom_debug.py --command checker4       # hold a known image
    python scripts/doom_debug.py --command release

Nothing can talk to the console and to the BIOS prompt at the same time, so
`--boot` does both in one session: it asks for the BIOS prompt, serves the
payload over TFTP, boots it, waits for the marker and only then sends the
command.  `--settle` gives the game time to reach a screen worth looking at.

    python scripts/doom_debug.py --boot --host-ip 169.254.25.153 --settle 25 \\
        --command frame

`--compare` analyses dumps already on disk without touching the board: it
converts the 8-bit source with the palette the board itself reported and counts
the bytes that disagree with the panel, which is how a band or block artefact is
attributed to the write path instead of to the data.

See docs/doom.md for the command list and what each dump is for.
"""
import argparse
import base64
import binascii
import concurrent.futures
import re
import struct
import sys
import time
import zlib
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from bios_image import unpack            # noqa: E402
from bios_tftp import PayloadServer      # noqa: E402
from doom_sim import raw_to_png          # noqa: E402

# Named commands; a single character is sent verbatim, so new payload commands
# need no change here.
COMMANDS = {
    'help': '?', 'pause': 'p', 'resume': 'p', 'step': 's',
    'frame': 'f', 'slot': 'F', 'source': 'g',
    'timing': 't', 'io': 'i', 'video': 'v', 'state': 'm', 'exit': 'x', 'release': '0',
    'bars': '1', 'checker1': '2', 'checker4': '3', 'hbars': '4', 'ramp': '5',
    'boot-check': '',  # marker followed by a returning app and BIOS prompt
}
DUMPS = ('frame', 'slot', 'source')
HEADER = re.compile(rb'^DBGDUMP (\S+) (\d+) (\d+) (\d+) (\d+) (\d+)\s*$')


def wad_palette(wad, index=0):
    """PLAYPAL palette `index` straight out of the WAD: an independent source of
    truth for the 8-bit dump, so a mismatch against the board's own RGB565
    output points at the blit rather than at the data."""
    data = Path(wad).read_bytes()
    # WAD header: magic(4) numlumps(4) infotablepos(4); each directory entry is
    # 16 bytes: pos(4) size(4) name(8).
    count, offset = struct.unpack('<2i', data[4:12])
    for i in range(count):
        base = offset + i * 16
        pos, size, name = struct.unpack('<ii8s', data[base:base + 16])
        if name.rstrip(b'\0').upper() == b'PLAYPAL':
            start = pos + index * 768
            if size < 768 or len(data) < start + 768:
                raise SystemExit(f'{wad}: PLAYPAL too small ({size} bytes)')
            return data[start:start + 768]
    raise SystemExit(f'PLAYPAL not found in {wad}')


def index_png(raw, png, palette, width, height):
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += raw[y * width:(y + 1) * width]

    def chunk(tag, payload):
        return (struct.pack('>I', len(payload)) + tag + payload
                + struct.pack('>I', zlib.crc32(tag + payload) & 0xffffffff))
    png.write_bytes(b'\x89PNG\r\n\x1a\n'
                    + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 3, 0, 0, 0))
                    + chunk(b'PLTE', palette)
                    + chunk(b'IDAT', zlib.compress(bytes(rows), 6))
                    + chunk(b'IEND', b''))


def read_until(port, pattern, timeout, echo):
    """Return everything received up to and including `pattern`."""
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = port.read(512)
        if chunk:
            data += chunk
            echo(chunk)
            if re.search(pattern, data, re.M):
                return data
    raise TimeoutError(f'no {pattern!r} within {timeout}s; got {bytes(data[-200:])!r}')


def parse_dumps(text):
    """Every `DBGDUMP … / base64 / DBGEND` block in a transcript, as
    (kind, width, height, bpp, raw).  The CRC32 in the trailer covers the raw
    bytes, so a frame that moved during the transfer is caught here rather than
    quietly analysed as if it were a real image."""
    dumps = []
    header = None
    body = bytearray()
    # DOOM writes some of its startup messages without a trailing newline, so a
    # header can end up glued to the tail of a log line.  Re-anchor the markers
    # at line starts; a marker inside base64 body text would only mis-CRC, which
    # is a loud failure, not a silently wrong image.
    text = re.sub(b'(?m)(?<!^)(?=(?:DBGDUMP|DBGEND))', b'\n', text)
    for line in text.splitlines():
        if line.startswith(b'DBGDUMP'):
            match = HEADER.match(line)
            if not match:
                raise SystemExit(f'unparsable dump header: {line!r}')
            header, body = match, bytearray()
            continue
        if line.startswith(b'DBGEND'):
            if header is None:
                raise SystemExit('DBGEND without DBGDUMP')
            kind, size, width, height, bpp, stride = (
                header.group(1).decode(), *(int(g) for g in header.groups()[1:]))
            raw = base64.b64decode(body)
            want = int(line.split()[1], 16)
            got = zlib.crc32(raw)
            print(f'{kind} {width}x{height} {bpp}bpp stride={stride} '
                  f'bytes={len(raw)}/{size} crc={"%08x" % got} board={"%08x" % want}')
            if got != want or len(raw) != size:
                raise SystemExit('dump failed its CRC: the frame moved while it was '
                                 'being read, or the transfer was corrupted')
            dumps.append((kind, width, height, bpp, raw))
            header = None
            continue
        if header is not None:
            body += line
    if header is not None:
        raise SystemExit('dump never finished (no DBGEND)')
    if not dumps:
        raise SystemExit('no dump in the transcript')
    return dumps


def rgb_palette(raw):
    """`struct color` is three 8-bit bitfields plus padding, so in memory each
    entry is b, g, r, a.  Return the 768-byte RGB triple form."""
    return bytes(b for i in range(256) for b in (raw[4 * i + 2], raw[4 * i + 1], raw[4 * i]))


def blit_diff(rgb, idx, palette, width, height):
    """Compare what the panel holds with the 8-bit source converted through
    PLAYPAL using i_video.c's own formula.  The two dumps come from different
    places (framebuffer slot vs I_VideoBuffer), so a clean source with a dirty
    slot means the blit or the DMA, while dirty source means the data or the
    engine.  Rows are reported as structure because the failures we are chasing
    are bands and blocks, not single pixels."""
    lut = bytearray(512)
    for i in range(256):
        r, g, b = palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]
        p = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        lut[i * 2], lut[i * 2 + 1] = p & 0xFF, p >> 8   # little-endian RGB565
    bad_rows, bad_bytes = [], 0
    for y in range(height):
        src = idx[y * width:(y + 1) * width]
        row = rgb[y * width * 2:(y + 1) * width * 2]
        want = b''.join(bytes(lut[2 * c:2 * c + 2]) for c in src)
        n = sum(a != b for a, b in zip(want, row))
        if n:
            bad_rows.append((y, n))
            bad_bytes += n
    total = width * height * 2
    print(f'blit diff: {width}x{height} {len(bad_rows)}/{height} rows differ, '
          f'{bad_bytes}/{total} bytes ({100.0 * bad_bytes / total:.2f}%)')
    if bad_rows:
        first, last = bad_rows[0][0], bad_rows[-1][0]
        run = best = 1
        for a, b in zip(bad_rows, bad_rows[1:]):
            run = run + 1 if b[0] == a[0] + 1 else 1
            best = max(best, run)
        print(f'  rows {first}..{last}, longest run of bad rows {best}')
        worst = sorted(bad_rows, key=lambda r: -r[1])[:5]
        print('  worst rows (row: bytes of ' + str(width * 2) + '): '
              + ', '.join(f'{y}:{n}' for y, n in worst))
        groups = 40
        per = max(1, (height + groups - 1) // groups)
        bar = ''
        for g in range(0, height, per):
            rows = [n for y, n in bad_rows if g <= y < g + per]
            frac = sum(rows) / (len(rows) and per * width * 2 or 1)
            bar += '#' if frac > 0.9 else ('+' if rows else '.')
        print(f'  row profile (each char = {per} rows, # mostly bad, + some, . clean):')
        for i in range(0, len(bar), 40):
            print('  ' + bar[i:i + 40])
    return bad_bytes


def board_palette(out_dir, wad):
    """The palette the board itself reported if a `source` dump carried one,
    otherwise PLAYPAL from the WAD (which misses the gamma step)."""
    path = Path(out_dir) / 'palette.raw'
    if path.exists():
        raw = path.read_bytes()
        if len(raw) >= 1024:
            print('palette: board colors[] (' + str(path) + ')')
            return rgb_palette(raw)
    print('palette: PLAYPAL from ' + str(wad) + ' (no board palette dump; gamma step not modelled)')
    return wad_palette(wad)


def compare(out_dir, wad):
    """Compare the native scan buffer with the source dump already on disk."""
    native = sorted(Path(out_dir).glob('scanindex8-*.raw'), key=lambda p: p.stat().st_mtime)
    if native:
        source = sorted(Path(out_dir).glob('index8-*.raw'), key=lambda p: p.stat().st_mtime)
        if not source:
            print(f'no index8 source dump in {out_dir}; collect it with --command source')
            return 1
        w, h = (int(v) for v in source[-1].stem.rsplit('-', 1)[1].split('x'))
        scanned, rendered = native[-1].read_bytes(), source[-1].read_bytes()
        if len(scanned) != w * h or len(rendered) != w * h:
            raise SystemExit('stale INDEX8 dump sizes')
        differences = sum(a != b for a, b in zip(scanned, rendered))
        print(f'INDEX8 source/native differences: {differences}/{w * h}')
        return int(differences != 0)
    found = {}
    for kind in ('rgb565', 'index8'):
        hits = sorted(Path(out_dir).glob(f'{kind}-*.raw'), key=lambda p: p.stat().st_mtime)
        if not hits:
            print(f'no {kind} dump in {out_dir}; collect it with --command '
                  f'{"frame" if kind == "rgb565" else "source"}')
            return 1
        found[kind] = hits[-1]
    print('comparing', found['rgb565'].name, 'against', found['index8'].name)
    # <kind>-<w>x<h>.raw; rsplit on '-' because the kind itself contains an 'x'
    w, h = (int(v) for v in found['index8'].stem.rsplit('-', 1)[1].split('x'))
    rgb = found['rgb565'].read_bytes()
    idx = found['index8'].read_bytes()
    if len(rgb) != w * h * 2 or len(idx) != w * h:
        raise SystemExit(f'stale dump sizes: rgb565={len(rgb)} index8={len(idx)} for {w}x{h}')
    blit_diff(rgb, idx, board_palette(out_dir, wad), w, h)
    return 0


def paused_state(port, echo):
    """Ask the payload whether it is paused.  'p' is a toggle, so a script that
    says "pause" twice would resume the game it meant to capture."""
    port.write(b'v')
    port.flush()
    text = drain(port, 2.0, echo)
    match = re.search(rb'paused=(\d+)', text)
    if not match:
        raise SystemExit('no answer from the debug console (is the DOOM payload running?)')
    return match.group(1) != b'0'


def wait_prompt(port, echo, tries=3):
    """Poke the idle console until it echoes its prompt.

    A payload that just exited leaves the console idle without reprinting the
    prompt, so a single newline is not enough to prove the board is reachable."""
    port.reset_input_buffer()
    seen = b''
    for _ in range(tries):
        port.write(b'\r')
        port.flush()
        try:
            return read_until(port, rb'> ', 5, echo)
        except TimeoutError as exc:
            seen += str(exc).encode()
    raise SystemExit('board is not at the BIOS setup prompt (no "> "): recover first with\n'
                     '  scripts/mini.py board recover --build current\n'
                     f'last output: {seen[-200:]!r}')


def prompt_or_exit(port, echo):
    """Return the prompt, asking a running payload to hand the console back first.

    Only one program owns the UART at a time and a payload keeps it until it
    returns, so booting a new image means ending the old one.  'x' is the debug
    console's exit; a payload without that console simply ignores it and the
    caller gets the original "not at the prompt" advice."""
    port.reset_input_buffer()
    port.write(b'\r')
    port.flush()
    try:
        return read_until(port, rb'> ', 4, echo)
    except TimeoutError:
        pass
    port.write(b'x')
    port.flush()
    try:
        read_until(port, rb'> ', 15, echo)
    except TimeoutError:
        pass
    return wait_prompt(port, echo)


def bios_command(port, echo, line, wait=20.0):
    """Run one line at the BIOS prompt and return what it printed.

    A running payload owns the UART, so this exits a payload first if it finds
    one.  `wait` has to cover slow commands: `fetchraw` of a 4 MiB WAD takes
    about 17 s at the stop-and-wait TFTP rate and prints nothing until it is
    done, so the prompt is the only thing to wait for."""
    prompt_or_exit(port, echo)
    port.write((line + '\r').encode())
    port.flush()
    try:
        return read_until(port, rb'> ', wait, echo).decode('ascii', 'replace')
    except TimeoutError:
        print(f'[doom_debug] no BIOS prompt within {wait:.0f} s of {line!r}', flush=True)
        return ''


def bios_status(port, echo):
    """Print the BIOS `status` video line, which carries the LCD DMA counters.

    `underflows` is the objective witness for the reported bands and flicker:
    the panel DMA streams the framebuffer straight out of DDR, so if DOOM's
    texture traffic starves it the panel repeats or drops a line.  The counter
    is cumulative since reset and a payload owns the UART while it runs, so the
    meaningful reading is the delta across a run: status, boot, play, exit,
    status."""
    text = bios_command(port, echo, 'status', 15)
    for line in text.splitlines():
        if 'RGB LCD' in line:
            print('BIOS ' + line.strip())
    return text


def boot_payload(port, args, echo):
    """BIOS prompt → TFTP the payload → boot it → wait for the marker.

    Uses the same building blocks as scripts/doom_verify.py.  Only one process
    can hold the UART, so a debug session that wants to choose the payload has
    to own the whole sequence."""
    image = args.image.resolve()
    unpack(image.read_bytes())            # refuse to offer a broken RPB1
    prompt_or_exit(port, echo)
    port.write(b'ls\r')
    port.flush()
    listing = read_until(port, rb'> ', 60, echo)
    if args.wad_name.encode() not in listing:
        raise SystemExit(f'{args.wad_name} is not in the SD root; serve it with '
                         f'scripts/bios_tftp.py and run `fetchraw {args.wad_name}` at the prompt.')
    port.write(('set server ' + args.host_ip + '\r').encode())
    port.flush()
    read_until(port, rb'> ', 30, echo)
    server = PayloadServer(args.host_ip, image.read_bytes(), args.tftp_name)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(server.serve_once)
            port.write(('boot net ' + args.tftp_name + '\r').encode())
            port.flush()
            try:
                read_until(port, args.boot_marker.encode(), args.boot_seconds, echo)
            except TimeoutError:
                raise SystemExit(f'{args.boot_marker!r} never appeared within '
                                 f'{args.boot_seconds:.0f} s; the payload may have faulted, '
                                 'check the transcript above')
            future.result(timeout=5)
    finally:
        server.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', default='COM4')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--command',
                    help='name from ' + ', '.join(sorted(COMMANDS)) + ', or a raw character')
    ap.add_argument('--compare', action='store_true',
                    help='skip the board: diff the native frame and source dumps in --out-dir')
    ap.add_argument('--boot', action='store_true',
                    help='boot the payload from the BIOS prompt before the command')
    ap.add_argument('--host-ip', help='TFTP bind address the board can reach (--boot)')
    ap.add_argument('--image', type=Path, default=ROOT / 'build/doom-payload/BOOT.RPB',
                    help='RPB1 payload to serve (--boot)')
    ap.add_argument('--tftp-name', default='DOOM.RPB')
    ap.add_argument('--wad-name', default='DOOM1.WAD', help='IWAD that must be on the SD card')
    ap.add_argument('--boot-marker', default='I_InitGraphics',
                    help='transcript line that means the payload is up (--boot)')
    ap.add_argument('--boot-seconds', type=float, default=300.0)
    ap.add_argument('--settle', type=float, default=0.0,
                    help='let the game run this long after the marker (--boot)')
    ap.add_argument('--out-dir', type=Path, default=ROOT / 'build/doom-debug')
    ap.add_argument('--wad', type=Path, default=ROOT / 'build/doom-assets/DOOM1.WAD',
                    help='PLAYPAL source for --command source')
    ap.add_argument('--timeout', type=float, default=180.0)
    ap.add_argument('--no-pause', action='store_true',
                    help='dump without pausing first (the CRC then catches a frame that moved)')
    ap.add_argument('--resume', action='store_true', help='unpause the game when done')
    ap.add_argument('--bios-command', metavar='LINE',
                    help='talk to the BIOS console instead of the payload: send one line '
                         '(exits a running payload first).  Example: --bios-command '
                         '"fetchraw DOOM1.WAD"')
    ap.add_argument('--bios-timeout', type=float, default=60.0,
                    help='seconds to wait for the prompt after --bios-command (default 60; '
                         'fetchraw of a 4 MiB WAD needs about 17 s)')
    args = ap.parse_args()
    if args.compare:
        return compare(args.out_dir, args.wad)

    def echo(data):
        sys.stdout.write(data.decode(errors='replace'))
        sys.stdout.flush()

    if args.bios_command:
        with serial.Serial(args.port, args.baud, timeout=0.05) as port:
            port.reset_input_buffer()
            bios_command(port, echo, args.bios_command, args.bios_timeout)
        return 0

    if not args.command:
        ap.error('--command is required (or use --compare or --bios-command)')
    if args.boot and not args.host_ip:
        ap.error('--host-ip is required with --boot')

    name = args.command
    if name == 'boot-check' and not args.boot:
        ap.error('boot-check requires --boot and an app-specific --boot-marker')
    char = COMMANDS.get(name, name)
    if name == 'bios-status':
        char = ''                 # a BIOS console query, not a payload command
    if len(char) > 1:
        raise SystemExit(f'unknown command {name!r}; known: ' + ', '.join(sorted(COMMANDS)))
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        port.reset_input_buffer()
        if name == 'bios-status':
            bios_status(port, echo)
            return 0
        if args.boot:
            boot_payload(port, args, echo)
            if name == 'boot-check':
                wait_prompt(port, echo)
                print('[doom_debug] app marker observed and BIOS prompt restored', flush=True)
                return 0
            if args.settle:
                print(f'[doom_debug] letting the game run {args.settle:.0f} s', flush=True)
                drain(port, args.settle, echo)
        if name in ('pause', 'resume'):
            want = name == 'pause'
            if paused_state(port, echo) == want:
                print(f'[doom_debug] already {"paused" if want else "running"}', flush=True)
                return 0
        if name in DUMPS and not args.no_pause:
            if not paused_state(port, echo):
                port.write(b'p')
                port.flush()
                read_until(port, rb'^DBG paused', 10, echo)
        port.write(char.encode())
        port.flush()
        if name in DUMPS:
            # Every command ends with `DBG done`; 'source' sends two dumps, so a
            # first DBGEND is not the end of the answer.
            transcript = read_until(port, rb'^DBG done', args.timeout, echo)
        else:
            # Text reports have no terminator; they end when the board stops talking.
            transcript = drain(port, min(args.timeout, 10.0), echo)
        if args.resume:
            if paused_state(port, echo):
                port.write(b'p')
                port.flush()
            drain(port, 2.0, echo)

    text = bytes(transcript)
    if b'DBGERR' in text:
        print('\nboard refused the command', file=sys.stderr)
        return 1
    if name not in DUMPS:
        return 0

    dumps = parse_dumps(text)
    board = next((d[4] for d in dumps if d[0] == 'palette'), None)
    palette = rgb_palette(board) if board else None
    for kind, width, height, bpp, raw in dumps:
        if kind == 'palette':
            path = out / 'palette.raw'
            path.write_bytes(raw)
            print('palette:', path)
            continue
        stem = out / f'{kind}-{width}x{height}'
        raw_path = stem.with_suffix('.raw')
        raw_path.write_bytes(raw)
        png_path = stem.with_suffix('.png')
        if bpp == 2:
            raw_to_png(str(raw_path), str(png_path), width, height)
        elif bpp == 1:
            index_png(raw, png_path, palette or wad_palette(args.wad), width, height)
        else:
            raise SystemExit(f'unexpected {bpp} bytes per pixel')
        print('raw:', raw_path)
        print('png:', png_path)
    slots = {d[0]: d for d in dumps if d[0] in ('slot0', 'slot1')}
    if len(slots) == 2:
        # Taken while frozen, so nothing is drawing: the two slots must agree.
        # If they do not, the panel alternates a current and a stale image, and
        # that is exactly what a player calls flicker.
        a, b = slots['slot0'][4], slots['slot1'][4]
        width, height, bpp = slots['slot0'][1:4]
        row = width * bpp
        rows = sum(1 for y in range(height)
                   if a[y * row:(y + 1) * row] != b[y * row:(y + 1) * row])
        verdict = ('identical: the panel alternates two copies of one image, '
                   'so nothing here can flicker' if not rows else
                   'the panel alternates two different images: visible flicker')
        print(f'slot0 vs slot1: {sum(1 for x, y in zip(a, b) if x != y)} bytes, '
              f'{rows}/{height} rows differ - {verdict}')
    return 0


def drain(port, seconds, echo):
    data = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        chunk = port.read(512)
        if chunk:
            data += chunk
            echo(chunk)
            deadline = min(deadline, time.monotonic() + 1.0)
    return data


if __name__ == '__main__':
    sys.exit(main())
