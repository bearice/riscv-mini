"""Linux loader rejects incompatible images before opening the board."""
import struct
import gzip
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'tests')]
from bios_image import pack
from boot_image import unpack_image
from mini_ops.linux import preflight
from mini_operations_test import MiniOperationsTests


class LinuxImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.args = SimpleNamespace(os_image=root/'boot.OSB', kernel=root/'Image', timeout=180)
        self.args.os_image.write_bytes(pack(bytes(128), os_image=True))
        image = bytearray(128)
        struct.pack_into('<QQ', image, 8, 0x400000, 4096)
        image[48:56] = b'RISCV\0\0\0'
        image[56:60] = b'RSC\x05'
        self.args.kernel.write_bytes(image)

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_rv32_aligned_image(self):
        self.assertEqual(preflight(self.args)['image_size'], 4096)

    def test_rejects_rv64_offset(self):
        image = bytearray(self.args.kernel.read_bytes())
        struct.pack_into('<Q', image, 8, 0x200000)
        self.args.kernel.write_bytes(image)
        with self.assertRaisesRegex(ValueError, 'RV32'):
            preflight(self.args)

    def test_rejects_bss_past_safe_ram(self):
        image = bytearray(self.args.kernel.read_bytes())
        struct.pack_into('<Q', image, 16, 0x08000000)
        self.args.kernel.write_bytes(image)
        with self.assertRaisesRegex(ValueError, 'footprint'):
            preflight(self.args)

    def test_rejects_ordinary_bios_payload(self):
        self.args.os_image.write_bytes(pack(bytes(128)))
        with self.assertRaisesRegex(ValueError, 'OSB1'):
            preflight(self.args)

    def test_rejects_elf_instead_of_image(self):
        self.args.kernel.write_bytes(b'\x7fELF'+bytes(124))
        with self.assertRaisesRegex(ValueError, 'Linux Image'):
            preflight(self.args)

    def test_external_initrd_identity_and_safe_address(self):
        self.args.initrd = self.args.kernel.parent/'initrd.cpio.gz'
        self.args.initrd.write_bytes(gzip.compress(b'070701'+bytes(128)))
        result = preflight(self.args)
        self.assertEqual(result['initrd_load_address'], '0x4000000')
        self.assertEqual(result['initrd_size'], self.args.initrd.stat().st_size)
        self.assertEqual(len(result['initrd_sha256']), 64)

    def test_rejects_kernel_initrd_overlap(self):
        self.args.initrd = self.args.kernel.parent/'initrd.cpio.gz'
        self.args.initrd.write_bytes(gzip.compress(b'070701'+bytes(128)))
        image = bytearray(self.args.kernel.read_bytes())
        struct.pack_into('<Q', image, 16, 0x02001000)
        self.args.kernel.write_bytes(image)
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            preflight(self.args)

    def test_rejects_non_cpio_external_initrd(self):
        self.args.initrd = self.args.kernel.parent/'initrd.cpio.gz'
        self.args.initrd.write_bytes(gzip.compress(b'not a rootfs'))
        with self.assertRaisesRegex(ValueError, 'newc'):
            preflight(self.args)


class LinuxSramRunTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MiniOperationsTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_sram_run_stops_autoboot_then_uploads_without_flash_write(self):
        build, op, board, port, calls = self.fixture.backend()
        board.run()
        self.assertEqual([c[c.index('--operation_index')+1] for c in calls], ['2'])
        self.assertEqual(port.commands, [b'b', b'u'])
        self.assertEqual(port.received, unpack_image(build.image)[1])
        self.assertTrue((op.path/'uart.log').read_bytes())


if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(LinuxImageTests)
    suite.addTest(LinuxSramRunTests('test_sram_run_stops_autoboot_then_uploads_without_flash_write'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
