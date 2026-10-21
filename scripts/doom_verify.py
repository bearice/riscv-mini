"""Run the DOOM payload on the board through the resident BIOS and check it.

Reuses the existing building blocks instead of re-implementing them:

* `boot_upload.BootSession` owns the console state machine (prompt framing,
  DDR-failure detection, state waits) — the same session object the other board
  scripts drive.
* `bios_tftp.PayloadServer` serves the payload in-process, exactly as
  `scripts/bios_verify.py` does; no separate exporter process is started.
* `bios_image.unpack` validates the RPB1 image before it is offered to the board.

The script never assumes the board state: it first has to see the BIOS setup
prompt, and if it does not, it stops and tells you to recover with
`scripts/mini.py board recover --build current` rather than typing into whatever
is running.

Requires the BIOS build that provides the file-system services (FS_*) and
`DOOM1.WAD` in the SD root (`fetchraw DOOM1.WAD` with `scripts/bios_tftp.py`
serving the file). DOOM does not return to the BIOS by itself (F12 on the USB
keyboard does), so the run ends with DOOM still playing. Nothing here programs
Flash, resets the board, or changes host network settings.
"""
import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from tools.boot_session import BootSession     # noqa: E402
from bios_image import unpack                # noqa: E402
from bios_tftp import PayloadServer          # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', default='COM4')
    p.add_argument('--host-ip', required=True)
    p.add_argument('--image', type=Path, default=ROOT / 'build/doom-payload/BOOT.RPB')
    p.add_argument('--tftp-name', default='DOOM.RPB')
    p.add_argument('--wad-name', default='DOOM1.WAD')
    p.add_argument('--boot-seconds', type=float, default=600.0,
                   help='how long to wait for I_InitGraphics after the boot command')
    p.add_argument('--frame-seconds', type=float, default=180.0,
                   help='how long to wait for 200 presented frames')
    p.add_argument('--output-dir', type=Path, default=ROOT / 'build/doom-verify')
    a = p.parse_args()

    image = a.image.resolve()
    header, raw = unpack(image.read_bytes())      # raises if the RPB1 image is invalid
    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / 'doom-console.log'
    report = {'image': str(image), 'tftp_name': a.tftp_name, 'wad': a.wad_name,
              'header': {'load': hex(header[2]), 'file_bytes': header[3],
                         'memory_bytes': header[4], 'entry': hex(header[5])},
              'checks': {}}

    with log_path.open('wb') as log, serial.Serial(a.port, 115200, timeout=0.1) as port:
        session = BootSession(port, log)

        def echo(data):
            sys.stdout.write(data.decode(errors='replace'))
            sys.stdout.flush()
            return data

        def wait(marker, limit, label):
            """`until` in slices, streaming every byte as it arrives.

            A single long `until` call is silent until the marker lands, which
            makes a slow board indistinguishable from a hung script; this echoes
            continuously and says out loud when the board has been quiet.
            Bytes are collected in the stream callback, so a slice that times
            out does not lose transcript.
            """
            got = bytearray()
            started = quiet = time.monotonic()

            def stream(chunk):
                nonlocal quiet
                sys.stdout.write(chunk.decode(errors='replace'))
                sys.stdout.flush()
                got.extend(chunk)
                quiet = time.monotonic()

            while time.monotonic() - started < limit:
                try:
                    session.until(marker, min(limit, 15.0), on_chunk=stream)
                    return bytes(got), True
                except TimeoutError:
                    if time.monotonic() - quiet > 15:
                        print(f'\n[doom_verify] {label}: quiet for '
                              f'{int(time.monotonic() - quiet)} s, '
                              f'{int(time.monotonic() - started)} s of '
                              f'{limit:.0f} s used', flush=True)
            print(f'\n[doom_verify] {label}: {marker.decode(errors="replace")} '
                  f'still missing after {limit:.0f} s', flush=True)
            return bytes(got), False

        def command(text, marker=b'> ', timeout=60):
            print(f'\n[doom_verify] {text}', flush=True)
            port.write((text + '\r').encode())
            port.flush()
            return echo(session.until(marker, timeout))

        # Board state first: the BIOS setup prompt must answer, otherwise the
        # machine is running something else (a payload, or a failed boot).
        port.reset_input_buffer()
        port.write(b'\r')
        port.flush()
        try:
            session.until(b'> ', 5)
        except TimeoutError as exc:
            raise SystemExit(
                'board is not at the BIOS setup prompt (no "> "): recover first with\n'
                '  scripts/mini.py board recover --build current\n'
                f'last output: {exc}')

        listing = command('ls')
        report['checks']['mount_ok'] = b'SD init' in listing or b'SD /' in listing
        report['checks']['wad_on_sd'] = a.wad_name.encode() in listing
        if not report['checks']['wad_on_sd']:
            print(f'\n{a.wad_name} is not in the SD root; serve it with scripts/bios_tftp.py '
                  f'and run `fetchraw {a.wad_name}` in the BIOS console.', flush=True)
            return 1
        command('set server ' + a.host_ip)

        server = PayloadServer(a.host_ip, image.read_bytes(), a.tftp_name)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(server.serve_once)
                port.write(('boot net ' + a.tftp_name + '\r').encode())
                port.flush()
                boot, _ = wait(b'I_InitGraphics', a.boot_seconds, 'boot')
                report['checks']['tftp_transfer'] = future.result(timeout=5)
            report['tftp'] = [ln for ln in server.events if 'RRQ' in ln or 'ACK' in ln][:4]
        finally:
            server.close()

        report['checks']['payload_ran'] = b'I_InitGraphics' in boot
        report['checks']['no_fault'] = b'FAULT cause=' not in boot
        report['checks']['wad_loaded'] = (b'W_Init: Init WADfiles' in boot
                                          and b"doesn't have IWAD or PWAD id" not in boot)
        if not report['checks']['payload_ran']:
            report['passed'] = False
            (out / 'doom-verification.json').write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
            print(report, flush=True)
            return 1

        frames, _ = wait(b'DOOM presents=200 ', a.frame_seconds, 'frames')
        text = frames.decode(errors='replace')
        presents = [int(t.split('presents=')[1].split()[0])
                    for t in text.splitlines() if 'presents=' in t]
        report['presents'] = presents[-1] if presents else 0
        report['checks']['frames'] = any(v >= 200 for v in presents)
        report['checks']['no_fault_after_boot'] = b'FAULT cause=' not in frames
        report['doom_log_tail'] = text.splitlines()[-10:]

    report['passed'] = all(report['checks'].values())
    (out / 'doom-verification.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('\n' + json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    print('console log:', log_path, flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
