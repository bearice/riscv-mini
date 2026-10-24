"""PLL lock status crosses two data stages, independent of system reset."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from migen import Module, Signal
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350.clock import PLLStatusSynchronizer


class PLLStatusTests(unittest.TestCase):
    def test_lock_and_loss_cross_two_stages(self):
        dut = Module()
        lock = Signal()
        dut.submodules.status = receiver = PLLStatusSynchronizer(lock)
        observed = []

        def process():
            for value in (0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1):
                yield lock.eq(value)
                yield
                observed.append(((yield receiver.meta), (yield receiver.o)))

        run_simulation(dut, process())
        self.assertIn((1, 0), observed)
        self.assertIn((0, 1), observed)
        self.assertEqual(observed[-1], (1, 1))
        for before, after in zip(observed, observed[1:]):
            self.assertEqual(after[1], before[0])


if __name__ == '__main__':
    unittest.main()
