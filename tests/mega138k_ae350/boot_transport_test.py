"""The shared run entry accepts the same Linux transports as the suites."""
import sys
import unittest
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[2] / 'scripts')]
from mini_ops.mega138k_ae350.ae350_boot_ops import RunRequest


class TransportTests(unittest.TestCase):
    def test_linux_and_windows_selectors(self):
        for port, cpu in (('/dev/serial/by-id/ftdi-if02-port0', 'serial:FT5AW9FXB'),
                          ('COM9', '1234')):
            RunRequest('run', 'selected', port=port, cpu_jtag_location=cpu).validate()

    def test_invalid_selectors_are_rejected(self):
        for cpu in ('serial:', 'unknown', 0):
            with self.assertRaises(ValueError):
                RunRequest('run', 'selected', cpu_jtag_location=cpu).validate()


if __name__ == '__main__':
    unittest.main()
