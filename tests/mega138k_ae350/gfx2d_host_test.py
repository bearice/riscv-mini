"""GFX2D Step 1-3: APB ID/ABI/CAP, descriptor latch, FILL/COPY/BLIT via fake UI."""
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

        rd_pending = [0]  # cycles until rd_data_valid

        def bench():
            # Fake DDR UI: always ready, masked writes + reads from mem.
            yield [d.ui.cmd_ready.eq(1), d.ui.wr_data_rdy.eq(1), d.ui.rd_data_valid.eq(0),
                   d.ui.rd_data.eq(0)]

            def pump():
                # Plain yield advances all clock domains (a signal yield
                # only advances that signal's domain in migen sim).
                # Read all UI signals in one timestep so the beat's
                # addr/data/mask are consistent.
                yield
                en, wen, addr, data, mask, cmd = yield [d.ui.cmd_en, d.ui.wr_data_en,
                                                        d.ui.addr, d.ui.wr_data,
                                                        d.ui.wr_data_mask, d.ui.cmd]
                yield [d.ui.rd_data_valid.eq(0)]
                if en and wen:
                    base = addr << 2  # UI addr = byte_addr[2:31] with low bits cleared
                    for i in range(32):
                        if not ((mask >> i) & 1):
                            mem[base + i] = (data >> (8 * i)) & 0xff
                elif en and cmd == 1:
                    base = addr << 2
                    val = 0
                    for i in range(32):
                        val |= mem[base + i] << (8 * i)
                    yield [d.ui.rd_data.eq(val), d.ui.rd_data_valid.eq(1)]

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
            # Unsupported op (3) reports ERR_UNSUPPORTED
            ok, _ = yield from access(reg['OP'], 3)
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

    def test_fill_carry_sum(self):
        # 10 pixels (20 bytes) at byte offset 20: off5 + rb_lo5 + 31 = 71 > 63
        # Exercises the second carry bit in geo_sum (regression test).
        d = make()
        mem = bytearray(DDR_SIZE)
        dst, stride, w, h, fg = 0x2014, 64, 10, 2, 0x07E0
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 0, 'DST_ADDR': dst, 'DST_STRIDE': stride,
            'WIDTH': w, 'HEIGHT': h, 'FG_COLOR': fg})
        for r in range(h):
            base = dst + r * stride
            for c in range(w):
                px = mem[base + 2 * c] | (mem[base + 2 * c + 1] << 8)
                assert px == fg, (r, c, px)
            assert mem[base - 1] == 0
            assert mem[base + 2 * w] == 0
        print('PASS GFX2D: FILL_RECT carry sum (off5 + rb_lo5 + 31 > 63)')

    def _run_op(self, d, mem_pump, desc):
        reg = amap.GFX2D_REGS

        def access(off, data=None):
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
            yield [d.ui.cmd_ready.eq(1), d.ui.wr_data_rdy.eq(1), d.ui.rd_data_valid.eq(0),
                   d.ui.rd_data.eq(0)]
            for name, v in desc.items():
                ok, _ = yield from access(reg[name], v)
                assert ok, name
            ok, _ = yield from access(reg['CONTROL'], 1)
            assert ok
            for _ in range(5000):
                yield from mem_pump()
                busy, = yield [d.busy]
                if not busy:
                    break
            else:
                raise AssertionError('op never completed')
            ok, val = yield from access(reg['STATUS'])
            assert ok and (val & 0b010), val  # DONE
            assert not (val & 0b100), val  # no ERROR
            ok, val = yield from access(reg['LAST_ERROR'])
            assert ok and val == 0, val

        run_simulation(d, bench(), clocks={'sys': 10, 'ddr': 10})

    def _fake_pump_factory(self, d, mem):
        def pump():
            yield
            en, wen, addr, data, mask, cmd = yield [d.ui.cmd_en, d.ui.wr_data_en,
                                                    d.ui.addr, d.ui.wr_data,
                                                    d.ui.wr_data_mask, d.ui.cmd]
            yield [d.ui.rd_data_valid.eq(0)]
            if en and wen:
                base = addr << 2
                for i in range(32):
                    if not ((mask >> i) & 1):
                        mem[base + i] = (data >> (8 * i)) & 0xff
            elif en and cmd == 1:
                base = addr << 2
                val = 0
                for i in range(32):
                    val |= mem[base + i] << (8 * i)
                yield [d.ui.rd_data.eq(val), d.ui.rd_data_valid.eq(1)]
        return pump

    def test_copy(self):
        d = make()
        mem = bytearray(1 << 20)
        # src 0x2000 stride 64, 10x3 RGB565 pattern; dst 0x4000 stride 96
        for r in range(3):
            for c in range(10):
                px = 0x1000 + r * 16 + c
                mem[0x2000 + r * 64 + 2 * c] = px & 0xff
                mem[0x2000 + r * 64 + 2 * c + 1] = px >> 8
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 1, 'SRC_ADDR': 0x2000, 'DST_ADDR': 0x4000,
            'SRC_STRIDE': 64, 'DST_STRIDE': 96, 'WIDTH': 10, 'HEIGHT': 3})
        for r in range(3):
            for c in range(10):
                px = mem[0x4000 + r * 96 + 2 * c] | (mem[0x4000 + r * 96 + 2 * c + 1] << 8)
                assert px == 0x1000 + r * 16 + c, (r, c, px)
        # Overlap: same buffer, shift down by one row (src < dst)
        d2 = make()
        mem2 = bytearray(1 << 20)
        for r in range(4):
            for c in range(8):
                px = 0x2000 + r * 8 + c
                mem2[0x8000 + r * 32 + 2 * c] = px & 0xff
                mem2[0x8000 + r * 32 + 2 * c + 1] = px >> 8
        src_snapshot = bytes(mem2[0x8000:0x8000 + 4 * 32])
        self._run_op(d2, self._fake_pump_factory(d2, mem2), {
            'OP': 1, 'SRC_ADDR': 0x8000, 'DST_ADDR': 0x8020,
            'SRC_STRIDE': 32, 'DST_STRIDE': 32, 'WIDTH': 8, 'HEIGHT': 3})
        for r in range(3):
            for c in range(8):
                px = mem2[0x8020 + r * 32 + 2 * c] | (mem2[0x8020 + r * 32 + 2 * c + 1] << 8)
                assert px == 0x2000 + r * 8 + c, ('overlap', r, c, px)
        # Multi-beat copy: 32 pixels (64 bytes = 2 beats) across 2 rows
        d3 = make()
        mem3 = bytearray(1 << 20)
        for r in range(2):
            for c in range(32):
                px = 0x3000 + r * 32 + c
                mem3[0x10000 + r * 64 + 2 * c] = px & 0xff
                mem3[0x10000 + r * 64 + 2 * c + 1] = px >> 8
        self._run_op(d3, self._fake_pump_factory(d3, mem3), {
            'OP': 1, 'SRC_ADDR': 0x10000, 'DST_ADDR': 0x20000,
            'SRC_STRIDE': 64, 'DST_STRIDE': 64, 'WIDTH': 32, 'HEIGHT': 2})
        for r in range(2):
            for c in range(32):
                px = mem3[0x20000 + r * 64 + 2 * c] | (mem3[0x20000 + r * 64 + 2 * c + 1] << 8)
                assert px == 0x3000 + r * 32 + c, ('multi-beat copy', r, c, px)
        print('PASS GFX2D: COPY_AREA basic + overlap-down + multi-beat')

    def test_blit(self):
        d = make()
        mem = bytearray(1 << 20)
        # 8x2 glyph, 1 byte/row MSB-first: 0b10100001, 0b01011110
        glyph = bytes([0b10100001, 0b01011110])
        mem[0x1000:0x1000 + 2] = glyph
        fg, bg = 0xF800, 0x001F
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 2, 'SRC_ADDR': 0x1000, 'DST_ADDR': 0x2000,
            'SRC_STRIDE': 0, 'DST_STRIDE': 32, 'WIDTH': 8, 'HEIGHT': 2,
            'FG_COLOR': fg, 'BG_COLOR': bg, 'BLIT_SRC_WB': 1})
        for r in range(2):
            for c in range(8):
                bit = (glyph[r] >> (7 - c)) & 1
                want = fg if bit else bg
                px = mem[0x2000 + r * 32 + 2 * c] | (mem[0x2000 + r * 32 + 2 * c + 1] << 8)
                assert px == want, (r, c, px, want)
        # Unaligned dst (even, offset 4) 16x1, src wb=2
        d2 = make()
        mem2 = bytearray(1 << 20)
        glyph2 = bytes([0b11001010, 0b00111100])
        mem2[0x3000:0x3000 + 2] = glyph2
        self._run_op(d2, self._fake_pump_factory(d2, mem2), {
            'OP': 2, 'SRC_ADDR': 0x3000, 'DST_ADDR': 0x4004,
            'SRC_STRIDE': 0, 'DST_STRIDE': 64, 'WIDTH': 16, 'HEIGHT': 1,
            'FG_COLOR': fg, 'BG_COLOR': bg, 'BLIT_SRC_WB': 2})
        for c in range(16):
            bit = (glyph2[c >> 3] >> (7 - (c & 7))) & 1
            want = fg if bit else bg
            px = mem2[0x4004 + 2 * c] | (mem2[0x4004 + 2 * c + 1] << 8)
            assert px == want, ('unaligned', c, px, want)
        # Src offset 16 inside the source beat (same DDR beat as a
        # previous blit at offset 0) -- regression for src_byte_off.
        # Two ops on one DUT instance need a single simulation, so
        # run the second op via the same _run_op helper machinery on
        # a fresh instance whose memory already contains both results
        # is not enough; instead exercise offset src on its own here
        # (multi-op state leakage is covered on the board suite).
        d3 = make()
        mem3 = bytearray(1 << 20)
        mem3[0x5000:0x5000 + 2] = bytes([0xF0, 0x0F])
        mem3[0x5010:0x5010 + 2] = glyph2
        self._run_op(d3, self._fake_pump_factory(d3, mem3), {
            'OP': 2, 'SRC_ADDR': 0x5010, 'DST_ADDR': 0x7004, 'SRC_STRIDE': 0,
            'DST_STRIDE': 64, 'WIDTH': 16, 'HEIGHT': 1, 'FG_COLOR': fg, 'BG_COLOR': bg,
            'BLIT_SRC_WB': 2})
        for c in range(16):
            bit = (glyph2[c >> 3] >> (7 - (c & 7))) & 1
            want = fg if bit else bg
            px = mem3[0x7004 + 2 * c] | (mem3[0x7004 + 2 * c + 1] << 8)
            assert px == want, ('src-off16', c, px, want)
        print('PASS GFX2D: IMAGE_BLIT 8x2 + unaligned 16x1')

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
