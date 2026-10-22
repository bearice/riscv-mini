"""JTAG identity safety: a different ROM must never be restarted as selected."""
import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from mini_ops.mega138k_ae350.ae350_jtag import CPUJTAG, restart


class JTAGTest(unittest.TestCase):
    def test_linux_requires_explicit_interface_serial(self):
        library=mock.Mock()
        with mock.patch('mini_ops.mega138k_ae350.ae350_jtag.os.name','posix'), \
             mock.patch('mini_ops.mega138k_ae350.ae350_jtag.c.CDLL',return_value=library):
            with self.assertRaisesRegex(ValueError,'serial:'):
                CPUJTAG(123,'unused')
        library.FT_OpenEx.assert_not_called()

    def test_linux_serial_is_passed_to_d2xx_as_serial(self):
        library=mock.Mock();library.FT_OpenEx.return_value=0
        with mock.patch('mini_ops.mega138k_ae350.ae350_jtag.os.name','posix'), \
             mock.patch('mini_ops.mega138k_ae350.ae350_jtag.c.CDLL',return_value=library), \
             mock.patch.object(CPUJTAG,'scan',side_effect=[0x1000563d,0,0x71,0,0]), \
             mock.patch.object(CPUJTAG,'tms'),mock.patch.object(CPUJTAG,'send'), \
             mock.patch.object(CPUJTAG,'call'),mock.patch.object(CPUJTAG,'write'):
            cpu=CPUJTAG('serial:FactoryAIOT ProA','unused')
        args=library.FT_OpenEx.call_args.args
        self.assertEqual(args[0].value,b'FactoryAIOT ProA')
        self.assertEqual(args[1],1)

    def test_rom_mismatch_does_not_change_pc_or_release_capture(self):
        cpu = mock.Mock(spec=CPUJTAG)
        cpu.read_memory.return_value = b'wrong'
        callback = mock.Mock()
        with mock.patch('mini_ops.mega138k_ae350.ae350_jtag.CPUJTAG', return_value=cpu):
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
