"""Boot identity survives peripheral and frequency changes; contract cannot drift."""
import copy
import json
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from gateware.description import load_machine, from_data
from gateware.machines.mega138k_ae350.address_map import AddressMap
from gateware.machines.mega138k_ae350 import boot_contract
from scripts.modular.machines.mega138k_ae350 import bootloader
from mini_ops.mega138k_ae350.ae350_bootrom import jedec_id, prepare, boot_neighborhood, ERASE_BLOCK


class ContractTests(unittest.TestCase):
    def test_programmer_erase_block_preserves_all_bytes_outside_boot_slot(self):
        old = bytes(range(256)) * (3 * ERASE_BLOCK // 256)
        image = b'\xa5' * boot_contract.ROM_SIZE
        block, expected = boot_neighborhood(old, image)
        self.assertEqual(len(block), ERASE_BLOCK)
        self.assertEqual(block[:len(image)], image)
        self.assertEqual(block[len(image):], old[ERASE_BLOCK+len(image):2*ERASE_BLOCK])
        self.assertEqual(expected[:ERASE_BLOCK], old[:ERASE_BLOCK])
        self.assertEqual(expected[ERASE_BLOCK+len(image):], old[ERASE_BLOCK+len(image):])
        with self.assertRaises(ValueError): boot_neighborhood(old[:-1], image)

    def test_official_flash_partition_keeps_cpu_reset_address(self):
        self.assertEqual(boot_contract.FLASH_OFFSET,0x600000)
        self.assertEqual(boot_contract.CONSTANTS['ROM_BASE'],0x80000000)
        self.assertLessEqual(boot_contract.FLASH_OFFSET+boot_contract.ROM_SIZE,boot_contract.FLASH_BYTES)

    def test_sdk_flash_ports_replace_custom_rom_ports(self):
        from gateware.machines.mega138k_ae350.cpu import NativeAE350
        from migen import Record
        cpu=NativeAE350(wrapper=True,vendor_flash=True)
        cpu.connect_boot_flash(Record([('cs_n',1),('clk',1),('mosi',1),('miso',1),('wp_n',1),('hold_n',1)]))
        self.assertNotIn('i_ROM_HRDATA',cpu.cpu_params)
        self.assertIn('io_FLASH_SPI_CSN',cpu.cpu_params)
        self.assertIn('i_FR_PCLK',cpu.cpu_params)
        self.assertIn('o_FR_PREADY',cpu.cpu_params)

    def test_boot_identity_does_not_track_devices_or_clocks(self):
        board, machine = load_machine('mega138k_ae350', ROOT)
        class Store:
            root = ROOT
            def get(self, ref):
                return {'dependencies': {'machine-description': 'description'}} if ref == 'cpu' else {
                    'config': {'board': board, 'machine': machine}}
            def lookup(self, component, key):
                self.key = key
                return None
        class Run:
            def step(self, *args, **kwargs): pass
        store = Store()
        with patch.object(bootloader, 'discover', return_value={}), patch.object(bootloader, 'identity', return_value={}):
            bootloader.firmware(store, Run(), 'cpu', plan=True)
            first = store.key
            before_abi = AddressMap(from_data(board, machine)).image_abi()
            machine['clocks']['sys'] = 80000000
            machine['clocks']['video'] = 24000000
            machine['gpio']['debounce_ms'] += 1
            self.assertNotEqual(before_abi, AddressMap(from_data(board, machine)).image_abi())
            bootloader.firmware(store, Run(), 'cpu', plan=True)
            self.assertEqual(first, store.key)

    def test_boot_register_drift_is_rejected(self):
        board, machine = load_machine('mega138k_ae350', ROOT)
        layout = AddressMap(from_data(board, machine))
        boot_contract.validate(layout)
        layout.STATUS_ADDR += 4
        with self.assertRaisesRegex(ValueError, 'STATUS_ADDR'):
            boot_contract.validate(layout)

    def test_flash_jedec_fail_closed(self):
        self.assertEqual(jedec_id('JEDEC ID: 0xef4018\nDetected: winbond'), 0xef4018)
        for text in ['JEDEC ID: 0xef4017', 'JEDEC ID: 0xef4019', 'JEDEC ID: 0xffffff',
                     'JEDEC ID: 0xef4018\nJEDEC ID: 0xc84018', 'memory capacity : 18']:
            with self.assertRaises(ValueError): jedec_id(text)

    def test_flash_preparation_rejects_partition_drift_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'outputs').mkdir()
            layout = dict(flash='config-U4', flash_bytes=boot_contract.FLASH_BYTES,
                          offset=boot_contract.FLASH_OFFSET, size=8192, erase_bytes=4096)
            class Store:
                def get(self, ref, **kwargs):
                    return {'config': {'instruction_memory': 'SDK-Flash-U4+hard-ILM'},
                            'dependencies': {'bootloader': 'boot'}}
                def path(self, ref): return root
            def save():
                (root / 'outputs/flash-layout.json').write_text(json.dumps(layout))
            save()
            binary = root / 'outputs/boot.bin'
            binary.write_bytes(b'abcd')
            image, _ = prepare(Store(), 'gateware')
            self.assertEqual(image, b'abcd' + b'\xff'*8188)
            binary.write_bytes(b'x'*8193)
            with self.assertRaisesRegex(ValueError, 'partition'): prepare(Store(), 'gateware')
            binary.write_bytes(b'abcd')
            layout['offset'] -= 4096
            save()
            with self.assertRaisesRegex(ValueError, 'contract'): prepare(Store(), 'gateware')


if __name__ == '__main__': unittest.main()
