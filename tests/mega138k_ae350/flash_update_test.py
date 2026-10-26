"""Persistent configuration updates must retain neighbors and require boot proof."""
import sys
import unittest
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from mini_ops.mega138k_ae350 import ae350_update as update
from mini_ops.mega138k_ae350.ae350_bootrom import ERASE_BLOCK
from mini_ops.mega138k_ae350 import ae350_session


class UpdateTests(unittest.TestCase):
    def test_wrong_fpga_identity_cannot_start_sram_write(self):
        self.check_sram_rejection('idcode 0x1000563d', 'DONE', 1)

    def test_programmer_printed_failure_is_not_success_even_with_zero_exit(self):
        self.check_sram_rejection('idcode 0x1081b', 'CRC check : FAIL', 2)

    def check_sram_rejection(self, identity, result, calls):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.tools.local.json').write_text(json.dumps(dict(openfpgaloader='openFPGALoader')))
            class Op:
                path = root
                report = {}
                def record(self, *args, **kwargs): pass
            with patch.object(ae350_session, 'ROOT', root):
                hardware = ae350_session.HardwareSession(Op())
            outputs = [subprocess.CompletedProcess([],0,identity,''),
                       subprocess.CompletedProcess([],0,result,'')]
            with patch.object(hardware, '_acquire'), patch.object(ae350_session.subprocess, 'run', side_effect=outputs) as run:
                with self.assertRaises(ae350_session.SessionError):
                    hardware.program('candidate.fs', cable_serial='test-board')
                self.assertEqual(run.call_count, calls)

    def test_fs_conversion_is_byte_exact_and_rejects_other_targets(self):
        header = '//Device: GW5AST-138\n//Encryption: OFF\n'
        self.assertEqual(update.configuration_image(header+'00000000000000011000000011111111\n'), b'\x00\x01\x80\xff')
        for body in ('001', '01234567', ''):
            with self.assertRaises(ValueError): update.configuration_image(header+body)
        with self.assertRaises(ValueError): update.configuration_image(header.replace('GW5AST-138','GW1N-9')+'0'*32)
        with patch.object(update, 'FLASH_OFFSET', 4):
            with self.assertRaises(ValueError): update.configuration_image(header+'0'*32)

    def test_partial_erase_block_tail_and_next_guard_are_preserved(self):
        image = b'\x5a' * (ERASE_BLOCK + 123)
        old = bytes(range(256)) * (3 * ERASE_BLOCK // 256)
        block, expected = update.configuration_update(old, image)
        self.assertEqual(len(block), 2 * ERASE_BLOCK)
        self.assertEqual(block[:len(image)], image)
        self.assertEqual(block[len(image):], old[len(image):2*ERASE_BLOCK])
        self.assertEqual(expected[len(image):], old[len(image):])
        with self.assertRaises(ValueError): update.configuration_update(old[:-1], image)

    def test_successful_matching_boot_installation_is_required(self):
        ref, boot = {'digest':'hardware'}, {'digest':'boot'}
        proof = dict(passed=True, installed=True, guard_unchanged=True, artifact=ref, bootloader=boot)
        update.check_boot_receipt(proof, boot)
        for key in ('passed', 'installed', 'guard_unchanged'):
            with self.assertRaises(ValueError): update.check_boot_receipt(dict(proof, **{key:False}), boot)
        with self.assertRaises(ValueError): update.check_boot_receipt(proof, {'digest':'other-boot'})
        # The same independent boot ROM remains valid after unrelated gateware
        # changes; each update still rechecks the actual Flash bytes.
        update.check_boot_receipt(dict(proof, artifact={'digest':'older-gateware'}), boot)


if __name__ == '__main__': unittest.main()
