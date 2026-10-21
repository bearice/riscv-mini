"""License outages retry; design failures and successful runs do not."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from scripts.modular.gowin import license_connection_failure, run_with_license_retry


class GowinRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def run_attempts(self, outputs, codes=None):
        calls = []
        codes = codes if codes is not None else [1] * len(outputs)
        def attempt(log):
            index = len(calls)
            calls.append(log)
            log.write_text(outputs[index], encoding='utf-8')
            return codes[index]
        with patch('scripts.modular.gowin.time.sleep') as sleep:
            code, log = run_with_license_retry(attempt, self.path)
        return code, log, calls, sleep, json.loads((self.path / 'gowin-attempts.json').read_text())

    def test_transient_signatures(self):
        for text in ('License verification failed Connection timeout',
                     'License verification failed\nServer not responding',
                     'Cannot connect to license server system.',
                     'FLEXnet Licensing error: -15,570',
                     'License server is unavailable'):
            with self.subTest(text=text):
                self.assertIsNotNone(license_connection_failure(text))

    def test_no_retry_for_empty_logs_expired_license_or_design_errors(self):
        for text in ('', 'ERROR (PR0004): unrouted nets', 'ERROR: syntax error',
                     'License has expired', 'Invalid license file',
                     'The license expires in 11 days.\nSynthesis completed',
                     'Connection timeout while reading an unrelated file'):
            with self.subTest(text=text):
                self.assertIsNone(license_connection_failure(text))

    def test_retry_then_success_preserves_both_logs(self):
        code, log, calls, sleep, summary = self.run_attempts(
            ['License verification failed\nServer not responding', 'Bitstream generation completed'], [1, 0])
        self.assertEqual(code, 0)
        self.assertEqual(log, calls[1])
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(5)
        self.assertIn('Server not responding', calls[0].read_text())
        self.assertEqual(summary['status'], 'succeeded')

    def test_outage_exhausts_four_attempts_and_bounded_backoff(self):
        code, _, calls, sleep, summary = self.run_attempts(['License verification failed Connection timeout'] * 4)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 15, 30])
        self.assertEqual(summary['status'], 'failed')

    def test_design_error_after_license_recovery_stops_immediately(self):
        code, _, calls, sleep, summary = self.run_attempts([
            'License verification failed Connection timeout', 'ERROR (PR0004): routing failed'], [1, 2])
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(5)
        self.assertIsNone(summary['attempts'][-1]['license_connection_failure'])

    def test_success_does_not_sleep(self):
        code, _, calls, sleep, _ = self.run_attempts(['The license expires in 11 days.'], [0])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_design_error_is_not_retried_even_with_a_license_message(self):
        code, _, calls, sleep, _ = self.run_attempts([
            'License verification failed Connection timeout\nERROR (PR0004): routing failed'])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_interrupt_during_backoff_does_not_start_another_attempt(self):
        calls = []
        def attempt(log):
            calls.append(log)
            log.write_text('License verification failed Connection timeout')
            return 1
        with patch('scripts.modular.gowin.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_with_license_retry(attempt, self.path)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads((self.path / 'gowin-attempts.json').read_text())['status'], 'interrupted')

    def test_vendor_zero_exit_with_license_failure_is_not_success(self):
        code, _, calls, _, summary = self.run_attempts(['License verification failed Connection timeout'] * 4, [0] * 4)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 4)
        self.assertEqual(summary['status'], 'failed')

    def test_real_child_process_failure_then_success(self):
        fixture = self.path / 'fake_gw_sh.py'
        fixture.write_text('import sys\n'
                           'print("License verification failed Connection timeout" if sys.argv[1]=="1" else "Bitstream generation completed", flush=True)\n'
                           'sys.exit(1 if sys.argv[1]=="1" else 0)\n')
        counter = []
        def attempt(log):
            counter.append(log)
            with log.open('w', encoding='utf-8') as stream:
                return subprocess.run([sys.executable, str(fixture), str(len(counter))],
                                      stdout=stream, stderr=subprocess.STDOUT).returncode
        with patch('scripts.modular.gowin.time.sleep'):
            code, final = run_with_license_retry(attempt, self.path)
        self.assertEqual(code, 0)
        self.assertEqual(len(counter), 2)
        self.assertIn('Bitstream generation completed', final.read_text())


if __name__ == '__main__':
    unittest.main()
