"""Exercise an already-running BIOS through its firmware commands and TFTP.

Creates a new BIOSDEM.RPB file and saves BIOS.CFG on SD. Does not program Flash,
reset the board, change host network settings, or require USB disconnection.
"""
import argparse
import concurrent.futures
from pathlib import Path
import serial
from boot_upload import BootSession
from bios_image import pack, unpack
from bios_tftp import PayloadServer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host-ip', required=True)
    p.add_argument('--port', default='COM4')
    p.add_argument('--image', type=Path, default=Path('build/bios-payload/BOOT.RPB'))
    p.add_argument('--sd-file', default='BIOSDEM.RPB', help='new SD file name; existing files are never replaced')
    p.add_argument('--log', type=Path, default=Path('build/bios/verification.log'))
    a = p.parse_args()
    image = a.image.read_bytes()
    header, raw = unpack(image)
    a.log.parent.mkdir(parents=True, exist_ok=True)
    with a.log.open('wb') as log, serial.Serial(a.port, 115200, timeout=0.1) as port:
        session = BootSession(port, log)

        def command(text, expect=None, timeout=30):
            port.write((text + '\r').encode())
            result = session.until(b'> ', timeout)
            print(result.decode(errors='replace'), flush=True)
            if expect and expect not in result:
                raise AssertionError(f'{text}: expected {expect!r}')
            if b'PAYLOAD DEMO FAIL' in result:
                raise AssertionError('payload service/BSS test failed')
            return result

        def transfer(text, name, data, expect, retry=False):
            server = PayloadServer(a.host_ip, data, name, exercise_retry=retry)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(server.serve_once)
                    command(text, expect, timeout=45)
                    if not future.result(timeout=5):
                        raise AssertionError('TFTP server rejected request')
                log.write(('TFTP ' + repr(server.events) + '\n').encode())
                print(server.events, flush=True)
            finally:
                print('Server events:', server.events, flush=True)
                server.close()

        port.reset_input_buffer()
        command('settings defaults')
        command('set server ' + a.host_ip)
        command('test bios', b'BIOS TEST PASS')
        transfer('boot net', 'BOOT.RPB', image, b'PAYLOAD RETURNED', retry=True)
        # A multiple of 512 bytes requires a final zero-length DATA packet.
        padded = ((len(image) + 511) // 512) * 512 - 32
        exact = pack(raw.ljust(padded, b'\0'), max(header[4], padded), header[5])
        transfer('boot net EXACT.RPB', 'EXACT.RPB', exact, b'PAYLOAD DEMO PASS', retry=True)
        bad = bytearray(image)
        bad[-1] ^= 1
        transfer('boot net BAD.RPB', 'BAD.RPB', bad, b'BOOT rejected')
        transfer('fetch ' + a.sd_file, a.sd_file, image, b'FETCH SAVED')
        command('boot sd ' + a.sd_file, b'PAYLOAD DEMO PASS')
        command('boot sd ' + a.sd_file, b'PAYLOAD DEMO PASS')
        command('boot sd ABSENT.RPB', b'file missing')
        command('set file ' + a.sd_file)
        command('settings save', b'SETTINGS SAVED')
        command('settings defaults')
        command('settings load', b'SETTINGS LOADED')
        state = command('settings')
        if ('file=' + a.sd_file).encode() not in state or ('server=' + a.host_ip).encode() not in state:
            raise AssertionError('settings did not survive reload')
        command('set ip 999.1.2.3', b'ERR IP')
        command('set delay 30001', b'ERR delay')
        command('post', b'POST BIOS ecall PASS')
        command('status')
        print('BIOS END-TO-END PASS', flush=True)


if __name__ == '__main__':
    main()
