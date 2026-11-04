"""Visible activity pulses must not disappear or stay continuously lit."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Signal
from migen.sim import run_simulation
from gateware.devices.gpio.activity import ActivityFlash


class ActivityTests(unittest.TestCase):
    def trace(self, traffic):
        raw = Signal()
        dut = ActivityFlash(raw, 4)
        observed = []
        def stimulus():
            for active in traffic:
                yield raw.eq(active)
                yield
                observed.append((yield dut.on))
        run_simulation(dut, stimulus())
        return observed

    def test_short_activity_is_visible_and_returns_idle(self):
        values = self.trace([0] * 5 + [1] * 2 + [0] * 30)
        self.assertGreaterEqual(sum(values), 4)
        self.assertFalse(any(values[-10:]))

    def test_continuous_activity_blinks_and_stops(self):
        values = self.trace([1] * 60 + [0] * 30)
        self.assertGreaterEqual(sum(a == 0 and b == 1 for a, b in zip(values, values[1:])), 3)
        self.assertFalse(any(values[-10:]))


if __name__ == '__main__':
    unittest.main()
