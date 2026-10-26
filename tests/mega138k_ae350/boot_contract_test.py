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
from gateware.description import load_machine, from_data, resolve_machine
from gateware.machines.mega138k_ae350.address_map import AddressMap
from gateware.machines.mega138k_ae350 import boot_contract
from scripts.modular.machines.mega138k_ae350 import bootloader
from mini_ops.mega138k_ae350.ae350_bootrom import jedec_id, prepare, boot_neighborhood, boot_region, ERASE_BLOCK

LAYOUT = AddressMap(resolve_machine('mega138k_ae350', ROOT))


class ContractTests(unittest.TestCase):
    def test_flash_region_tracks_selected_board_capacity(self):
        board, machine = load_machine('mega138k_ae350', ROOT)
        board['resources'][machine['devices']['boot_flash']]['size_bytes'] = 8 << 20
        layout = AddressMap(from_data(board, machine))
        self.assertEqual(layout.ROM_SIZE, (8 << 20) - boot_contract.FLASH_OFFSET)
        boot_contract.validate(layout)
        self.assertIn('LENGTH = 0x200000', boot_contract.ld_fragment(layout.ROM_SIZE))

    def test_programmer_erase_block_preserves_all_bytes_outside_boot_slot(self):
        for size in (9000, ERASE_BLOCK + 123, LAYOUT.ROM_SIZE):
            with self.subTest(size=size):
                start, length = boot_region(size, LAYOUT.FLASH_BYTES)
                old = (bytes(range(256)) * ((length+255)//256))[:length]
                image = b'\xa5' * size
                block, expected = boot_neighborhood(old, image, LAYOUT.FLASH_BYTES)
                prefix = boot_contract.FLASH_OFFSET - start
                self.assertEqual(block[:size], image)
                self.assertEqual(block[size:], old[prefix+size:prefix+len(block)])
                self.assertEqual(expected[:prefix], old[:prefix])
                self.assertEqual(expected[prefix+size:], old[prefix+size:])
                self.assertLessEqual(start + length, LAYOUT.FLASH_BYTES)
                with self.assertRaises(ValueError): boot_neighborhood(old[:-1], image, LAYOUT.FLASH_BYTES)

    def test_boot_region_can_end_at_last_flash_byte(self):
        start, length = boot_region(LAYOUT.ROM_SIZE, LAYOUT.FLASH_BYTES)
        self.assertEqual(start + length, LAYOUT.FLASH_BYTES)
        self.assertEqual(LAYOUT.ROM_SIZE, 10 << 20)
        with self.assertRaises(ValueError): boot_region(LAYOUT.ROM_SIZE + 1, LAYOUT.FLASH_BYTES)

    def test_official_flash_partition_keeps_cpu_reset_address(self):
        self.assertEqual(boot_contract.FLASH_OFFSET,0x600000)
        self.assertEqual(boot_contract.CONSTANTS['ROM_BASE'],0x80000000)
        self.assertLessEqual(boot_contract.FLASH_OFFSET+LAYOUT.ROM_SIZE,LAYOUT.FLASH_BYTES)

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
            board['resources'][machine['devices']['flash']]['size_bytes'] //= 2
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
            layout = dict(flash='config-U4', flash_bytes=LAYOUT.FLASH_BYTES,
                          offset=boot_contract.FLASH_OFFSET, size=LAYOUT.ROM_SIZE, erase_bytes=4096)
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
            image, _, _ = prepare(Store(), 'gateware')
            self.assertEqual(image, b'abcd')
            binary.write_bytes(b'x'*9000)
            image, _, _ = prepare(Store(), 'gateware')
            self.assertEqual(len(image), 9000)
            binary.write_bytes(b'x'*(LAYOUT.ROM_SIZE+1))
            with self.assertRaisesRegex(ValueError, 'partition'): prepare(Store(), 'gateware')
            binary.write_bytes(b'abcd')
            # Historical small-image artifacts remain installable, bounded by
            # their declared region and the physical Flash capacity.
            layout['size'] = 8192
            save()
            self.assertEqual(prepare(Store(), 'gateware')[0], b'abcd')
            layout['offset'] -= 4096
            save()
            with self.assertRaisesRegex(ValueError, 'contract'): prepare(Store(), 'gateware')


if __name__ == '__main__': unittest.main()
