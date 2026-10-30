"""Persistent work areas preserve caches but never publish failed outputs."""
import sys
import tempfile
import unittest
import stat
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from scripts.modular.storage import Store, Run, read, write, canonical, digest, workspace_lock
from scripts.modular.workspace import working, location, invalidate_pnr, gateware_files, cached_implementation, record_implementation, clean_workspaces
from scripts.modular.machines.mega138k_ae350.ae350 import incremental_seed, implementation_flow


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def run_record(self):
        return Run('test', 'workspace', root=self.store.root)

    def test_changed_key_keeps_path_and_cache(self):
        first = self.run_record()
        with working(self.store, first, 'machine', 'gateware', 'one') as work:
            path = work.path
            (path / 'cache').write_bytes(b'cache')
            work.success({'component': 'gateware', 'digest': 'a' * 64})
        with working(self.store, self.run_record(), 'machine', 'gateware', 'two') as work:
            self.assertEqual(path, work.path)
            self.assertEqual((path / 'cache').read_bytes(), b'cache')
            self.assertEqual(work.previous['input_key'], 'one')
            work.success()

    def test_failure_keeps_previous_success_and_marks_dirty(self):
        run = self.run_record()
        with working(self.store, run, 'machine', 'gateware', 'good') as work:
            work.success({'component': 'gateware', 'digest': 'a' * 64})
        with self.assertRaisesRegex(RuntimeError, 'failed'):
            with working(self.store, run, 'machine', 'gateware', 'bad') as work:
                raise RuntimeError('failed')
        state = read(work.path / 'workspace.json')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['last_success']['input_key'], 'good')

    def test_lock_and_named_area_isolation(self):
        run = self.run_record()
        default = location(self.store, run, 'machine', 'gateware')
        with workspace_lock(default):
            with self.assertRaisesRegex(ValueError, 'already in use'):
                with working(self.store, run, 'machine', 'gateware', 'key'):
                    pass
        run.result['workspace'] = 'experiment'
        self.assertNotEqual(default, location(self.store, run, 'machine', 'gateware'))
        self.assertNotEqual(location(self.store, run, 'machine', 'gateware'),
                            location(self.store, run, 'machine', 'gateware', fresh=True))
        run.result['workspace'] = '../escape'
        with self.assertRaises(ValueError):
            location(self.store, run, 'machine', 'gateware')

    def test_old_final_outputs_removed_but_compiler_cache_retained(self):
        work = self.store.build / 'work/test'
        for name in ('gateware/impl/pnr/project.fs', 'gateware/impl/pnr/project.tr',
                     'gateware/impl/pnr/project.pr', 'gateware/impl/gwsynthesis/project.vg',
                     'gateware/top.v'):
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test')
        invalidate_pnr(work)
        self.assertFalse((work / 'gateware/impl/pnr/project.fs').exists())
        self.assertTrue((work / 'gateware/impl/gwsynthesis/project.vg').exists())
        self.assertEqual([p.name for p in gateware_files(work, False)], ['top.v'])

    def test_readonly_bitstream_can_be_invalidated(self):
        work = self.store.build / 'work/test'
        path = work / 'gateware/impl/pnr/project.fs'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'old')
        path.chmod(stat.S_IREAD)
        invalidate_pnr(work)
        self.assertFalse(path.exists())

    def test_generated_only_success_keeps_implementation_baseline(self):
        run = self.run_record()
        ref = dict(component='gateware', digest='a' * 64)
        with working(self.store, run, 'machine', 'gateware', 'implemented') as work:
            work.success(ref, implementation=True)
        with working(self.store, run, 'machine', 'gateware', 'generated') as work:
            work.success(dict(component='gateware', digest='b' * 64))
        with working(self.store, run, 'machine', 'gateware', 'next') as work:
            self.assertEqual(work.implementation['artifact'], ref)
            work.success()

    def test_cleanup_rejects_active_work_and_preserves_archives(self):
        run = self.run_record()
        candidate = self.store.build / 'work/machine'
        with working(self.store, run, 'machine', 'gateware', 'active') as work:
            with self.assertRaisesRegex(ValueError, 'already in use'):
                clean_workspaces(self.store, [candidate])
            self.assertTrue(work.path.exists())
            work.success()
        archive = self.store.build / 'store/keep.bin'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'keep')
        clean_workspaces(self.store, [candidate])
        self.assertFalse(candidate.exists())
        self.assertEqual(archive.read_bytes(), b'keep')

    def artifact(self):
        board = dict(fpga=dict(part='GW5AST-test', device='GW5AST-138B'))
        config = dict(machine='mega138k_ae350', fpga=board['fpga'], synthesize=True, tools={'gowin': 'test'},
                      ddr_profile={}, core_hz=800, bus_hz=80, data_memory_clock_hz=100, input_key='inputs')
        ref = self.store.publish('gateware', digest(config), {
            'gateware/impl/pnr/project.p': b'place', 'gateware/impl/pnr/project.pr': b'route',
            'gateware/impl/pnr/project_tr_content.html':
                b'Numbers of Setup Violated Endpoints 0 Numbers of Hold Violated Endpoints 0',
            'gateware/impl/pnr/project.tr': b'MPW Summary: Slack: 0.1',
        }, config=config)
        return ref, board, config

    def test_seed_checks_compatibility_and_corruption(self):
        ref, board, config = self.artifact()
        seed = incremental_seed(self.store, {'artifact': ref}, config, board, 'auto')
        self.assertEqual(seed['mode'], 'pnr')
        self.assertIsNone(incremental_seed(self.store, {'artifact': ref}, dict(config, bus_hz=81), board, 'auto'))
        with self.assertRaisesRegex(ValueError, 'No compatible'):
            incremental_seed(self.store, None, config, board, 'place')
        self.assertIsNone(incremental_seed(self.store, None, config, board, 'full'))
        (self.store.path(ref) / seed['member']).write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'file differs'):
            incremental_seed(self.store, {'artifact': ref}, config, board, 'auto')

    def test_timing_failure_cannot_seed(self):
        ref, board, config = self.artifact()
        with patch('scripts.modular.machines.mega138k_ae350.ae350.timing_report', side_effect=ValueError('timing failed')):
            with self.assertRaisesRegex(ValueError, 'timing failed'):
                incremental_seed(self.store, {'artifact': ref}, config, board, 'auto')

    def test_ip_recipe_change_keeps_physical_configuration_gate(self):
        ref, board, config = self.artifact()
        changed = dict(config, ddr_profile={'ip_identity': {'ddr3': 'new-cli-recipe'}})
        seed = incremental_seed(self.store, {'artifact': ref}, changed, board, 'place')
        self.assertEqual(seed['mode'], 'place')
        self.assertTrue(seed['ip_recipe_changed'])
        for physical in ({'parameters': {'Memory_Clock': 200}}, {'geometry': {'data_width': 16}},
                         {'pll_parameters': {'CLKOUT2_FREQ': 200}}):
            with self.assertRaisesRegex(ValueError, 'No compatible'):
                incremental_seed(self.store, {'artifact': ref}, dict(changed, ddr_profile=physical), board, 'place')

    def test_vendor_fallback_is_reported_as_full_not_incremental_success(self):
        text = ('WARN  (PA2136) : Placement matching(58.68) is less than the threshold value(80) '
                'needed to run incremental flow, the default placement flow will be run.')
        flow = implementation_flow(text, dict(mode='place'))
        self.assertEqual(flow['actual'], 'full')
        self.assertEqual(flow['placement_match_percent'], 58.68)
        self.assertEqual(flow['minimum_match_percent'], 80)
        routing = ('WARN (PA2136) : Placement and routing matching(44.71%) is less than '
                   'the threshold value(80%) needed to run incremental flow, '
                   'the default placement and routing flow will be run.')
        routed = implementation_flow(routing, dict(mode='pnr'))
        self.assertEqual(routed['actual'], 'full')
        self.assertEqual(routed['placement_match_percent'], 44.71)
        self.assertEqual(routed['minimum_match_percent'], 80)
        self.assertEqual(implementation_flow('', None)['actual'], 'full')

    def test_success_cache_does_not_depend_on_changing_workspace_seed(self):
        ref, _, _ = self.artifact()
        # Rebuild index from immutable manifests; the execution seed may change
        # without turning an unchanged source request into another vendor run.
        self.assertEqual(cached_implementation(self.store, 'gateware', 'inputs'), ref)
        record_implementation(self.store, 'gateware', 'inputs', ref)
        with self.assertRaisesRegex(ValueError, 'different preserved'):
            record_implementation(self.store, 'gateware', 'inputs', dict(ref, digest='b' * 64))

    def test_backend_keeps_work_cache_and_archives_successful_deliveries(self):
        from scripts.modular.targets import backend
        args = SimpleNamespace(target='payload', output_dir=None, machine=None, source='first')
        def build(actual, run):
            (actual.output_dir / 'payload.bin').write_bytes(args.source.encode())
            (actual.output_dir / 'obj').mkdir(exist_ok=True)
            (actual.output_dir / 'obj/cache.o').write_bytes(b'cache')
        with patch('scripts.modular.targets.importlib.import_module', return_value=SimpleNamespace(build=build)):
            first = self.run_record()
            backend(self.store, first, args, False)
            ref = first.result['artifact']
            args.source = 'second'
            second = self.run_record()
            backend(self.store, second, args, False)
        self.assertEqual(first.result['output_dir'], second.result['output_dir'])
        self.assertNotEqual(ref, second.result['artifact'])
        self.assertEqual((self.store.path(ref) / 'outputs/payload.bin').read_bytes(), b'first')
        self.assertTrue((Path(second.result['output_dir']) / 'obj/cache.o').exists())
        self.assertNotIn('outputs/obj/cache.o', self.store.get(ref)['files'])

    def test_failed_backend_does_not_archive_or_overwrite_last_success(self):
        from scripts.modular.targets import backend
        args = SimpleNamespace(target='payload', output_dir=None, machine=None)
        run = self.run_record()
        with patch('scripts.modular.targets.importlib.import_module',
                   return_value=SimpleNamespace(build=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('failed')))):
            with self.assertRaises(RuntimeError):
                backend(self.store, run, args, False)
        self.assertNotIn('artifact', run.result)
        self.assertEqual(read(Path(run.result['output_dir']) / 'workspace.json')['status'], 'failed')

    def test_rebuilding_static_library_removes_obsolete_members(self):
        from scripts.modular.worker import perform
        archive = self.store.root / 'hal.a'
        archive.write_bytes(b'old object members')
        action = self.store.root / 'action.json'
        write(action, dict(argv=['ar', 'rcsD', str(archive)], cwd=str(self.store.root),
                           environment={}, remove_before=[str(archive)]))
        def invoke(*args, **kwargs):
            self.assertFalse(archive.exists())
            return SimpleNamespace(returncode=0)
        with patch('scripts.modular.worker.subprocess.run', side_effect=invoke):
            self.assertEqual(perform(['action', str(action)]), 0)


if __name__ == '__main__':
    unittest.main()
