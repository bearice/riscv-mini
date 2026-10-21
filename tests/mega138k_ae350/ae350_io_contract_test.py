"""The I/O suite contract keeps physical observation out of a digital pass."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from mini_ops.mega138k_ae350.ae350_io_contract import DONE, ERR_BUSY, GPIO_ID_PRODUCT, MACHINE_EXTERNAL, PLIC_GPIO_SOURCE, SCHEMA, WORDS, WS2812_ID, judge

QUIET = [0, 0, 0, 0, 0]


def good(overrides=None):
    words = [0] * WORDS
    words[0] = DONE
    words[1] = GPIO_ID_PRODUCT << 12
    words[2] = 1
    words[3] = 1
    words[4] = 0x3f
    words[5] = 1000
    words[6] = 1000
    words[7] = 2
    words[8] = 1
    words[9] = 1
    words[10] = 0
    words[11] = 4
    words[12] = 1
    words[13] = 1
    words[14] = PLIC_GPIO_SOURCE
    words[15] = 19
    words[16] = 1
    words[17] = ERR_BUSY
    words[18] = WS2812_ID
    words[20] = 1
    words[21] = 1
    words[24] = 0
    words[25] = 0
    words[26] = 0
    words[27] = 8
    words[28] = 8
    words[31] = SCHEMA
    for key, value in (overrides or {}).items():
        words[key] = value
    return words


def decide(words, mcause=MACHINE_EXTERNAL, trap=None):
    return judge(words, 0x3000, mcause, 0x3000, 0x1000, QUIET if trap is None else trap)


class IoContractTests(unittest.TestCase):
    def test_digital_pass_leaves_physical_unsigned(self):
        self.assertEqual(decide(good()), [])
        self.assertEqual(good()[22], 0)

    def test_completed_run_keeps_the_external_interrupt_cause(self):
        self.assertTrue(any('mcause' in item for item in decide(good(), mcause=0)))

    def test_synchronous_record_is_not_an_interrupt_cause(self):
        failures = decide(good(), trap=[0x80000002, 0, 0, 0, 0])
        self.assertTrue(any('synchronous exception record' in item for item in failures))

    def test_merged_level_cannot_pretend_to_be_edges(self):
        self.assertTrue(decide(good({8: 2})))

    def test_wrong_gpio_source_fails(self):
        self.assertTrue(decide(good({14: 9})))

    def test_incomplete_stage_stops(self):
        words = good()
        words[0] = 4
        self.assertTrue(any('stage' in item for item in decide(words, mcause=0)))

    def test_incomplete_synchronous_exception_is_named(self):
        words = good()
        words[0] = 4
        self.assertTrue(any('synchronous exception mcause' in item for item in decide(words, mcause=5)))

    def test_irq_must_be_serviced_while_dma_is_busy(self):
        self.assertTrue(any('IRQ services' in item for item in decide(good({27: 0, 28: 4}))))
        self.assertTrue(any('DMA bytes' in item for item in decide(good({25: 1}))))


if __name__ == '__main__':
    unittest.main()
