"""GFX2D Step 1/2: APB ID/ABI/CAP, descriptor latch, FILL_RECT via fake UI."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Signal
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.peripherals.gfx2d import Gfx2D, ERR_NONE, ERR_UNSUPPORTED

DDR_SIZE = 1 << 20


def make():
    return Gfx2D(ddr_size=DDR_SIZE)


class Gfx2DHostTest(unittest.TestCase):
    def test_apb_and_fill(self):
        d = make()
        reg = amap.GFX2D_REGS
        mem = bytearray(DDR_SIZE)

        def access(off, data=None):
            # Mirror the fabric: inputs held, one commit pulse sampled
            # by the peripheral at the commit edge.
            yield [d.off.eq(off), d.write.eq(data is not None),
                   d.wdata.eq(data or 0), d.selected.eq(1), d.commit.eq(0)]
            yield
            yield [d.commit.eq(1)]
            yield
            ok = yield d.ok
            rdata = yield d.rdata
            yield [d.selected.eq(0), d.commit.eq(0), d.write.eq(0)]
            yield
            return ok, rdata

        def bench():
            # Fake DDR UI: always ready, apply masked writes to mem.
            yield [d.ui.cmd_ready.eq(1), d.ui.wr_data_rdy.eq(1), d.ui.rd_data_valid.eq(0)]

            def pump():
                # Plain yield advances all clock domains (a signal yield
                # only advances that signal's domain in migen sim).
                # Read all UI signals in one timestep so the beat's
                # addr/data/mask are consistent.
                yield
                en, wen, addr, data, mask = yield [d.ui.cmd_en, d.ui.wr_data_en,
                                                   d.ui.addr, d.ui.wr_data, d.ui.wr_data_mask]
                if en and wen:
                    base = addr << 2  # UI addr = byte_addr[2:31] with low bits cleared
                    for i in range(32):
                        if (mask >> i) & 1:
                            mem[base + i] = (data >> (8 * i)) & 0xff

            for off, want in ((0, amap.GFX2D_ID), (4, amap.ABI_VERSION), (8, amap.GFX2D_CAP)):
                ok, val = yield from access(off)
                assert ok and val == want, (off, ok, val, want)
            # Descriptor latch
            ok, _ = yield from access(reg['OP'], 0)
            assert ok
            ok, val = yield from access(reg['OP'])
            assert ok and val == 0
            # Unaligned fill: dst=0x1003, stride=100, 10x3 RGB565 red
            dst, stride, w, h, fg = 0x1003, 100, 10, 3, 0xF800
            for name, v in (('DST_ADDR', dst), ('DST_STRIDE', stride),
                            ('WIDTH', w), ('HEIGHT', h), ('FG_COLOR', fg)):
                ok, _ = yield from access(reg[name], v)
                assert ok
            ok, _ = yield from access(reg['CONTROL'], 1)
            assert ok
            # Wait for completion (busy clears), pumping UI each cycle
            for _ in range(500):
                yield from pump()
                busy, = yield [d.busy]
                if not busy:
                    break
            else:
                raise AssertionError('fill never completed')
            ok, val = yield from access(reg['STATUS'])
            assert ok and (val & 0b110) == 0b010, val  # DONE, no ERROR
            ok, val = yield from access(reg['LAST_ERROR'])
            assert ok and val == ERR_NONE
            ok, val = yield from access(reg['COMMANDS'])
            assert ok and val == 1
            ok, val = yield from access(reg['ERRORS'])
            assert ok and val == 0
            # Verify pixels: RGB565 little-endian (low byte first)
            for row in range(h):
                base = dst + row * stride
                for col in range(w):
                    px = mem[base + 2 * col] | (mem[base + 2 * col + 1] << 8)
                    assert px == fg, (row, col, px)
                # bytes outside the row stay zero (mask edges)
                assert mem[base - 1] == 0 or base == dst
            clean_base = dst + h * stride
            assert all(b == 0 for b in mem[clean_base:clean_base + 64])
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
            # Unsupported op (COPY_AREA) reports ERR_UNSUPPORTED
            ok, _ = yield from access(reg['OP'], 1)
            assert ok
            ok, _ = yield from access(reg['CONTROL'], 1)
            assert ok
            for _ in range(500):
                yield from pump()
                busy, = yield [d.busy]
                if not busy:
                    break
            ok, val = yield from access(reg['STATUS'])
            assert ok and (val & 0b110) == 0b110, val
            ok, val = yield from access(reg['LAST_ERROR'])
            assert ok and val == ERR_UNSUPPORTED
            print('PASS GFX2D: ID/ABI/CAP, latch, FILL_RECT unaligned, irq, unsupported op')

        run_simulation(d, bench(), clocks={'sys': 10, 'ddr': 10})

    def test_address_map(self):
        self.assertEqual(amap.GFX2D_BASE, 0xF80C0000)
        self.assertEqual(amap.GFX2D_ID, 0xAE350601)
        self.assertEqual(amap.DEVICE_COUNT, 12)
        self.assertTrue(amap.SYSINFO_CAP & amap.CAP_GFX2D)
        self.assertEqual(amap.GFX2D_EP[0], amap.GFX2D_ID)
        self.assertEqual(amap.GFX2D_EP[1], amap.GFX2D_BASE)
        vals = list(amap.GFX2D_REGS.values())
        self.assertEqual(len(set(vals)), len(vals))
        self.assertTrue(all(v % 4 == 0 and 12 <= v < 4096 for v in vals))


if __name__ == '__main__':
    unittest.main()
