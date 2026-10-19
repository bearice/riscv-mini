"""Evidence accounting independent of FPGA and debug transports."""
import sys
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from mini_ops.ae350_ddr_suite import StressTimer
from modular import ae350
from modular.storage import Store
from ae350_report import verify_archive


class EvidenceTests(unittest.TestCase):
    def test_fragmented_markers_and_clipped_pauses(self):
        timer = StressTimer()
        timer.observe(b'STRESS BE', 9)
        timer.observe(b'GIN\r\n', 10)
        timer.observe(b'STRESS END\r\n', 20)
        value = timer.result([(8, 11), (13, 15), (19, 23), (25, 27)])
        self.assertEqual(value['wall_seconds'], 10)
        self.assertEqual(value['paused_seconds'], 4)
        self.assertEqual(value['running_seconds'], 6)

    def test_missing_end_is_unavailable(self):
        timer = StressTimer()
        timer.observe(b'STRESS BEGIN\r\n', 10)
        self.assertFalse(timer.result([])['available'])

    def test_repeated_buffer_does_not_move_start(self):
        timer = StressTimer()
        timer.observe(b'STRESS BEGIN\r\n', 10)
        timer.observe(b'progress', 11)
        timer.observe(b'STRESS END\r\n', 12)
        self.assertEqual(timer.result([])['running_seconds'], 2)

    def test_retired_monitor_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown AE350 profile'):
            ae350.firmware(None, None, None, profile='monitor')

    def test_archive_checks_source_and_complete_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root)
            artifact = store.publish('gateware', 'a'*64, {'image.fs': b'gateware'})
            payload = store.publish('payload', 'b'*64, {'app.bin': b'payload'})
            for name in ['source.zip', 'source.patch', 'README.md', 'evidence/host/result.json']:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b'preserved bytes')
            # The store's build-key indexes are included as ordinary files too.
            manifest = dict(artifact=artifact, payload_artifact=payload, complete=True,
                kind='candidate', source_commit='parent', source_dirty=True,
                files={p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in root.rglob('*') if p.is_file()})
            path = root / 'manifest.json'
            path.write_text(json.dumps(manifest))
            self.assertGreater(verify_archive(path, artifact, payload)['files'], 4)
            (root / 'extra.txt').write_text('unlisted')
            with self.assertRaisesRegex(ValueError, 'inventory'):
                verify_archive(path, artifact, payload)
            (root / 'extra.txt').unlink()
            (root / 'source.zip').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'differs: source.zip'):
                verify_archive(path, artifact, payload)


if __name__ == '__main__':
    unittest.main()
