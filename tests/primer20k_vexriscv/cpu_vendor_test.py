"""The production CPU RTL is committed vendor IP and a declared build input."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from gateware.machines.primer20k_vexriscv.config import VENDOR_CPU_DIR, cpu_capabilities, vendored_cpu_path
from scripts.modular.machines.primer20k_vexriscv.cpu import locked_cpu
from scripts.modular.machines.primer20k_vexriscv.hardware import PRIMER20K_CLOSURE, source_closure
from scripts.modular.storage import file_hash, normalize_text_eol, text_file_hash


class CpuVendorTest(unittest.TestCase):
    def test_selected_variant_is_committed(self):
        path = vendored_cpu_path(True, False)
        self.assertEqual(path, VENDOR_CPU_DIR / 'VexRiscv_Mmu.v')
        self.assertTrue(path.is_file())

    def test_uncommitted_combinations_report_absent(self):
        self.assertIsNone(vendored_cpu_path(True, True))
        self.assertIsNone(vendored_cpu_path(False, True))

    def test_lock_validates_committed_rtl(self):
        path = vendored_cpu_path(True, False)
        lock = locked_cpu(path)
        self.assertEqual(lock['rtl_sha256'], text_file_hash(path))
        self.assertTrue(lock['variant']['mmu'])
        self.assertFalse(lock['variant']['fpu'])
        self.assertTrue(lock['variant']['single_cycle_shift'])
        self.assertTrue(lock['variant']['single_cycle_muldiv'])
        self.assertTrue(lock['high_mmio_xip'])
        self.assertIn('--outputFile VexRiscv_Mmu', lock['command'])

    def test_provenance_matches_artifact(self):
        record = json.loads((VENDOR_CPU_DIR / 'PROVENANCE.json').read_text(encoding='utf-8'))
        for name in ('VexRiscv_Mmu.v', 'VexRiscv_Mmu.yaml', 'LICENSE.VexRiscv'):
            self.assertEqual(record['files'][name]['hash_normalization'], 'lf')
            self.assertEqual(record['files'][name]['sha256'], text_file_hash(VENDOR_CPU_DIR / name), name)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / name
                lf = normalize_text_eol((VENDOR_CPU_DIR / name).read_bytes())
                for eol in (b'\n', b'\r\n', b'\r'):
                    path.write_bytes(lf.replace(b'\n', eol))
                    self.assertEqual(text_file_hash(path), record['files'][name]['sha256'])

    def test_vendor_reinstall_refreshes_stale_license_hash(self):
        from scripts.builders.cpu_generator import install_vendor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, vendor = root / 'generated', root / 'vendor'
            output.mkdir()
            vendor.mkdir()
            for name in ('VexRiscv_Mmu.v', 'VexRiscv_Mmu.yaml'):
                shutil.copyfile(VENDOR_CPU_DIR / name, output / name)
            shutil.copyfile(VENDOR_CPU_DIR / 'VexRiscv_Mmu.v', vendor / 'VexRiscv_Mmu.v')
            rtl = output / 'VexRiscv_Mmu.v'
            rtl.write_bytes(normalize_text_eol(rtl.read_bytes()).replace(b'\n', b'\r\n'))
            shutil.copyfile(VENDOR_CPU_DIR / 'LICENSE.VexRiscv', vendor / 'LICENSE.VexRiscv')
            license = vendor / 'LICENSE.VexRiscv'
            license.write_bytes(normalize_text_eol(license.read_bytes()).replace(b'\n', b'\r\n'))
            provenance = json.loads((VENDOR_CPU_DIR / 'PROVENANCE.json').read_text())
            provenance['files']['LICENSE.VexRiscv']['sha256'] = 'stale-before-reinstall'
            (vendor / 'PROVENANCE.json').write_text(json.dumps(provenance))
            report = json.loads((VENDOR_CPU_DIR / 'generator.json').read_text())
            install_vendor(report, output, vendor, SimpleNamespace(vexriscv_source=root))
            updated = json.loads((vendor / 'PROVENANCE.json').read_text())
            self.assertEqual(updated['files']['LICENSE.VexRiscv']['sha256'],
                             text_file_hash(license))
            self.assertEqual(updated['files']['LICENSE.VexRiscv']['hash_normalization'], 'lf')
            self.assertEqual(text_file_hash(rtl), locked_cpu(vendor / rtl.name)['rtl_sha256'])

    def test_capabilities_come_from_the_lock(self):
        self.assertEqual(cpu_capabilities(vendored_cpu_path(True, False)),
                         dict(mmu=True, fpu=False, dcache=True, compressed=False, bitmanip=[],
                              high_mmio_xip=True))

    def test_closure_declares_the_cpu(self):
        declared = set(PRIMER20K_CLOSURE)
        for name in ('gateware/vendor/vexriscv/VexRiscv_Mmu.v',
                     'gateware/vendor/vexriscv/VexRiscv_Mmu.yaml',
                     'gateware/vendor/vexriscv/generator.json'):
            self.assertIn(name, declared)
        self.assertFalse([name for name in source_closure(ROOT) if not (ROOT / name).is_file()])

    def test_generator_lock_records_only_the_selected_variant(self):
        data = json.loads((VENDOR_CPU_DIR / 'generator.json').read_text(encoding='utf-8'))
        self.assertEqual(list(data['cpu_rtls']), ['VexRiscv_Mmu'])
        self.assertEqual(len(data['commands']), 1)
        self.assertIn('--outputFile VexRiscv_Mmu', data['commands'][0])
        self.assertIn('--singleCycleShift true', data['commands'][0])
        self.assertIn('--singleCycleMulDiv true', data['commands'][0])

    def test_cpu_lock_ignores_eol_but_detects_content_changes(self):
        original = VENDOR_CPU_DIR / 'VexRiscv_Mmu.v'
        expected = locked_cpu(original)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / original.name
            shutil.copyfile(VENDOR_CPU_DIR / 'generator.json', path.parent / 'generator.json')
            lf = normalize_text_eol(original.read_bytes())
            for eol in (b'\n', b'\r\n', b'\r'):
                path.write_bytes(lf.replace(b'\n', eol))
                self.assertEqual(locked_cpu(path), expected)
            path.write_bytes(lf + b'// content change\n')
            with self.assertRaisesRegex(ValueError, 'differs from generator metadata'):
                locked_cpu(path)

    def test_text_hash_preserves_whitespace_and_final_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.txt'
            path.write_bytes(b'line\n')
            expected = text_file_hash(path)
            raw_lf = file_hash(path)
            path.write_bytes(b'line\r\n')
            self.assertEqual(text_file_hash(path), expected)
            self.assertNotEqual(file_hash(path), raw_lf)
            for changed in (b'line', b'line \n', b'line\n\n'):
                path.write_bytes(changed)
                self.assertNotEqual(text_file_hash(path), expected)

    def test_generation_parameters_are_recorded(self):
        data = json.loads((VENDOR_CPU_DIR / 'generator.json').read_text(encoding='utf-8'))
        self.assertEqual(data['fixed_parameters']['icache_bytes'], 2048)
        self.assertEqual(data['fixed_parameters']['dcache_bytes'], 2048)
        self.assertEqual(data['fixed_parameters']['csr_permission_patch'], 'time/TM')
        self.assertTrue(data['high_mmio_xip'])
        self.assertTrue(data['pipeline']['two_cycle_icache'])
        self.assertTrue(data['pipeline']['injector_register'])
        self.assertEqual(data['resolved_flags'], dict(relaxed_pc_calculation=False, compressed=False,
                                                     pipelined_fetch=True, arithmetic_matrix=False))
        self.assertIn('1.8.', data['source_lock']['java_version'])
        for key in ('generator_script_sha256', 'template_sha256', 'java_binary_sha256',
                    'sbt_launcher_sha256', 'requirements_sha256'):
            self.assertEqual(len(data['source_lock'][key]), 64, key)
        self.assertTrue(data['source_lock']['upstream_files'])


if __name__ == '__main__':
    unittest.main()
