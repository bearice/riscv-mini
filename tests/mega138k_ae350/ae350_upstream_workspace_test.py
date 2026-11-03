"""AE350 upstream outputs obey workspace selection and survive failed retries."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
from scripts.modular.storage import Store, Run, read
from scripts.modular.machines.mega138k_ae350 import opensbi, linux


class UpstreamWorkspaceTests(unittest.TestCase):
    def test_build_archives_outputs_and_retry_preserves_named_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            source = store.build/'vendor/opensbi'
            (source/'firmware').mkdir(parents=True)
            (source/'firmware/fw_base.S').write_text('source')
            dts = store.root/'firmware/dts/mega138k_ae350.dts'
            dts.parent.mkdir(parents=True)
            dts.write_text('device tree')
            config = store.root/'firmware/opensbi/ae350/opensbi.config'
            config.parent.mkdir(parents=True)
            config.write_text('configuration')
            (config.parent/'source.json').write_text('{"version": "7a7a60dc87a41c79fb63d5972943e9d8fe4abf77"}')
            args = SimpleNamespace(source=source, target='opensbi', output_dir=None)
            makes = []
            listing = ['01000000 <_start>:\n 1000000: \tauipc\ta0, 0x0\n']
            def record():
                run = Run('test', 'upstream', root=store.root)
                run.result['workspace'] = 'default'
                return run
            def native(command, **kwargs):
                if command[0] == 'llvm-nm':
                    return SimpleNamespace(stdout='01000000 T _fw_start\n01040000 B _fw_end\n')
                if command[0] == 'llvm-objdump':
                    return SimpleNamespace(stdout=listing[0])
                if command[:5] == ['dtc', '-I', 'dtb', '-O', 'dts']:
                    return SimpleNamespace(stdout='/ {\n};\n')
                if command[0] in ('dtc', 'clang'):
                    Path(command[command.index('-o')+1]).write_bytes(b'dtb')
                else:
                    makes.append(command[-1])
                    self.assertIn('OPENSBI_VERSION_GIT=7a7a60dc87a4-ae350', command)
                    self.assertIn('PLATFORM_RISCV_ISA=rv32imfd_zicsr_zifencei', command)
                    out = Path(next(x[2:] for x in command if x.startswith('O=')))
                    binary = out/'platform/generic/firmware/fw_jump.bin'
                    binary.parent.mkdir(parents=True, exist_ok=True)
                    binary.write_bytes(b'firmware')
            first = record()
            with patch.object(opensbi, 'prepare', return_value=source), patch.object(opensbi.subprocess, 'run', side_effect=native):
                opensbi.build(store, first, args, False)
            work = store.build/'work/mega138k_ae350/opensbi/default'
            self.assertTrue((work/'obj/platform/generic/firmware/fw_jump.bin').is_file())
            self.assertEqual(makes, ['clean', '-j8'])
            self.assertEqual(read(work/'workspace.json')['status'], 'succeeded')
            delivery = Path(first.result['binary'])
            self.assertTrue(delivery.is_relative_to(store.build/'store'))
            (work/'obj/compiler-cache').write_bytes(b'keep')
            second = record()
            with patch.object(opensbi, 'prepare', return_value=source), patch.object(opensbi.subprocess, 'run', side_effect=RuntimeError('build failed')):
                with self.assertRaisesRegex(RuntimeError, 'build failed'):
                    opensbi.build(store, second, args, False)
            state = read(work/'workspace.json')
            self.assertEqual(state['status'], 'failed')
            self.assertEqual(state['last_success']['artifact'], first.result['artifact'])
            self.assertEqual((work/'obj/compiler-cache').read_bytes(), b'keep')
            self.assertEqual(delivery.read_bytes(), b'firmware')
            self.assertFalse((store.build/'opensbi-ae350').exists())
            makes.clear()
            with patch.object(opensbi, 'prepare', return_value=source), patch.object(opensbi.subprocess, 'run', side_effect=native):
                opensbi.build(store, record(), args, False)
            self.assertEqual(makes, ['-j8'])
            listing[0] += ' 1000004: \tamoor.w\ta0, a1, (a2)\n'
            with patch.object(opensbi, 'prepare', return_value=source), patch.object(opensbi.subprocess, 'run', side_effect=native):
                with self.assertRaisesRegex(ValueError, 'amoor.w'):
                    opensbi.build(store, record(), args, False)


    def test_linux_external_source_does_not_choose_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            source = store.root/'external/kernel'
            names = ('Makefile', 'arch/riscv/Makefile', 'arch/riscv/kernel/entry.S',
                     'arch/riscv/include/asm/atomic.h', 'arch/riscv/include/asm/cmpxchg.h',
                     'arch/riscv/include/asm/bitops.h', 'arch/riscv/include/asm/futex.h',
                     'arch/riscv/include/asm/tlbflush.h')
            for name in names:
                p = source/name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('configured source')
            for name in ('init.c', 'install.py', 'ae350/linux.config', 'ae350/patches/10-reserved-kernel-va.patch', 'ae350/source.json'):
                p = store.root/'firmware/linux'/name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('port input')
            (store.root/'firmware/linux/ae350/linux.config').write_text(
                'CONFIG_32BIT=y\nCONFIG_MMU=y\nCONFIG_PAGE_OFFSET=0xc0000000\n'
                'CONFIG_RISCV_RESERVED_VA_START=0xa0000000\nCONFIG_RISCV_RESERVED_VA_SIZE=0x210000\n')
            work = store.build/'work/mega138k_ae350/linux/default'
            obj = work/'obj'
            obj.mkdir(parents=True)
            (obj/'.config').write_text('CONFIG_SMP=n\nCONFIG_FPU=n\n')
            (obj/'uapi/include/linux').mkdir(parents=True)
            (obj/'uapi/include/linux/types.h').write_text('types')
            def native(command, **kwargs):
                if command[0] == 'clang':
                    Path(command[command.index('-o')+1]).write_bytes(b'init')
                else:
                    self.assertIn('O='+str(obj), command)
                    image = obj/'arch/riscv/boot/Image'
                    image.parent.mkdir(parents=True, exist_ok=True)
                    image.write_bytes(b'kernel')
            args = SimpleNamespace(source=source, target='linux', output_dir=None)
            run = Run('test', 'kernel-workspace', root=store.root)
            with patch.object(linux, 'prepare', return_value=source), patch.object(linux.subprocess, 'run', side_effect=native):
                linux.build(store, run, args, False)
            self.assertEqual(read(work/'workspace.json')['status'], 'succeeded')
            self.assertFalse((source.parent/'obj-minimal').exists())
            self.assertEqual(Path(run.result['binary']).read_bytes(), b'kernel')


if __name__ == '__main__':
    unittest.main()
