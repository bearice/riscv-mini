"""JTAG identity safety: a different ROM must never be restarted as selected."""
import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from mini_ops.ae350_jtag import CPUJTAG, restart


class JTAGTest(unittest.TestCase):
    def test_rom_mismatch_does_not_change_pc_or_release_capture(self):
        cpu = mock.Mock(spec=CPUJTAG)
        cpu.read_memory.return_value = b'wrong'
        callback = mock.Mock()
        with mock.patch('mini_ops.ae350_jtag.CPUJTAG', return_value=cpu):
            with self.assertRaisesRegex(ValueError, 'readback differs'):
                restart(123, 'unused', b'right', callback)
        self.assertFalse(any(len(call.args) > 1 for call in cpu.register.call_args_list))
        callback.assert_not_called()
        cpu.close.assert_called_once()

    def test_dmi_failure_is_not_treated_as_register_data(self):
        cpu = CPUJTAG.__new__(CPUJTAG)
        cpu.bits = 41
        cpu.scan = mock.Mock(side_effect=[0, 3])
        cpu.tms = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, 'DMI error'):
            cpu.read(0x11)

    def test_abstract_busy_does_not_read_stale_data(self):
        cpu = CPUJTAG.__new__(CPUJTAG)
        cpu.write = mock.Mock()
        cpu.read = mock.Mock(return_value=1 << 12)
        with self.assertRaisesRegex(RuntimeError, 'busy timeout'):
            cpu.register(0x7b1)
        self.assertTrue(all(call.args == (0x16,) for call in cpu.read.call_args_list))


if __name__ == '__main__':
    unittest.main()
