"""Commit qualification consumes real artifact identities, not relabeled legacy records."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from scripts.modular.storage import Store, source_file_hash, SOURCE_HASH_FORMAT
from scripts.tools.prepare_commit import selected_build


class ModularCommitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'firmware').mkdir()
        self.source = self.root / 'firmware/example.c'
        self.source.write_bytes(b'qualified source\n')
        sources = {'inputs/source/firmware/example.c': source_file_hash(self.source)}
        store = Store(self.root)
        self.hardware = store.publish('gateware', 'a'*64,
            {'gateware/impl/pnr/project.fs': b'bitstream'},
            config=dict(machine='mega138k_ae350', synthesize=True, sources=sources, source_hash_format=SOURCE_HASH_FORMAT))
        self.payload = store.publish('payload', 'b'*64, {'outputs/app.bin': b'payload'},
            config=dict(binary='outputs/app.bin', sources=sources, source_hash_format=SOURCE_HASH_FORMAT))
        self.receipt = dict(passed=True, artifact=self.hardware, payload_artifact=self.payload,
            fs_sha256=hashlib.sha256(b'bitstream').hexdigest(),
            payload_sha256=hashlib.sha256(b'payload').hexdigest(),
            steps=[dict(name='results', checks=1, failures=0, registers={'mcause':'0x0'})])
        self.path = self.root / 'receipt.json'

    def tearDown(self):
        self.temporary.cleanup()

    def qualify(self):
        self.path.write_text(json.dumps(self.receipt))
        with patch('scripts.modular.machines.mega138k_ae350.description.timing_report', return_value={'setup':0,'hold':0}), \
             patch('scripts.tools.prepare_commit.source_identity', return_value={'inputs_sha256':'current'}):
            return selected_build(str(self.path), self.root)

    def test_exact_artifacts_and_sources_qualify(self):
        result = self.qualify()
        self.assertEqual(result['artifact'], self.hardware)
        self.assertEqual(result['source']['kind'], 'modular-captured')

    def test_changed_source_cannot_reuse_board_evidence(self):
        self.source.write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'Qualified source differs'):
            self.qualify()

    def test_source_eol_change_preserves_qualification(self):
        self.source.write_bytes(b'qualified source\r\n')
        self.assertEqual(self.qualify()['artifact'], self.hardware)

    def test_wrong_binary_hash_is_rejected(self):
        self.receipt['fs_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'binary identities'):
            self.qualify()

    def test_trapped_execution_is_rejected(self):
        self.receipt['steps'][0]['registers']['mcause'] = '0x5'
        with self.assertRaisesRegex(ValueError, 'executed diagnostic'):
            self.qualify()

    def test_dry_run_is_not_board_evidence(self):
        self.receipt['dry_run'] = True
        with self.assertRaisesRegex(ValueError, 'non-dry-run'):
            self.qualify()


if __name__ == '__main__':
    unittest.main()
