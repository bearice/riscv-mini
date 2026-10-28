"""HPD CDC and stable-level filtering, independent of video pixel clocks."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350.peripherals.video import HDMIHotplugStatus
class HotplugTest(unittest.TestCase):
    def test_filter(self):
        dut=HDMIHotplugStatus(debounce_cycles=8)
        def test():
            for _ in range(12): yield
            self.assertEqual((yield dut.connected),0)
            # Short insertion bounce must not announce a connected sink.
            yield dut.input.eq(1)
            for _ in range(4): yield
            yield dut.input.eq(0)
            for _ in range(12): yield
            self.assertEqual((yield dut.connected),0)
            yield dut.input.eq(1)
            for _ in range(16): yield
            self.assertEqual((yield dut.current),1)
            self.assertEqual((yield dut.connected),1)
            # Synchronized low immediately blocks EDID even before debounce.
            yield dut.input.eq(0)
            for _ in range(5): yield
            self.assertEqual((yield dut.current),0)
            self.assertEqual((yield dut.connected),1)
            for _ in range(12): yield
            self.assertEqual((yield dut.connected),0)
            yield dut.input.eq(1)
            for _ in range(16): yield
            self.assertEqual((yield dut.connected),1)
        run_simulation(dut,test())
    def test_sticky_mask_and_clear_race(self):
        dut=HDMIHotplugStatus(debounce_cycles=4)
        def test():
            yield dut.input.eq(1)
            for _ in range(12): yield
            self.assertEqual((yield dut.pending),1)
            self.assertEqual((yield dut.irq),0) # masked events remain latched
            yield dut.enable.eq(1)
            yield
            self.assertEqual((yield dut.irq),1)
            yield dut.clear.eq(1)
            yield
            yield dut.clear.eq(0)
            yield
            self.assertEqual((yield dut.pending),0)
            self.assertEqual((yield dut.irq),0)
            yield dut.input.eq(0)
            for _ in range(12): yield
            self.assertEqual((yield dut.pending),2)
            self.assertEqual((yield dut.irq),0) # only connection enabled
            yield dut.enable.eq(3)
            yield
            self.assertEqual((yield dut.irq),1)
            yield dut.input.eq(1)
            for _ in range(12): yield
            self.assertEqual((yield dut.pending),3)
            yield dut.clear.eq(1)
            yield
            yield dut.clear.eq(0)
            yield
            self.assertEqual((yield dut.pending),2)
            self.assertEqual((yield dut.irq),1)
            yield dut.enable.eq(0)
            yield
            self.assertEqual((yield dut.irq),0)
            self.assertEqual((yield dut.pending),2)
        run_simulation(dut,test())
    def test_new_event_wins_continuous_clear(self):
        dut=HDMIHotplugStatus(debounce_cycles=4)
        def test():
            yield dut.clear.eq(3)
            for level, event in ((1,1),(0,2)):
                yield dut.input.eq(level)
                seen=False
                for _ in range(16):
                    yield
                    if (yield dut.pending)==event: seen=True
                self.assertTrue(seen, 'continuous W1C swallowed new event')
                self.assertEqual((yield dut.pending),0)
        run_simulation(dut,test())
if __name__=='__main__':unittest.main()
