"""Public build command contract, backend dispatch and removed-entry regression."""
import ast
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from scripts.mini import parser
from scripts.modular.targets import CORE,BACKENDS,execute
from scripts.modular.storage import Store,Run


class BuildEntrypointTests(unittest.TestCase):
    def test_build_and_plan_share_all_registered_targets(self):
        for command in ('build','plan'):
            for target in (*CORE,*BACKENDS):
                argv=[command,target]
                if target=='cpu-gcb':argv+=['--base-generator','base','--plugins','plugins','--java','java','--sbt-launch','sbt']
                if target=='cpu-qualification':argv+=['--cpu-verilog','CPU.v']
                args=parser().parse_args(argv)
                self.assertEqual((args.command,args.target),(command,target))
                self.assertEqual(args.hardware,'current')
                self.assertIsNone(args.output_dir)

    def test_removed_commands_are_not_available(self):
        removed=('build.py','modular_build.py','rtl_build.py','build_matrix.py','cpu_generate.py',
                 'cpu_gcb_generate.py','cpu_qualify.py','linux_build.py','busybox_build.py',
                 'uboot_build.py','opensbi_build.py','bios_payload.py','doom_image.py','gwbasic_image.py',
                 'nyancat_assets.py','release.py','builds.py','run_linux_build.py',
                 'linux_build.ps1','busybox_build.ps1','opensbi_build.ps1','mini_ops/build.py')
        for name in removed:self.assertFalse((ROOT/'scripts'/name).exists(),name)

    def test_backends_have_no_second_cli(self):
        for path in (ROOT/'scripts/builders').glob('*.py'):
            source=path.read_text(encoding='utf-8');tree=ast.parse(source)
            self.assertNotIn('parse_args(',source,path.name)
            self.assertNotIn('ArgumentParser(',source,path.name)
            self.assertNotIn("__name__",source,path.name)
            if path.stem!='__init__':
                build=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build')
                self.assertEqual(build.args.args[-1].arg,'run')

    def test_old_build_forms_and_stage_switches_rejected(self):
        commands=(['build','--minimal'],['build','--from','current'],['build','system','--with-audio'],
                  ['build','linux','--record'],['build','uboot','--prepare-only'],
                  ['build','opensbi','--pack-only'],['export','current'],['release','--build','current'])
        for argv in commands:
            with self.subTest(argv=argv),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                parser().parse_args(argv)

    def test_backend_plan_cannot_download_or_execute(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            store=Store(Path(folder));run=Run('build','plan',root=store.root)
            args=parser().parse_args(['plan','linux'])
            with mock.patch('scripts.modular.targets.importlib.import_module',side_effect=AssertionError('backend executed')):
                execute(store,run,args)
            self.assertTrue(run.result['passed']);self.assertTrue(run.result['plan'])
            self.assertFalse(Path(run.result['output_dir']).exists())

    def test_backend_receives_the_same_request_and_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            store=Store(Path(folder));run=Run('build','payload',root=store.root)
            args=parser().parse_args(['build','payload','--soft-float'])
            backend=mock.Mock()
            with mock.patch('scripts.modular.targets.importlib.import_module',return_value=backend):execute(store,run,args)
            request=backend.build.call_args.args[0]
            self.assertTrue(request.soft_float)
            self.assertEqual(backend.build.call_args.kwargs['run'],run)
            self.assertTrue((request.output_dir/'build.lock.json').is_file())

    def test_generator_options_cannot_be_silently_ignored(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            store=Store(Path(folder));run=Run('build','cpu',root=store.root)
            args=parser().parse_args(['build','cpu','--compressed'])
            with self.assertRaisesRegex(ValueError,'require --generate'):execute(store,run,args)

    def test_one_upstream_command_owns_prepare_execute_and_record(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            store=Store(Path(folder));run=Run('build','linux',root=store.root)
            args=parser().parse_args(['build','linux'])
            backend=mock.Mock();phases=[]
            def build(request,run):
                phases.append(request.record)
                (request.output_dir/'build.sh').write_text('exit 0\n')
            backend.build.side_effect=build
            with mock.patch('scripts.modular.targets.importlib.import_module',return_value=backend), \
                 mock.patch('scripts.modular.executor.execute') as executor:
                execute(store,run,args)
            self.assertEqual(phases,[False,True])
            self.assertEqual(executor.call_count,1)
            self.assertIs(executor.call_args.kwargs['run'],run)
            self.assertTrue(run.result['passed'])


if __name__=='__main__':unittest.main()
