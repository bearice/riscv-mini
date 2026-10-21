"""Protection and failure behavior of obsolete-output garbage collection."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.tools.build_cleanup import clean, plan


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'build').mkdir()

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def artifact(self, letter, dependencies=None):
        ref = dict(component='gateware', digest=letter * 64)
        self.put('build/store/gateware/' + ref['digest'] + '/manifest.json',
                 dict(dependencies=dependencies or {}))
        return ref

    def targets(self):
        return {Path(item['path']).relative_to(self.root).as_posix()
                for item in plan(self.root)['targets']}

    def test_pinned_dependency_closure_and_stale_indexes(self):
        dep = self.artifact('a')
        current = self.artifact('b', {'input': dep})
        dead = self.artifact('c')
        self.put('build/refs/current.json', current)
        self.put('build/index/dead.json', dead)
        self.put('build/refs/candidates/ae350/dead.json', dead)
        self.put('build/runs/old/result.json', dead)
        self.put('build/runs/live/result.json', current)
        targets = self.targets()
        self.assertEqual(targets, {'build/store/gateware/' + 'c' * 64,
                                  'build/index/dead.json', 'build/runs/old',
                                  'build/refs/candidates/ae350/dead.json'})
        clean(self.root, execute=True)
        self.assertTrue((self.root / 'build/store/gateware' / ('a' * 64)).exists())
        self.assertTrue((self.root / 'build/runs/live').exists())

    def test_inputs_archives_and_catalog_path_are_preserved(self):
        for name in ('doom-src', 'doom-assets', 'doom-old', 'doom-pinned', 'unknown'):
            self.put('build/' + name + '/data.json', {})
        self.put('build/catalog/pinned.json', dict(path=str(self.root / 'build/doom-pinned')))
        self.put('build/archives/final/release.json', {})
        self.put('build/old.log', {})
        self.assertEqual(self.targets(), {'build/doom-old', 'build/old.log'})
        clean(self.root)
        self.assertTrue((self.root / 'build/doom-old').exists())

    def test_document_evidence_and_git_worktree_are_preserved(self):
        run = '20261019T123456Z-board-test'
        self.put('build/runs/' + run + '/result.json', {})
        doc = self.root / 'docs/ae350-platform.md'
        doc.parent.mkdir()
        doc.write_text('Evidence: build/runs/' + run + '/', encoding='utf-8')
        (self.root / '.git').mkdir()
        self.put('build/doom-checkout/data.json', {})
        with patch('scripts.tools.build_cleanup.subprocess.run') as proc:
            proc.return_value.stdout = 'worktree ' + str(self.root / 'build/doom-checkout') + '\n'
            self.assertEqual(self.targets(), set())

    def test_corrupt_protected_manifest_stops_planning(self):
        ref = self.artifact('a')
        self.put('build/refs/current.json', ref)
        path = self.root / 'build/store/gateware' / ('a' * 64) / 'manifest.json'
        path.write_text('broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            plan(self.root)

    def test_invalid_reference_cannot_escape_build(self):
        self.put('build/refs/current.json', dict(component='../../outside', digest='a' * 64))
        with self.assertRaisesRegex(ValueError, 'Invalid artifact'):
            plan(self.root)

    def test_delete_failure_is_recorded_and_other_targets_continue(self):
        self.put('build/doom-denied/data.json', {})
        self.put('build/old.log', {})
        with patch('scripts.tools.build_cleanup.shutil.rmtree', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                clean(self.root, execute=True)
        result = json.loads((self.root / 'build/reports/cleanup/obsolete-result.json').read_text())
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['retained']), 1)
        self.assertFalse((self.root / 'build/old.log').exists())

    def test_nested_backend_components_follow_archive_roots(self):
        live = dict(component='backend/payload', digest='a' * 64)
        self.put('build/store/backend/payload/' + 'a' * 64 + '/manifest.json', dict(dependencies={}))
        self.put('build/store/backend/payload/' + 'b' * 64 + '/manifest.json', dict(dependencies={}))
        self.put('build/archives/test/receipt.json', dict(artifact=live))
        self.assertEqual(self.targets(), {'build/store/backend/payload/' + 'b' * 64})

    def test_gc_refuses_an_active_workspace(self):
        from scripts.modular.storage import Store, Run
        from scripts.modular.workspace import working
        store = Store(self.root)
        run = Run('test', 'active', root=self.root)
        self.put('build/old.log', {})
        with working(store, run, 'machine', 'target', 'inputs') as workspace:
            with self.assertRaisesRegex(ValueError, 'already in use'):
                clean(self.root, execute=True)
            self.assertTrue((self.root / 'build/old.log').exists())
            workspace.success()

    def test_successful_workspace_keeps_its_implementation_seed(self):
        ref = self.artifact('a')
        self.put('build/work/machine/gateware/default/workspace.json',
                 dict(last_success=dict(artifact=ref), last_implementation=dict(artifact=ref)))
        self.assertNotIn('build/store/gateware/' + 'a' * 64, self.targets())


if __name__ == '__main__':
    unittest.main(verbosity=2)
