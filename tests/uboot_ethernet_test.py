"""Ethernet runtime DT follows the selected SoC; DMA never falls back to PIO."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.opensbi_build import ethernet_node, ETH_RING_REGISTERS


class EthernetTest(unittest.TestCase):
    def ring(self):
        return {'csr_bases': {'eth_dma': 0xf0009000, 'ethphy': 0xf0009800},
            'constants': {'mini_feature_eth_dma': 1, 'config_eth_ring_dma': 1,
                'eth_dma_interrupt': 7}, 'memories': {},
            'csr_registers': {'eth_dma_'+name: {'addr': 0xf0009000+4*(13-i),
                'size': 1} for i, name in enumerate(ETH_RING_REGISTERS)}}

    def test_ring_without_cpu_packet_sram(self):
        node = ethernet_node(self.ring())
        self.assertIn('riscv-mini,liteeth-ring', node)
        self.assertIn('reg=<0xf0009000 0x38>,<0xf0009800 0x10>', node)
        self.assertIn('interrupts=<7>', node)
        self.assertIn('csr-offsets=<0x34 0x30 0x2c', node)
        self.assertNotIn('f100', node)

    def test_invalid_ring_never_selects_pio(self):
        for mutate in (lambda c: c['constants'].update(config_eth_ring_dma=0),
                lambda c: c['csr_registers'].pop('eth_dma_busy'),
                lambda c: c['csr_registers']['eth_dma_rx_base'].update(size=2)):
            csr = self.ring()
            mutate(csr)
            with self.assertRaises(ValueError):
                ethernet_node(csr)

    def test_pio_uses_selected_addresses(self):
        csr = {'csr_bases': {'ethmac': 0xf0001800, 'ethphy': 0xf0002000},
            'constants': {'ethmac_interrupt': 5}, 'memories': {
                'ethmac_rx': {'base': 0xf1000000, 'size': 4096},
                'ethmac_tx': {'base': 0xf1001000, 'size': 4096}}}
        node = ethernet_node(csr)
        self.assertIn('reg=<0xf0001800 0x40>,<0xf0002000 0x10>', node)
        self.assertNotIn('liteeth-ring', node)
        self.assertEqual(ethernet_node({'csr_bases': {}}), '')


if __name__ == '__main__':
    unittest.main()
