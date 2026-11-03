"""JTAG identity safety: a different ROM must never be restarted as selected."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from mini_ops.mega138k_ae350.ae350_jtag import CPUJTAG, ftdi_sio_interface, restart, parse_script, script_text, run_script


class FakeCPU:
    def __init__(self, halted=False):
        self.halted, self.log, self.regs, self.mem = halted, [], {0x7b1: 0x1000, 0x7c0: 0xa0000001}, {}

    def is_halted(self):
        return self.halted

    def halt(self):
        self.log.append('halt'); self.halted = True

    def resume(self):
        self.log.append('resume'); self.halted = False

    def register(self, number, value=None):
        assert self.halted, 'register access while running'
        if value is not None:
            self.regs[number] = value
        return self.regs.get(number, 0)

    def read_memory(self, address, length):
        assert self.halted
        return b''.join(self.mem.get(address + off, 0).to_bytes(4, 'little') for off in range(0, length, 4))

    def write_word(self, address, value):
        assert self.halted
        self.mem[address] = value


class JTAGScriptTest(unittest.TestCase):
    def test_parse_accepts_lines_semicolons_comments_and_register_names(self):
        steps = parse_script('halt  # stop\nread-reg milmb; write-word 0x100 0x5\n\nexpect-reg satp 0 0x80000000')
        self.assertEqual([s['command'] for s in steps], ['halt', 'read-reg', 'write-word', 'expect-reg'])
        self.assertEqual(steps[1]['args'], [0x7c0])
        self.assertEqual(steps[3]['args'], [0x180, 0, 0x80000000])
        self.assertEqual(steps[2]['line'], 2)

    def test_parse_rejects_bad_scripts_before_hardware(self):
        for text, error in [('frob', 'unknown command'), ('read-word', 'takes'), ('read-reg nosuch', 'bad argument'),
                            ('read-word 0x102', 'aligned'), ('write-word 0 0x100000000', 'uint32'),
                            ('step 0', 'positive'), ('# only a comment', 'no commands')]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                parse_script(text)

    def test_legacy_flags_become_one_command_and_cannot_mix_with_scripts(self):
        args = mock.Mock(script=None, exec=None, halt=False, resume=False, step=False, restart=None, reset=False,
                         pc=False, set_pc=None, read_word=0xa0002080, write_word=None, read_mem=None,
                         read_reg=None, write_reg=None)
        self.assertEqual(script_text(args), 'read-word 0xa0002080')
        args.read_reg = 'milmb'
        with self.assertRaisesRegex(ValueError, '--exec'):
            script_text(args)
        args.read_reg, args.exec = None, ['halt']
        with self.assertRaisesRegex(ValueError, 'cannot be combined'):
            script_text(args)

    def test_access_halts_only_temporarily_and_explicit_halt_persists(self):
        cpu, op = FakeCPU(halted=False), mock.Mock()
        self.assertFalse(run_script(cpu, op, parse_script('read-reg milmb')))
        self.assertEqual(cpu.log, ['halt', 'resume'])
        cpu.log.clear()
        self.assertTrue(run_script(cpu, op, parse_script('halt; write-word 0x10 7; read-word 0x10')))
        self.assertEqual(cpu.log, ['halt'])
        self.assertEqual(cpu.mem[0x10], 7)

    def test_expect_mismatch_stops_script_and_records_failure(self):
        cpu, op = FakeCPU(halted=True), mock.Mock()
        with self.assertRaisesRegex(RuntimeError, 'line 1: .*MISMATCH'):
            run_script(cpu, op, parse_script('expect-reg milmb 0xa0000000\nwrite-word 0x20 1'))
        self.assertNotIn(0x20, cpu.mem)
        self.assertFalse(op.record.call_args.kwargs['matched'])
        run_script(cpu, op, parse_script('expect-reg milmb 0xa0000000 0xfffffc00'))


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
             mock.patch('mini_ops.mega138k_ae350.ae350_jtag.ftdi_sio_interface',return_value=None), \
             mock.patch('mini_ops.mega138k_ae350.ae350_jtag.c.CDLL',return_value=library), \
             mock.patch.object(CPUJTAG,'scan',side_effect=[0x1000563d,0,0x71,0,0]), \
             mock.patch.object(CPUJTAG,'tms'),mock.patch.object(CPUJTAG,'send'), \
             mock.patch.object(CPUJTAG,'call'),mock.patch.object(CPUJTAG,'write'):
            cpu=CPUJTAG('serial:FactoryAIOT ProA','unused')
        args=library.FT_OpenEx.call_args.args
        self.assertEqual(args[0].value,b'FactoryAIOT ProA')
        self.assertEqual(args[1],1)

    def test_kernel_interface_is_resolved_from_the_d2xx_serial(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            driver = root/'driver'; driver.mkdir()
            for (bus, serial) in (('1-3', '2025030317'), ('1-4', 'FactoryAIOT Pro')):
                device = root/'devices'/bus; device.mkdir(parents=True)
                (device/'serial').write_text(serial + '\n')
                for number in (0, 1):
                    interface = device/('%s:1.%d' % (bus, number)); interface.mkdir()
                    (interface/'bInterfaceNumber').write_text('%02x\n' % number)
                    (driver/interface.name).symlink_to(interface)
            with mock.patch('mini_ops.mega138k_ae350.ae350_jtag.os.name', 'posix'):
                self.assertEqual(ftdi_sio_interface('serial:FactoryAIOT ProA', driver), '1-4:1.0')
                self.assertEqual(ftdi_sio_interface('serial:2025030317B', driver), '1-3:1.1')
                self.assertIsNone(ftdi_sio_interface('serial:FactoryAIOT ProC', driver))
                self.assertIsNone(ftdi_sio_interface('1', driver))

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
