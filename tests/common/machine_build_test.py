"""P0 input identity, hard-CPU dispatch, snapshot integrity and machine isolation."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from scripts.mini import parser
from scripts.modular import machines
from scripts.modular.machines.mega138k_ae350 import description as ae350_description
from scripts.modular.storage import Store, Run, read, write, digest
from scripts.modular.targets import execute
from scripts.modular.machines.primer20k_vexriscv.system import bundle, import_legacy
from scripts.modular.machines.primer20k_vexriscv.contracts import assembly
from scripts.modular.machines.primer20k_vexriscv.hardware import PRIMER20K_CLOSURE, generate_soc


class MachineBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'checkout'
        names = ('gateware/boards/mega138k_pro/board.json', 'gateware/machines/mega138k_ae350/machine.json',
                 'scripts/modular/machines/__init__.py', 'scripts/modular/machines/mega138k_ae350/description.py',
                 'scripts/modular/storage.py', 'scripts/modular/environment.py',
                 'scripts/modular/workspace.py', *PRIMER20K_CLOSURE)
        for name in names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = ROOT / name
            if source.is_file():
                shutil.copyfile(source, target)
            else:
                target.write_text('# placeholder\n', encoding='utf-8')
        self.store = Store(self.root)
        self.tools = mock.patch('scripts.modular.machines.mega138k_ae350.description.discover', return_value=dict(
            platform={'os': 'test', 'arch': 'test'}, tools={'python': {'sha': '1'}, 'gowin': {'sha': '2'}}, bindings={}))
        self.tools.start()
        self.addCleanup(self.tools.stop)
        from gateware.description import load_machine
        def resolved_ip(store, run, machine, plan=False):
            if plan: return None
            board, configuration = load_machine(machine, store.root)
            return dict(board=board, machine=configuration, artifacts={})
        ip = mock.patch('scripts.modular.ip.manager.ensure', side_effect=resolved_ip)
        ip.start()
        self.addCleanup(ip.stop)


    def args(self, target='machine-description', command='build', extra=()):
        return parser().parse_args([command, target, '--machine', machines.AE350, *extra])

    def run_target(self, target='machine-description', command='build', extra=()):
        run = Run('build', 'p0', root=self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            ref = execute(self.store, run, self.args(target, command, extra))
        return ref, run

    def test_real_hard_cpu_artifact_needs_no_soft_cpu_verilog(self):
        ref, run = self.run_target('cpu')
        data = self.store.get(ref, recursive=True)
        self.assertEqual(data['config']['kind'], 'hard')
        self.assertEqual(data['config']['cpu']['implementation'], 'ae350')
        self.assertEqual(set(data['files']), {'cpu.lock.json'})
        self.assertFalse(run.result['hardware_qualified'])
        self.assertFalse(run.result['board_identity_confirmed'])
        self.assertFalse((self.store.build / 'refs/current.json').exists())

    def test_payload_build_and_plan_use_only_machine_selection(self):
        artifact = dict(component='payload', digest='a' * 64)
        for command in ('build', 'plan'):
            with self.subTest(command=command), mock.patch(
                    'scripts.modular.machines.mega138k_ae350.ae350.build',
                    return_value=artifact) as backend:
                ref, _ = self.run_target('payload', command)
                self.assertEqual(ref, artifact)
                backend.assert_called_once()
                self.assertEqual(backend.call_args.args[2].target, 'payload')
                self.assertEqual(backend.call_args.args[-1], command == 'plan')


    def test_repeated_build_reuses_the_same_inputs(self):
        first, _ = self.run_target()
        second, run = self.run_target()
        self.assertEqual(first, second)
        self.assertEqual(run.result['steps'][0]['status'], 'REUSE')

    def test_ae350_eol_changes_reuse_and_cold_capture_the_same_artifact(self):
        first, _ = self.run_target()
        paths = [self.root / 'gateware/boards/mega138k_pro/board.json',
                 self.root / 'gateware/machines/mega138k_ae350/machine.json']
        for path in paths:
            path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        second, run = self.run_target()
        self.assertEqual(second, first)
        self.assertEqual(run.result['steps'][0]['status'], 'REUSE')
        with mock.patch.object(self.store, 'lookup', return_value=None):
            third, _ = self.run_target()
        self.assertEqual(third, first)
        self.store.get(first, recursive=True)

    def test_20k_hardware_key_ignores_eol(self):
        from scripts.modular.machines.primer20k_vexriscv.hardware import source_files
        before = source_files(self.root)
        for name in PRIMER20K_CLOSURE:
            path = self.root / name
            path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        self.assertEqual(source_files(self.root), before)

    def test_jtag_cannot_take_camera_pins(self):
        path = self.root / 'gateware/boards/mega138k_pro/board.json'
        board = json.loads(path.read_text())
        board['resources']['cpu_jtag']['signals']['tdi']['pins'] = ['E23']
        path.write_text(json.dumps(board))
        with self.assertRaisesRegex(ValueError, 'Pin E23 claimed'):
            ae350_description.resolve(self.store)

    def test_board_and_machine_inputs_invalidate_description(self):
        for path in (self.root / 'gateware/boards/mega138k_pro/board.json',
                     self.root / 'gateware/machines/mega138k_ae350/machine.json'):
            with self.subTest(path=path.name):
                before, _ = ae350_description.resolve(self.store)
                original = path.read_bytes()
                path.write_bytes(original + b'\n ')
                after, _ = ae350_description.resolve(self.store)
                self.assertNotEqual(digest(before), digest(after))
                path.write_bytes(original)

    def test_external_example_tree_does_not_affect_description(self):
        before, _ = ae350_description.resolve(self.store)
        example = Path(self.temporary.name) / 'external-example'
        example.mkdir()
        source = example / 'top.v'
        source.write_text('module top; endmodule\n')
        after, _ = ae350_description.resolve(self.store)
        self.assertEqual(before, after)
        source.write_text('module top; wire changed; endmodule\n')
        self.assertEqual(before, ae350_description.resolve(self.store)[0])

    def test_machine_description_snapshot_contains_only_local_inputs(self):
        ref, _ = self.run_target()
        data = self.store.get(ref, recursive=True)
        self.assertFalse(any(name.startswith('inputs/vendor/') for name in data['files']))
        self.assertFalse((self.store.path(ref) / 'inputs/vendor').exists())
        self.assertFalse(data['config']['evidence']['board_tested'])
        self.assertEqual(data['config']['device']['source'], 'board.json')

    def test_checkout_location_does_not_define_identity(self):
        before, _ = ae350_description.resolve(self.store)
        relocated = Path(self.temporary.name) / 'relocated'
        shutil.copytree(self.root, relocated)
        after, _ = ae350_description.resolve(Store(relocated))
        self.assertEqual(before, after)

    def test_tool_identity_changes_key(self):
        before, _ = ae350_description.resolve(self.store)
        with mock.patch('scripts.modular.machines.mega138k_ae350.description.discover', return_value=dict(platform={}, tools={'gowin': 'changed'})):
            after, _ = ae350_description.resolve(self.store)
        self.assertNotEqual(digest(before), digest(after))

    def test_illegal_clock_profiles_rejected(self):
        path = self.root / 'gateware/machines/mega138k_ae350/machine.json'
        original = read(path)
        for domain, value in (('sys', 200000000), ('data_memory_bus', 200000000), ('cpu', 800000001), ('unexpected', 1)):
            changed = json.loads(json.dumps(original))
            changed['clocks'][domain] = value
            write(path, changed)
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                ae350_description.resolve(self.store)
        write(path, original)

    def test_plan_marks_missing_rtl_backend_blocked(self):
        _, run = self.run_target('system', 'plan')
        self.assertTrue(run.result['plan'])
        self.assertEqual(run.result['steps'][-1]['status'], 'BUILD')
        self.assertFalse((self.store.build / 'store').exists())

    def test_unimplemented_targets_never_dispatch_to_legacy(self):
        for target in ('system', 'bios', 'hal-sdk'):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'pending P1'):
                self.run_target(target)


    def test_p0_cpu_cannot_be_assembled_as_system(self):
        cpu, _ = self.run_target('cpu')
        desc = self.store.get(cpu)['dependencies']['machine-description']
        with self.assertRaisesRegex(ValueError, 'P0 hard CPU'):
            assembly(self.store, {'cpu': cpu, 'machine-description': desc})

    def test_machine_refs_do_not_fallback_to_20k(self):
        old = self.store.build / 'refs/current.json'
        write(old, {'system': {'component': 'system', 'digest': '0' * 64}})
        before = old.read_bytes()
        with self.assertRaisesRegex(ValueError, 'No current reference'):
            machines.select_hardware(self.store, 'current', machines.AE350)
        new = machines.reference_path(self.store, 'current', machines.AE350)
        write(new, {'system': {'component': 'system', 'digest': '1' * 64}})
        self.assertEqual(machines.select_hardware(self.store, 'current', machines.AE350), str(new))
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(machines.reference_path(self.store, 'current'), old)

    def test_wrong_machine_exact_system_rejected_even_without_alias(self):
        run = Run('build', 'fixture', root=self.root)
        ref = bundle(self.store, {}, {'machine': machines.AE350}, {}, run)
        with self.assertRaisesRegex(ValueError, 'Machine mismatch'):
            import_legacy(self.store, ref['digest'], run)
        machines.check_system(self.store, ref, machines.AE350)

    def test_capture_race_does_not_publish(self):
        original = ae350_description.resolve
        calls = 0
        def changing(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                path = self.root / 'gateware/machines/mega138k_ae350/machine.json'
                path.write_bytes(path.read_bytes() + b'\n ')
            return original(*args, **kwargs)
        with mock.patch('scripts.modular.machines.mega138k_ae350.description.resolve', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                self.run_target()
        self.assertFalse((self.store.build / 'store').exists())

    def test_p1_targets_dispatch_to_ae350_backend(self):
        for target in ('bootloader', 'soc-description', 'gateware'):
            with mock.patch('scripts.modular.machines.mega138k_ae350.ae350.build') as build:
                self.run_target(target)
            self.assertEqual(build.call_args.args[2].target, target)
            cpu = build.call_args.args[3]
            self.assertEqual(self.store.get(cpu)['config']['kind'], 'hard')

    def test_138k_input_changes_do_not_invalidate_20k_but_20k_board_changes_do(self):
        cpu = self.store.publish('cpu', digest('legacy'), {'cpu.lock.json': b'{}'})
        def key():
            run = Run('build', 'legacy-plan', root=self.root)
            with mock.patch('scripts.modular.machines.primer20k_vexriscv.hardware.discover', return_value=dict(platform={}, tools={})), \
                 mock.patch('scripts.modular.machines.primer20k_vexriscv.hardware.python_packages', return_value={}), \
                 mock.patch.object(self.store, 'lookup', return_value=None) as lookup, \
                 contextlib.redirect_stdout(io.StringIO()):
                generate_soc(self.store, run, None, cpu, plan=True, validation={})
            return lookup.call_args.args[1]
        before = key()
        board = self.root / 'gateware/boards/mega138k_pro/board.json'
        board.write_bytes(board.read_bytes() + b'\n')
        implementation = self.root / 'gateware/machines/mega138k_ae350/soc.py'
        implementation.write_text('# 138K-only RTL implementation\n')
        self.assertEqual(before, key())
        legacy = self.root / 'gateware/boards/primer20k_dock3713/board.json'
        legacy.write_bytes(legacy.read_bytes() + b'\n')
        self.assertNotEqual(before, key())

    def test_hard_cpu_cannot_fall_through_to_vex_rtl_generator(self):
        cpu, _ = self.run_target('cpu')
        with self.assertRaisesRegex(ValueError, 'machine RTL backend'):
            generate_soc(self.store, Run('build', 'wrong-backend', root=self.root), None, cpu,
                         plan=True, validation={})


if __name__ == '__main__':
    unittest.main()
