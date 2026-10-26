"""Link real RISC-V objects across the XIP/ILM and physical Flash boundaries."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from gateware.machines.mega138k_ae350 import boot_contract
from scripts.modular.environment import discover
from gateware.description import resolve_machine
from gateware.machines.mega138k_ae350.address_map import AddressMap
ROM_SIZE = AddressMap(resolve_machine('mega138k_ae350', ROOT)).ROM_SIZE


class BootLinkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = discover(['target.cc', 'target.objcopy', 'target.readelf'], ROOT)['bindings']

    def link(self, filler_bytes, expected_size=None, section='rodata'):
        with tempfile.TemporaryDirectory(prefix='ae350-boot-link-') as directory:
            path = Path(directory)
            (path / 'ae350_boot_contract.ld').write_text(boot_contract.ld_fragment(ROM_SIZE))
            source = path / 'layout.S'
            flags = 'ax' if section == 'text' else 'a'
            filler = '.fill %d,4,0x13\n' % (filler_bytes//4) if section == 'text' else '.fill %d,1,0xa5\n' % filler_bytes
            source.write_text('.section .text.start,"ax"\n.globl _start\n_start: nop\n'
                '.section .text.extra,"ax"\nnop\n' +
                '.section .%s.large,"%s"\n' % (section,flags) + filler +
                '.section .ilm.text,"ax"\nnop\n')
            elf = path / 'layout.elf'
            linker = ROOT / 'firmware/bootloader/mega138k_ae350/link.ld'
            result = subprocess.run([*self.tools['target.cc'], '-march=rv32ima_zicsr_zifencei',
                '-mabi=ilp32', '-nostdlib', '-nostartfiles', '-Wl,--no-relax',
                '-Wl,-L,'+str(path), '-Wl,-T,'+str(linker), str(source), '-o', str(elf)],
                capture_output=True, text=True)
            if expected_size is None:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('overflow', result.stderr.lower())
                return
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            sections = subprocess.check_output([*self.tools['target.readelf'], '-SW', str(elf)], text=True)
            self.assertRegex(sections, r'\.text\s+PROGBITS\s+80000000')
            self.assertRegex(sections, r'\.ilm\s+PROGBITS\s+a0000000')
            binary = path / 'layout.bin'
            subprocess.run([*self.tools['target.objcopy'], '-O', 'binary', str(elf), str(binary)], check=True)
            self.assertEqual(binary.stat().st_size, expected_size)

    def test_code_and_constants_larger_than_ilm_use_xip(self):
        for section in ('text', 'rodata'):
            with self.subTest(section=section):
                self.link(80 << 10, (80 << 10) + 12, section=section)

    def test_exact_flash_end_links_and_overflow_is_rejected(self):
        self.link(ROM_SIZE - 12, ROM_SIZE)
        self.link(ROM_SIZE - 8)


if __name__ == '__main__': unittest.main()
