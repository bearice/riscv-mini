"""Host regression for firmware verification's final BIOS display state."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import firmware_verify


class Port:
    def __init__(self, bios=True):
        self.bios = bios
        self.commands = []
        self.data = bytearray()
        self.graphics = False
        self.blank = False

    def __enter__(self):return self
    def __exit__(self, *args):pass
    def reset_input_buffer(self):self.data.clear()
    def flush(self):pass
    def read_all(self):
        result = bytes(self.data)
        self.data.clear()
        return result
    def write(self, data):
        name = data.decode().strip()
        self.commands.append(name)
        if name.startswith('test lcd'):
            self.graphics = True
            if name == 'test lcd clear':self.blank = True
        if name == 'tty':
            self.graphics = False
            self.blank = False
        if name == 'status':
            text = 'CPU/sys=60 MHz DDR=120 MHz\r\n' + ('BIOS ABI=1\r\n' if self.bios else '')
        elif name in ('test unknown',):text = 'ERR test command/argument\r\n'
        else:text = 'TEST ' + name[5:] + ' PASS\r\nUNSUPPORTED: feature disabled\r\nUNSUPPORTED: L2 disabled\r\n'
        self.data.extend((name + '\r\n' + text + '> ').encode())


class Session:
    def __init__(self, port, log):self.port = port
    def menu(self, reset):pass
    def upload(self, image):
        return b'RISCV MINI BIOS\r\nSYSTEM READY - BIOS\r\n> ' if self.port.bios else b'SYSTEM READY - monitor\r\n> '
    def until(self, marker, timeout):return self.port.read_all()


class Tests(unittest.TestCase):
    def verify(self, bios):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as temp:
            output = Path(temp)
            (output / 'firmware').mkdir()
            (output / 'firmware/app.img').write_bytes(b'image')
            validation = {'bitstream_sha256':'test', 'boot_image':{'abi_tag':0},
                          'features':{n:n == 'video' for n in ('video','filesystem','sd','flash','spi_lcd','board_io','ws2812','audio','eth','usb','mic')}}
            port = Port(bios)
            with patch.object(sys, 'argv', ['firmware_verify.py','--output-dir',temp]), \
                 patch.object(firmware_verify, 'verified_output', return_value=(validation,None)), \
                 patch.object(firmware_verify, 'unpack_image'), \
                 patch.object(firmware_verify.serial, 'Serial', return_value=port), \
                 patch.object(firmware_verify, 'BootSession', Session):
                firmware_verify.main()
            return port

    def test_bios_verification_restores_text_after_clear(self):
        port = self.verify(True)
        self.assertFalse(port.graphics, 'Verification leaves BIOS in graphics mode')
        self.assertFalse(port.blank, 'Verification leaves the LCD black')
        self.assertEqual(port.commands[-2:], ['tty','status'])

    def test_monitor_does_not_receive_bios_only_command(self):
        port = self.verify(False)
        self.assertNotIn('tty', port.commands)


if __name__ == '__main__':unittest.main()
