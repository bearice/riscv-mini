"""AE350 DDR ownership, OS handoff and device-tree placement follow address_map.py."""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
from gateware.description import resolve_machine
from gateware.machines.mega138k_ae350.address_map import AddressMap
from scripts.modular.machines.mega138k_ae350 import linux, opensbi
from scripts.mini_ops.mega138k_ae350 import linux as ae350_linux
from scripts.mini_ops.mega138k_ae350.linux import check_banner
from unittest.mock import patch


def layout():
    return AddressMap(resolve_machine('mega138k_ae350'))


class SoftwareLayoutTests(unittest.TestCase):
    def test_contract_is_consistent(self):
        self.assertEqual(layout().check(), [])

    def test_overlap_in_a_shared_phase_is_rejected(self):
        m = layout()
        m.DDR_LAYOUT = m.DDR_LAYOUT + (('probe.scratch', m.OS_DTB_BASE, 0x1000, ('bios',)),)
        self.assertTrue(any('probe.scratch' in e and 'bios phase' in e for e in m.check()))

    def test_ranges_in_disjoint_phases_may_share_addresses(self):
        m = layout()
        live = {name: phases for (name, _, _, phases) in m.DDR_LAYOUT}
        self.assertFalse(set(live['bios.app']) & set(live['os.sbi']))
        self.assertEqual(m.check(), [])

    def test_local_memory_must_be_inside_reserved_kernel_va(self):
        m = layout()
        m.OS_RESERVED_VA_BYTES = m.DLM_BASE - m.ILM_BASE
        self.assertIn('dlm window overlaps Linux vmalloc outside the reserved kernel VA', m.check())
        m = layout()
        m.OS_PAGE_OFFSET = 0xa0000000
        errors = m.check()
        self.assertIn('ilm window overlaps Linux linear outside the reserved kernel VA', errors)
        self.assertIn('reserved kernel VA must be a page range inside Linux vmalloc', errors)

    def test_handoff_placement_rules(self):
        m = layout()
        m.OS_KERNEL_BASE = m.OS_KERNEL_BASE + 0x1000
        self.assertIn('kernel load address is not Sv32 megapage aligned', m.check())
        m = layout()
        m.OS_MEMORY_BYTES = m.OS_DTB_BASE
        self.assertIn('os.dtb is outside OS memory', m.check())

    def test_shared_bios_application_window_matches(self):
        text = (ROOT/'firmware/bios/include/bios.h').read_text()
        value = lambda name: int(re.search(r'#define %s (0x[0-9a-fA-F]+)u' % name, text)[1], 16)
        m = layout()
        self.assertEqual((value('BIOS_PAYLOAD_BASE'), value('BIOS_PAYLOAD_LIMIT')),
                         (m.BIOS_APP_BASE, m.BIOS_APP_LIMIT))

    def test_payload_bss_is_a_contract_region(self):
        m = layout()
        self.assertIn(' bss (rw) : ORIGIN = 0x%x, LENGTH = 0x%x' % (m.BIOS_BSS_BASE, m.BIOS_BSS_BYTES),
                      m.ld_fragment())
        linker = (ROOT/'firmware/bsp/mega138k_ae350/runtime/link.ld').read_text()
        self.assertIn('> bss', linker)
        self.assertNotRegex(linker, r'ORIGIN\s*=\s*0x')
        m.BIOS_BSS_BASE = m.OS_KERNEL_BASE
        m.DDR_LAYOUT = tuple(item if item[0] != 'bios.bss' else ('bios.bss', m.OS_KERNEL_BASE) + item[2:]
                             for item in m.DDR_LAYOUT)
        self.assertIn('bios.bss overlaps os.kernel in bios phase', m.check())

    def test_diagnostic_scratch_is_checked_against_payload_ranges(self):
        m = layout()
        m.DDR_LAYOUT = tuple(item if item[0] != 'diag.dma' else ('diag.dma', m.DDR_CODE) + item[2:]
                             for item in m.DDR_LAYOUT)
        self.assertIn('bios.image overlaps diag.dma in diag phase', m.check())
        m = layout()
        m.DDR_LAYOUT = tuple(item if item[0] != 'diag.io' else ('diag.io', m.DIAG_DMA_BASE) + item[2:]
                             for item in m.DDR_LAYOUT)
        self.assertIn('diag.io overlaps diag.dma in diag phase', m.check())

    def test_diagnostic_sources_take_addresses_from_the_contract(self):
        m = layout()
        bases = {m.UART_BASE, m.SFP_BASE, m.USB_BASE, m.ETH_BASE, m.DMAC_BASE, m.VIDEO_BASE}
        literal_pointer = re.compile(r'\*\s*\)\s*\(?\s*0x[0-9a-fA-F]+')
        literal_base = re.compile(r'0x0*(%s)u?\b' % '|'.join('%x' % b for b in bases), re.I)
        sources = [p for d in ('firmware/diagnostics/mega138k_ae350', 'firmware/examples/mega138k_ae350')
                   for p in sorted((ROOT/d).rglob('*')) if p.suffix in ('.c', '.h')]
        self.assertTrue(sources)
        for path in sources:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                for pattern in (literal_pointer, literal_base):
                    self.assertIsNone(pattern.search(line), '%s:%d: %s' % (path.relative_to(ROOT), number, line.strip()))

    def test_bios_scanout_uses_contract(self):
        text = (ROOT/'firmware/bsp/mega138k_ae350/bios/services.c').read_text()
        self.assertIn('ae350_video_init((uint16_t *)AE350_BIOS_SCANOUT0,(uint16_t *)AE350_BIOS_SCANOUT1', text)

    def test_linux_config_matches_virtual_layout(self):
        linux.check_config((ROOT/'firmware/linux/ae350/linux.config').read_text())
        good = 'CONFIG_32BIT=y\nCONFIG_MMU=y\nCONFIG_PAGE_OFFSET=0xc0000000\n' \
               'CONFIG_RISCV_RESERVED_VA_START=0xa0000000\nCONFIG_RISCV_RESERVED_VA_SIZE=0x210000\n'
        linux.check_config(good)
        with self.assertRaisesRegex(ValueError, 'PAGE_OFFSET'):
            linux.check_config(good.replace('0xc0000000', '0x80000000'))
        with self.assertRaisesRegex(ValueError, 'RESERVED_VA'):
            linux.check_config(good.replace('=0x210000', '=0x10000'))

    def test_opensbi_banner_must_report_contract_addresses(self):
        m = layout()
        banner = ('Firmware Base               : 0x1000000\n'
                  'Firmware Size               : 317 KB\n'
                  'Domain0 Next Address        : 0x00400000\n'
                  'Domain0 Next Arg1           : 0x03200000\n')
        self.assertEqual(check_banner(banner, m)['firmware_bytes'], 317 * 1024)
        with self.assertRaisesRegex(RuntimeError, 'os.kernel/os.dtb'):
            check_banner(banner.replace('0x03200000', '0x01f00000'), m)
        with self.assertRaisesRegex(RuntimeError, 'os.sbi'):
            check_banner(banner.replace('317 KB', '2048 KB'), m)
        payload = banner.replace('0x00400000', '0x%08x' % m.OS_UBOOT_BASE).replace(
            '0x03200000', '0x%08x' % m.OS_FIRMWARE_FDT_BASE)
        self.assertEqual(check_banner(payload, m, uboot=True)['next_address'], m.OS_UBOOT_BASE)
        with self.assertRaisesRegex(RuntimeError, 'os.uboot/os.firmware-fdt'):
            check_banner(banner, m, uboot=True)

    def test_uboot_payload_follows_opensbi(self):
        m = layout()
        self.assertEqual(m.OS_UBOOT_BASE, m.OS_SBI_BASE + m.OS_SBI_BYTES)
        self.assertEqual(m.software_layout()['os']['uboot'], [m.OS_UBOOT_BASE, m.OS_UBOOT_BYTES])
        m.OS_UBOOT_BASE += 0x1000
        self.assertTrue(any('os.uboot must follow os.sbi' in e for e in m.check()))

    def test_opensbi_image_kind_follows_the_os_sbi_size(self):
        m = layout()
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp)/'image.bin'
            image.write_bytes(b'\0' * m.OS_SBI_BYTES)
            self.assertEqual(ae350_linux.opensbi_image(image, m), ('fw_jump.bin', m.OS_SBI_BYTES))
            image.write_bytes(b'\0' * (m.OS_SBI_BYTES + 4))
            self.assertEqual(ae350_linux.opensbi_image(image, m),
                             ('fw_payload.bin', m.OS_SBI_BYTES + m.OS_UBOOT_BYTES))

    def test_boot_loads_come_from_bios_and_uboot_evidence(self):
        m = layout()
        text = ('LOAD OK fw_payload.bin bytes=1310720 crc=0badf00d\r\n'
                '4096 bytes read in 3 ms\r\n'
                'CRC32 for %08x ... %08x ==> 12345678\r\n'
                'CRC32 for %08x ... %08x ==> 9abcdef0\r\n'
                'CRC32 for 01000000 ... 01000fff ==> 11111111\r\n') % (
            m.OS_KERNEL_BASE, m.OS_KERNEL_BASE + 4095, m.OS_DTB_BASE, m.OS_DTB_BASE + 15)
        self.assertEqual(ae350_linux.loaded(text, m), {
            'fw_payload.bin': dict(bytes=1310720, crc32='0badf00d'),
            'Image': dict(bytes=4096, crc32='12345678'),
            'mega138k_ae350.dtb': dict(bytes=16, crc32='9abcdef0')})
        with self.assertRaisesRegex(RuntimeError, 'Image'):
            ae350_linux.check_loaded(text.encode(), m, [('Image', b'x' * 4096)])

    def test_board_address_is_in_the_host_subnet(self):
        listing = SimpleNamespace(stdout='[{"addr_info":[{"local":"192.168.1.1","prefixlen":24},'
                                         '{"local":"169.254.52.166","prefixlen":16}]}]')
        with patch.object(ae350_linux.subprocess, 'run', return_value=listing):
            self.assertEqual(ae350_linux.board_address('192.168.1.1'), '192.168.1.20')
            self.assertEqual(ae350_linux.board_address('192.168.1.1', '192.168.1.7'), '192.168.1.7')
            with self.assertRaisesRegex(ValueError, 'not a free host'):
                ae350_linux.board_address('192.168.1.1', '169.254.20.20')
            with self.assertRaisesRegex(ValueError, 'not configured'):
                ae350_linux.board_address('10.0.0.1')

    @unittest.skipUnless(shutil.which('dtc') and shutil.which('clang'), 'dtc/clang not in PATH')
    def test_device_tree_uses_contract_and_checks_unit_addresses(self):
        m = layout()
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            with open(work/'log', 'w') as log:
                dtb = opensbi.compile_dtb(SimpleNamespace(root=ROOT), m, work, log)
            text = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', str(dtb)],
                                  check=True, capture_output=True, text=True).stdout
            self.assertIn('reg = <0x00 0x%x>;' % m.OS_MEMORY_BYTES, text)
            # OpenSBI injects its own no-map reservations (mmode_resv*); the DTB
            # must not duplicate them (that caused OVERLAP DETECTED in Linux).
            self.assertNotIn('opensbi@', text)
            self.assertIn('interrupts = <0x%x 0x01>;' % (m.PLIC_EXT_INT_SOURCE_BASE + m.EXT_INT_IO), text)
            bad = work/'bad'
            bad.mkdir()
            (bad/'firmware/dts').mkdir(parents=True)
            source = (ROOT/'firmware/dts/mega138k_ae350.dts').read_text()
            (bad/'firmware/dts/mega138k_ae350.dts').write_text(
                source.replace('serial@f0300000', 'serial@f0200000'))
            with open(work/'bad.log', 'w') as log, self.assertRaises(subprocess.CalledProcessError):
                opensbi.compile_dtb(SimpleNamespace(root=bad), m, bad, log)
            (bad/'firmware/dts/mega138k_ae350.dts').write_text(
                source.replace('plmt0@e6000000', 'plmt0@e6100000'))
            with open(work/'bad.log', 'w') as log, self.assertRaises(subprocess.CalledProcessError):
                opensbi.compile_dtb(SimpleNamespace(root=bad), m, bad, log)


if __name__ == '__main__':
    unittest.main()
