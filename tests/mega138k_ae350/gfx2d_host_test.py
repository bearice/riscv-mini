"""GFX2D Step 1 skeleton: APB ID/ABI/CAP, descriptor latch, START unsupported."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Signal
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.peripherals.gfx2d import Gfx2D


def make():
    d = Gfx2D()
    return d


class Gfx2DHostTest(unittest.TestCase):
    def test_apb_skeleton(self):
        d = make()
        reg = amap.GFX2D_REGS

        def access(off, data=None):
            yield [d.off.eq(off), d.write.eq(data is not None),
                   d.wdata.eq(data or 0), d.selected.eq(1), d.commit.eq(1)]
            yield
            # admitted is registered; go asserts the following cycle
            yield
            ok = yield d.ok
            rdata = yield d.rdata
            yield [d.selected.eq(0), d.commit.eq(0), d.write.eq(0)]
            yield
            return ok, rdata

        def bench():
            # ID / ABI / CAP
            for off, want in ((0, amap.GFX2D_ID), (4, amap.ABI_VERSION), (8, amap.GFX2D_CAP)):
                ok, val = yield from access(off)
                assert ok and val == want, (off, ok, val, want)
            # Descriptor latch
            ok, _ = yield from access(reg['OP'], 2)
            assert ok
            ok, val = yield from access(reg['OP'])
            assert ok and val == 2
            ok, _ = yield from access(reg['DST_ADDR'], 0x08200000)
            assert ok
            ok, val = yield from access(reg['DST_ADDR'])
            assert ok and val == 0x08200000
            # Unknown offset is rejected
            ok, _ = yield from access(0x100)
            assert not ok
            # START is accepted then reports UNSUPPORTED (Step 1: no DDR)
            ok, _ = yield from access(reg['CONTROL'], 1)
            assert ok
            ok, val = yield from access(reg['STATUS'])
            assert ok and (val & 0b110) == 0b110, val  # DONE + ERROR
            ok, val = yield from access(reg['LAST_ERROR'])
            assert ok and val == 1
            ok, val = yield from access(reg['COMMANDS'])
            assert ok and val == 1
            ok, val = yield from access(reg['ERRORS'])
            assert ok and val == 1
            # IRQ pending set, enable gates irq line
            ok, val = yield from access(reg['IRQ_STATUS'])
            assert ok and val == 1
            assert (yield d.irq) == 0
            yield from access(reg['IRQ_ENABLE'], 1)
            yield  # irq is registered
            assert (yield d.irq) == 1
            # W1C clears pending
            yield from access(reg['IRQ_STATUS'], 1)
            yield
            assert (yield d.irq) == 0
            # CLEAR_ERRORS clears DONE/ERROR
            yield from access(reg['CLEAR_ERRORS'], 1)
            ok, val = yield from access(reg['STATUS'])
            assert ok and (val & 0b110) == 0
            print('PASS GFX2D skeleton: ID/ABI/CAP, latch, START unsupported, irq, clear')

        run_simulation(d, bench())

    def test_address_map(self):
        self.assertEqual(amap.GFX2D_BASE, 0xF80C0000)
        self.assertEqual(amap.GFX2D_ID, 0xAE350601)
        self.assertEqual(amap.DEVICE_COUNT, 12)
        self.assertTrue(amap.SYSINFO_CAP & amap.CAP_GFX2D)
        self.assertEqual(amap.GFX2D_EP[0], amap.GFX2D_ID)
        self.assertEqual(amap.GFX2D_EP[1], amap.GFX2D_BASE)
        # Register offsets are unique, aligned, within one 4KiB page
        vals = list(amap.GFX2D_REGS.values())
        self.assertEqual(len(set(vals)), len(vals))
        self.assertTrue(all(v % 4 == 0 and 12 <= v < 4096 for v in vals))


if __name__ == '__main__':
    unittest.main()
