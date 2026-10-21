"""The DMA suite contract rejects an executed-but-wrong result block."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from mini_ops.mega138k_ae350.ae350_dma_contract import CYCLIC, DMAC_CFG, DONE, ERR_ABI, ERR_ALIGN, ERR_BIND, ERR_DEVICE, ERR_WIDTH, POLICY_DIRTY, POLICY_WRITE_THROUGH, REJECT, SCHEMA, STALE, WORDS, judge


def good(overrides=None):
    words = [0] * WORDS
    words[0] = DONE
    words[1] = 10000
    words[5] = REJECT
    words[6] = ERR_DEVICE
    words[7] = ERR_ABI
    words[8] = ERR_BIND | (ERR_BIND << 16)
    words[9] = 1
    words[10] = 1
    words[11] = CYCLIC
    words[13] = STALE
    words[14] = POLICY_WRITE_THROUGH
    words[15] = 8
    words[16] = 1
    words[17] = 1
    words[20] = 4
    words[21] = 4
    words[22] = 16
    words[23] = 3
    words[24] = 0x73000001
    words[25] = 0x74000002
    words[26] = 1
    words[27] = DMAC_CFG
    words[29] = ERR_ALIGN
    words[30] = ERR_WIDTH
    words[31] = SCHEMA
    words = list(words)
    for key, value in (overrides or {}).items():
        words[key] = value
    return words


class ContractTest(unittest.TestCase):
    def test_write_through_observation_passes_without_claiming_a_dirty_line(self):
        self.assertEqual(judge(good(), 0x3340, 0, 0x3000, 0x1000), [])

    def test_flat_counter_still_visible_to_dma_is_not_a_dirty_line(self):
        self.assertEqual(judge(good({15: 0}), 0x3340, 0, 0x3000, 0x1000), [])

    def test_proved_dirty_line_passes(self):
        self.assertEqual(judge(good({14: POLICY_DIRTY, 15: 0, 16: 0}), 0x3340, 0, 0x3000, 0x1000), [])

    def test_visible_store_is_not_accepted_as_a_dirty_line(self):
        failures = judge(good({14: POLICY_DIRTY, 15: 0, 16: 1}), 0x3340, 0, 0x3000, 0x1000)
        self.assertTrue(any('dirty-line' in item for item in failures))

    def test_unresolved_policy_fails(self):
        self.assertTrue(any('policy' in item for item in judge(good({14: 0}), 0x3340, 0, 0x3000, 0x1000)))

    def test_incomplete_payload_is_not_a_pass(self):
        failures = judge(good({0: 0xC3100001, 2: 5}), 0x3340, 0, 0x3000, 0x1000)
        self.assertEqual(len(failures), 1)
        self.assertIn('completion', failures[0])

    def test_mismatch_reject_and_drain_are_failures(self):
        failures = judge(good({2: 1, 5: 0xFF, 11: 0x3, 12: 2, 19: 1, 26: 0}),
                         0x3340, 0, 0x3000, 0x1000)
        text = ' '.join(failures)
        self.assertIn('mismatches', text)
        self.assertIn('reject', text)
        self.assertIn('cyclic', text)
        self.assertIn('late writes', text)
        self.assertIn('handshake', text)

    def test_trap_is_not_execution_success(self):
        self.assertTrue(any('mcause' in item for item in judge(good(), 0x3340, 2, 0x3000, 0x1000)))


if __name__ == '__main__':
    unittest.main()
