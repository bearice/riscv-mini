"""The production CPU RTL is committed vendor IP and a declared build input."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from gateware.machines.primer20k_vexriscv.config import VENDOR_CPU_DIR, cpu_capabilities, vendored_cpu_path
from scripts.modular.machines.primer20k_vexriscv.cpu import locked_cpu
from scripts.modular.machines.primer20k_vexriscv.hardware import PRIMER20K_CLOSURE, source_closure
from scripts.modular.storage import file_hash


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
        self.assertEqual(lock['rtl_sha256'], file_hash(path))
        self.assertTrue(lock['variant']['mmu'])
        self.assertFalse(lock['variant']['fpu'])
        self.assertTrue(lock['variant']['single_cycle_shift'])
        self.assertTrue(lock['variant']['single_cycle_muldiv'])
        self.assertTrue(lock['high_mmio_xip'])
        self.assertIn('--outputFile VexRiscv_Mmu', lock['command'])

    def test_provenance_matches_artifact(self):
        record = json.loads((VENDOR_CPU_DIR / 'PROVENANCE.json').read_text(encoding='utf-8'))
        for name in ('VexRiscv_Mmu.v', 'VexRiscv_Mmu.yaml', 'LICENSE.VexRiscv'):
            self.assertEqual(record['files'][name]['sha256'], file_hash(VENDOR_CPU_DIR / name), name)

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

    def test_committed_rtl_bytes_are_line_ending_stable(self):
        """The recorded sha256 must survive checkout: the RTL is committed -text."""
        data = (VENDOR_CPU_DIR / 'VexRiscv_Mmu.v').read_bytes()
        self.assertNotIn(b'\r\n', data)

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
