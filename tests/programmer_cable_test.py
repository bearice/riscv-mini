"""Cable discovery follows USB re-enumeration and refuses ambiguous targets."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from programmer_cable import resolve_location
from mini import parser


class ProgrammerCableTests(unittest.TestCase):
    def scan(self, output, code=0):
        return subprocess.CompletedProcess(['programmer'], code, output, '')

    def test_reenumeration_is_rescanned_and_uart_channel_ignored(self):
        runner = Mock(side_effect=[self.scan(
            'Cable found: USB Debugger A/0/4145/null\n'
            'Cable found: USB Debugger A/1/0/null\n'), self.scan(
            'Cable found: USB Debugger A/0/4161/null\n')])
        self.assertEqual(resolve_location('programmer', runner=runner), '4145')
        self.assertEqual(resolve_location('programmer', runner=runner), '4161')
        self.assertEqual(runner.call_count, 2)

    def test_explicit_override_skips_scan(self):
        runner = Mock()
        self.assertEqual(resolve_location('programmer', '1234', runner=runner), '1234')
        runner.assert_not_called()

    def test_no_cable_or_multiple_jtag_candidates_are_rejected(self):
        for output in ('', 'Cable found: USB Debugger A/1/0/null\n',
                       'Cable found: USB Debugger A/0/4145/null\n'
                       'Cable found: USB Debugger A/0/4161/null\n'):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                resolve_location('programmer', runner=Mock(return_value=self.scan(output)))

    def test_failed_scan_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'scan failed'):
            resolve_location('programmer', runner=Mock(return_value=self.scan('error', 1)))

    def test_cli_default_is_discovery(self):
        self.assertIsNone(parser().parse_args(['board', 'recover']).location)
        self.assertEqual(parser().parse_args(['board','update']).method,'programmer')


if __name__ == '__main__':
    unittest.main()
