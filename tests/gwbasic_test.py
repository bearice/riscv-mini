"""GW-BASIC payload checks: RPB1 image layout, then the interpreter on QEMU.

The interpreter is a BIOS payload, so the shipped artifact is the thing to check
first: the header the BIOS validates, the load address, the entry point and the
memory footprint it asks for.  The second half runs the real payload on QEMU
behind scripts/gwbasic_sim.py, which supplies a stand-in BIOS for the ecall
services (see that script for what is emulated and what is not).

The QEMU part is skipped when qemu-system-riscv32 is not installed.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import bios_image            # noqa: E402
import gwbasic_sim           # noqa: E402

IMAGE_DIR = ROOT / 'build/gwbasic'
IMAGE = IMAGE_DIR / 'BOOT.RPB'
SIM_DIR = ROOT / 'build/gwbasic-sim-test'
QEMU = gwbasic_sim.find_qemu()

# The BIOS loads a payload at 0x01000000 and reserves 64 KiB of stack above it.
LOAD = 0x01000000
LIMIT = 0x07e00000
MAX_FILE = 4 * 1024 * 1024


def build_image():
    subprocess.run([sys.executable, str(ROOT / 'scripts/gwbasic_image.py'),
                    '--output-dir', str(IMAGE_DIR)],
                   check=True, stdout=subprocess.DEVNULL)


class ImageTest(unittest.TestCase):
    """The artifact the board gets."""

    @classmethod
    def setUpClass(cls):
        if not IMAGE.exists():
            build_image()
        cls.header, cls.payload = bios_image.unpack(IMAGE.read_bytes())

    def test_header_and_crc(self):
        self.assertEqual(self.header[0], bios_image.MAGIC)
        self.assertEqual(self.header[1], bios_image.VERSION)
        self.assertEqual(self.header[2], LOAD)
        self.assertEqual(len(self.payload), self.header[3])

    def test_entry_points_at_the_linked_start(self):
        self.assertEqual(self.header[5], LOAD)

    def test_memory_footprint(self):
        file_bytes, memory_bytes = self.header[3], self.header[4]
        self.assertLess(file_bytes, MAX_FILE)
        self.assertGreaterEqual(memory_bytes, file_bytes)
        # The payload asks for its 4 MiB heap (.bss) on top of the image.
        self.assertGreater(memory_bytes - file_bytes, 4 * 1024 * 1024)
        self.assertLessEqual(memory_bytes, LIMIT - LOAD - 65536)

    def test_corruption_is_rejected(self):
        image = bytearray(IMAGE.read_bytes())
        for index in (0, 4, 12, 16, 20, 24, len(image) - 1):
            with self.subTest(index=index):
                bad = bytearray(image)
                bad[index] ^= 1
                with self.assertRaises(ValueError):
                    bios_image.unpack(bytes(bad))


@unittest.skipUnless(QEMU, 'qemu-system-riscv32 is not installed')
class SimTest(unittest.TestCase):
    """The interpreter, running its real code on the RISC-V core."""

    @classmethod
    def setUpClass(cls):
        cls.elf = gwbasic_sim.build(SIM_DIR)

    def run_script(self, text, timeout=120):
        script = (text.rstrip('\n') + '\nSYSTEM\n').encode()
        code, output, timed_out = gwbasic_sim.run(self.elf, QEMU, script, timeout,
                                                  SIM_DIR / 'test.log')
        self.assertFalse(timed_out, 'the payload did not finish')
        self.assertNotIn('SIM TRAP', output)
        self.assertEqual(code, 0, output[-2000:])
        return output

    def test_banner_and_prompt(self):
        output = self.run_script('PRINT 1+1')
        self.assertIn('GW-BASIC for riscv-mini', output)
        self.assertIn(' Bytes free', output)
        self.assertIn(' 2 ', output)

    def test_built_in_self_test(self):
        output = self.run_script('TESTS')
        self.assertIn('ALL TESTS PASSED', output)

    def test_repl_entry_list_and_run(self):
        output = self.run_script(
            '10 FOR I=1 TO 3: PRINT I;: NEXT I\n'
            '20 PRINT\n'
            '30 A$="HELLO": PRINT MID$(A$,2,3)\n'
            'LIST\n'
            'RUN')
        self.assertIn('10 FOR I=1 TO 3', output)
        self.assertIn(' 1  2  3 ', output)
        self.assertIn('ELL', output)

    def test_error_reporting(self):
        output = self.run_script('PRINT 1/0')
        self.assertIn('Division by zero', output)


if __name__ == '__main__':
    unittest.main()
