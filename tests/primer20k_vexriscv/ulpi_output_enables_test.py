"""Check every initialization enable and asynchronous DIR/serial combination."""
import unittest

from migen import Signal
from migen.sim import run_simulation

from gateware.machines.primer20k_vexriscv.peripherals.usb_ultra import ULPIOutputEnables


class OutputEnableTest(unittest.TestCase):
    def test_turnaround_truth_table(self):
        ready, direction, initial = Signal(), Signal(), Signal(8)
        dut = ULPIOutputEnables(ready, direction, initial)

        def check():
            for serial in range(2):
                yield ready.eq(serial)
                for enables in range(256):
                    yield initial.eq(enables)
                    for phy_drives in range(2):
                        yield direction.eq(phy_drives)
                        yield
                        gated = 0 if phy_drives else enables
                        expected = ((gated & 0xf8) | 7) if serial else gated
                        self.assertEqual((yield dut.oe), expected)

        run_simulation(dut, check())


if __name__ == '__main__':
    unittest.main()
