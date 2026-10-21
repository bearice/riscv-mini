"""Cable discovery: identity-aware, ambiguity-refusing, no positional channel.

The list position in a scan is not a channel number, so the resolver probes the
advertised USB locations and picks the one that actually scans the expected
target.  These tests mock the tool only; they never touch hardware.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tools.programmer_cable import resolve_location
from mini import parser

DEVICE = 'Device Info:\n  ID: 0x0001081B\n  1 device(s) found!\n'


class ProgrammerCableTests(unittest.TestCase):
    @staticmethod
    def scan(output, code=0):
        return subprocess.CompletedProcess(['programmer'], code, output, '')

    def test_only_the_target_that_scans_the_fpga_is_selected(self):
        # Two debuggers advertise a JTAG interface; the first one does not reach
        # an FPGA, the second one does.
        runner = Mock(side_effect=[
            self.scan('Cable found: USB Debugger A/0/4145/null\n'
                      'Cable found: USB Debugger A/0/4161/null\n'),
            self.scan('Cable found: USB Debugger A/0/4145/null\n'),      # probe 4145
            self.scan('Cable found: USB Debugger A/0/4161/null\n' + DEVICE),  # probe 4161
        ])
        self.assertEqual(resolve_location('programmer', runner=runner), '4161')

    def test_explicit_override_is_verified_when_an_identity_is_known(self):
        runner = Mock(return_value=self.scan('Target Cable: Gowin USB Cable/0/1234\n' + DEVICE))
        self.assertEqual(resolve_location('programmer', '1234', identity=0x0001081B, runner=runner), '1234')
        self.assertEqual(runner.call_count, 1)

    def test_explicit_override_with_wrong_identity_is_rejected(self):
        runner = Mock(return_value=self.scan('Target Cable: Gowin USB Cable/0/1234\n'
                                             '  ID: 0x0000123B\n  1 device(s) found!\n'))
        with self.assertRaisesRegex(RuntimeError, 'expected target identity'):
            resolve_location('programmer', '1234', identity=0x0001081B, runner=runner)

    def test_explicit_override_without_identity_skips_probing(self):
        runner = Mock()
        self.assertEqual(resolve_location('programmer', '1234', runner=runner), '1234')
        runner.assert_not_called()

    def test_no_candidate_or_no_target_is_rejected(self):
        for output in ('', 'Cable found: USB Debugger A/1/0/null\n'):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                resolve_location('programmer', runner=Mock(return_value=self.scan(output)))

    def test_failed_scan_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'scan failed'):
            resolve_location('programmer', runner=Mock(return_value=self.scan('error', 1)))

    def test_cli_default_is_discovery(self):
        self.assertIsNone(parser().parse_args(['board', 'recover']).location)
        self.assertEqual(parser().parse_args(['board', 'update']).method, 'programmer')


if __name__ == '__main__':
    unittest.main()
