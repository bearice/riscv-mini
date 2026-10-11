"""DOOM payload checks: RPB1 image layout, then the game on QEMU.

The shipped artifact is the thing the BIOS validates, so the header, load
address, entry point and memory footprint are checked first.  The second half
runs the real payload on QEMU behind scripts/doom_sim.py, which supplies a
stand-in BIOS for the ecall services (video, file system, keys, clock) — see that
script for what is emulated and what is not.

Both the QEMU run and the WAD are external inputs: the sim is skipped when
qemu-system-riscv32 is missing, and when the shareware `DOOM1.WAD` has not been
placed in build/doom-assets/ (see docs/doom.md for the source and hash).
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import bios_image            # noqa: E402
import doom_sim              # noqa: E402

IMAGE_DIR = ROOT / 'build/doom-payload'
IMAGE = IMAGE_DIR / 'BOOT.RPB'
SIM_DIR = ROOT / 'build/doom-sim-test'
WAD = ROOT / 'build/doom-assets/DOOM1.WAD'
QEMU = doom_sim.find_qemu()

LOAD = 0x01000000
LIMIT = 0x07e00000
MAX_FILE = 4 * 1024 * 1024


def build_image():
    subprocess.run([sys.executable, str(ROOT / 'scripts/doom_image.py'),
                    '--soft-float', '--output-dir', str(IMAGE_DIR)],
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
        # DOOM asks for its 48 MiB heap (.bss) on top of the image.
        self.assertGreater(memory_bytes - file_bytes, 32 * 1024 * 1024)
        # Loading copies the image over the staging area at 0x07000000.
        self.assertLessEqual(memory_bytes, 0x06000000)
        self.assertLessEqual(memory_bytes, LIMIT - LOAD - 65536)

    def test_corruption_is_rejected(self):
        image = bytearray(IMAGE.read_bytes())
        for index in (0, 4, 12, 16, 20, 24, len(image) - 1):
            with self.subTest(index=index):
                bad = bytearray(image)
                bad[index] ^= 1
                with self.assertRaises(ValueError):
                    bios_image.unpack(bytes(bad))


def run_sim(timeout=600, presents=250):
    """Run the payload on QEMU; returns (CompletedProcess, captured log)."""
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/doom_sim.py'),
         '--output-dir', str(SIM_DIR), '--wad', str(WAD), '--soft-float',
         '--max-presents', str(presents), '--sample', '25',
         '--timeout', str(timeout),
         '--expect', 'W_Init: Init WADfiles',
         '--expect', 'adding DOOM1.WAD',
         '--expect', 'R_Init: Init DOOM refresh daemon',
         '--expect', 'P_Init: Init Playloop state',
         '--expect', 'I_InitGraphics',
         '--expect', 'DOOM presents=200'],
        capture_output=True, text=True, timeout=timeout + 60)
    log = SIM_DIR / 'sim.log'
    text = log.read_text(errors='replace') if log.exists() else result.stdout
    return result, text


@unittest.skipUnless(QEMU, 'qemu-system-riscv32 is not installed')
@unittest.skipUnless(WAD.is_file(), f'{WAD} is not present (shareware IWAD)')
class SimTest(unittest.TestCase):
    """DOOM running its real code on the RISC-V core."""

    @classmethod
    def setUpClass(cls):
        cls.result, cls.text = run_sim()

    def test_boot_reaches_the_game(self):
        self.assertNotIn('SIM TRAP', self.text)
        self.assertNotIn('FAULT', self.text)
        self.assertIn('Emulating the behavior', self.text)

    def test_no_lump_errors(self):
        self.assertNotIn('not found!', self.text)
        self.assertNotIn("doesn't have IWAD or PWAD id", self.text)

    def test_sim_reports_success(self):
        self.assertEqual(self.result.returncode, 0,
                         self.text[-2000:] + self.result.stdout[-2000:])


if __name__ == '__main__':
    unittest.main()
