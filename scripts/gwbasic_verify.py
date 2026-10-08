"""Run the GW-BASIC payload on the board through the resident BIOS and check it.

The BIOS must already be running (for a RAM load: `boot_upload.py --mode uart`).
This script drives the BIOS console over the serial port, exports the payload over
TFTP to the address the BIOS talks to, boots it, runs the built-in self test and a
few interactive checks, then returns to the BIOS with SYSTEM.

Nothing here programs Flash, resets the board, or changes host network or firewall
settings.  The TFTP server runs as a separate process under a Python interpreter
the host firewall already allows: the repository virtualenv is not on that list,
and listening on UDP/69 from it never receives the board's request.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from bios_image import unpack                # noqa: E402

ALLOWED_PYTHON = Path(r'C:\Users\bearice\AppData\Local\Programs\Python\Python313\python.exe')


class Console:
    """Line oriented console on top of the BIOS TTY."""

    def __init__(self, port, log):
        self.port = port
        self.log = log

    def note(self, data):
        if isinstance(data, str):
            data = data.encode()
        self.log.write(data)
        self.log.flush()

    def until(self, marker, timeout=60):
        data = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.port.read(1)
            if chunk:
                data.extend(chunk)
                if data.endswith(marker):
                    self.note(data)
                    return bytes(data)
        self.note(data)
        raise TimeoutError(f'waiting for {marker!r}; got {bytes(data[-800:])!r}')

    def command(self, text, marker=b'> ', timeout=60):
        self.port.write((text + '\r').encode())
        self.port.flush()
        return self.until(marker, timeout)

    def send(self, text, pace=0.002):
        """Type the text; the console is a real terminal, so pace the bytes."""
        for ch in (text + '\r').encode():
            self.port.write(bytes([ch]))
            self.port.flush()
            if pace:
                time.sleep(pace)

    def line(self, text, marker=b'Ok\r\n', timeout=60):
        self.send(text)
        return self.until(marker, timeout)


def start_tftp(python, host_ip, image, name, log_path):
    """Start the TFTP exporter under an interpreter the firewall allows."""
    log = log_path.open('wb')
    proc = subprocess.Popen([str(python), str(ROOT / 'scripts/bios_tftp.py'),
                             '--bind', host_ip, '--file', str(image), '--name', name],
                            stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError('TFTP server exited: ' +
                               log_path.read_text(errors='replace'))
        if 'TFTP ready' in log_path.read_text(errors='replace'):
            return proc
        time.sleep(0.1)
    raise TimeoutError('TFTP server did not report ready')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', default='COM4')
    p.add_argument('--host-ip', default='169.254.25.153')
    p.add_argument('--image', type=Path, default=ROOT / 'build/gwbasic/BOOT.RPB')
    p.add_argument('--tftp-name', default='BASIC.RPB')
    p.add_argument('--tftp-python', type=Path, default=ALLOWED_PYTHON)
    p.add_argument('--output-dir', type=Path, default=ROOT / 'build/gwbasic/board-verify')
    p.add_argument('--no-quit-payload', action='store_true',
                   help='do not send q first; use when the BIOS prompt is already there')
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    image = a.image.read_bytes()
    unpack(image)                            # RPB1 header, CRC and layout
    tftp_python = a.tftp_python if a.tftp_python.is_file() else Path(sys.executable)
    report = {'image': str(a.image), 'bytes': len(image),
              'sha256': hashlib.sha256(image).hexdigest(),
              'tftp_name': a.tftp_name, 'host_ip': a.host_ip,
              'tftp_python': str(tftp_python), 'checks': {}, 'bios': []}
    log_path = a.output_dir / 'console.log'
    tftp_log = a.output_dir / 'tftp.log'
    proc = None
    try:
        with log_path.open('wb') as log, serial.Serial(a.port, 115200, timeout=0.2) as port:
            console = Console(port, log)
            port.reset_input_buffer()
            if not a.no_quit_payload:
                console.note(b'[host] q -> leave any running BIOS payload\r\n')
                port.write(b'q')
                port.flush()
            port.write(b'\r')                # ask for a fresh prompt
            port.flush()
            try:
                console.until(b'> ', timeout=20)
                report['checks']['bios_prompt'] = True
            except TimeoutError:
                report['checks']['bios_prompt'] = False
                console.note(b'[host] no BIOS prompt seen; assuming it is already there\r\n')
            status = console.command('status', timeout=30).decode(errors='replace')
            report['bios'] = [ln.strip() for ln in status.splitlines()
                              if 'BUILD' in ln or 'RISCV MINI' in ln or 'IP' in ln]

            proc = start_tftp(tftp_python, a.host_ip, a.image, a.tftp_name, tftp_log)
            console.command('set server ' + a.host_ip, timeout=30)
            boot = console.line('boot net ' + a.tftp_name, marker=b'Ok\r\n', timeout=180)
            text = boot.decode(errors='replace')
            report['checks']['payload_booted'] = 'GW-BASIC for riscv-mini' in text

            transcript = b''
            transcript += console.line('TESTS', marker=b'checks)\r\n', timeout=240)
            transcript += console.until(b'Ok\r\n', timeout=30)
            transcript += console.line('PRINT 1+1', timeout=30)
            for line in ('10 FOR I=1 TO 3: PRINT I;: NEXT I', '20 PRINT "BOARD OK"',
                         'LIST', 'RUN'):
                transcript += console.line(line, timeout=60)
            transcript += console.line('SYSTEM', marker=b'PAYLOAD RETURNED', timeout=60)
            seen = transcript.decode(errors='replace')
            report['checks']['self_test'] = 'ALL TESTS PASSED' in seen
            report['checks']['arithmetic'] = ' 2 ' in seen
            report['checks']['program_list'] = '10 FOR I=1 TO 3' in seen
            report['checks']['program_run'] = ' 1  2  3 ' in seen and 'BOARD OK' in seen
            report['checks']['returns_to_bios'] = True   # the SYSTEM wait succeeded
    finally:
        # Always drop the TFTP exporter: leaving it behind would hold UDP/69.
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    events = tftp_log.read_text(errors='replace') if tftp_log.exists() else ''
    report['tftp'] = [ln for ln in events.splitlines() if 'RRQ' in ln or 'ACK' in ln][:6]
    report['checks']['tftp_transfer'] = 'RRQ ' + a.tftp_name in events
    report['passed'] = all(report['checks'].values())
    (a.output_dir / 'gwbasic-verification.json').write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print('console log:', log_path)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
