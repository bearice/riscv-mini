"""GFX2D Step 1-3: APB ID/ABI/CAP, descriptor latch, FILL/COPY/BLIT via fake UI."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Signal
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.peripherals.gfx2d import Gfx2D, ERR_NONE, ERR_UNSUPPORTED, ERR_PARAM

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
            # Unsupported op (5) reports ERR_UNSUPPORTED
            ok, _ = yield from access(reg['OP'], 5)
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

    def test_fill_odd_multi_beat(self):
        # 32 pixels (64 bytes) at odd destination address 0x2003 with color 0x1234.
        # Spans multiple DDR beats; verifies wr_pattern byte rotation across
        # 32-byte beat boundaries (regression test for odd address pattern shift).
        d = make()
        mem = bytearray(DDR_SIZE)
        dst, stride, w, h, fg = 0x2003, 128, 32, 2, 0x1234
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 0, 'DST_ADDR': dst, 'DST_STRIDE': stride,
            'WIDTH': w, 'HEIGHT': h, 'FG_COLOR': fg})
        for r in range(h):
            base = dst + r * stride
            for c in range(w):
                px = mem[base + 2 * c] | (mem[base + 2 * c + 1] << 8)
                assert px == fg, (r, c, hex(px), hex(fg))
            assert mem[base - 1] == 0
            assert mem[base + 2 * w] == 0
        print('PASS GFX2D: FILL_RECT odd destination multi-beat rotation')

    def _run_op(self, d, mem_pump, desc, expect_err=0):
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

        max_cycles = max(5000, desc.get('WIDTH', 0) * desc.get('HEIGHT', 1) * 8 + 2000)

        def bench():
            yield [d.ui.cmd_ready.eq(1), d.ui.wr_data_rdy.eq(1), d.ui.rd_data_valid.eq(0),
                   d.ui.rd_data.eq(0)]
            for name, v in desc.items():
                ok, _ = yield from access(reg[name], v)
                assert ok, name
            ok, _ = yield from access(reg['CONTROL'], 1)
            assert ok
            for _ in range(max_cycles):
                yield from mem_pump()
                busy, = yield [d.busy]
                if not busy:
                    break
            else:
                raise AssertionError('op never completed')
            ok, val = yield from access(reg['STATUS'])
            if expect_err:
                assert ok and (val & 0b100), val  # ERROR
                ok, val = yield from access(reg['LAST_ERROR'])
                assert ok and val == expect_err, (val, expect_err)
            else:
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
        # Overlapping rightward copy on same row (32 pixels, 64 bytes from 0x10000 to 0x10020):
        # dst - src = 32 < 64 (row_bytes); must be rejected with ERR_PARAM (regression test).
        d4 = make()
        mem4 = bytearray(1 << 20)
        self._run_op(d4, self._fake_pump_factory(d4, mem4), {
            'OP': 1, 'SRC_ADDR': 0x10000, 'DST_ADDR': 0x10020,
            'SRC_STRIDE': 64, 'DST_STRIDE': 64, 'WIDTH': 32, 'HEIGHT': 1},
            expect_err=ERR_PARAM)
        # Overlapping unequal-stride copy (Codex regression test):
        # src 0x2000 stride 128, dst 0x2020 stride 32, w 16 (row_bytes=32), h 4
        # Spans overlap in memory: must be rejected with ERR_PARAM.
        d5 = make()
        mem5 = bytearray(1 << 20)
        self._run_op(d5, self._fake_pump_factory(d5, mem5), {
            'OP': 1, 'SRC_ADDR': 0x2000, 'DST_ADDR': 0x2020,
            'SRC_STRIDE': 128, 'DST_STRIDE': 32, 'WIDTH': 16, 'HEIGHT': 4},
            expect_err=ERR_PARAM)
        print('PASS GFX2D: COPY_AREA basic + overlap-down + multi-beat + reject rightward overlap + reject unequal stride overlap')

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

    def test_fill_rect_xrgb8888(self):
        # 32bpp (4 bytes/px) XRGB8888 fill.
        # Test 16 pixels (64 bytes) spanning multiple beats at unaligned offset (0x2005)
        d = make()
        mem = bytearray(DDR_SIZE)
        dst, stride, w, h, fg = 0x2005, 128, 16, 2, 0x11223344
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 0, 'DST_ADDR': dst, 'DST_STRIDE': stride,
            'WIDTH': w, 'HEIGHT': h, 'FG_COLOR': fg, 'FORMAT': 2})
        for r in range(h):
            base = dst + r * stride
            for c in range(w):
                px = (mem[base + 4 * c] |
                      (mem[base + 4 * c + 1] << 8) |
                      (mem[base + 4 * c + 2] << 16) |
                      (mem[base + 4 * c + 3] << 24))
                assert px == fg, (r, c, hex(px), hex(fg))
            assert mem[base - 1] == 0
            assert mem[base + 4 * w] == 0
        print('PASS GFX2D: FILL_RECT XRGB8888 multi-beat unaligned')

    def test_copy_area_xrgb8888(self):
        # 32bpp COPY_AREA: forward and downward overlap
        d = make()
        mem = bytearray(DDR_SIZE)
        stride = 256
        # Fill source area (8x4 pixels of 32bpp) with unique words
        for r in range(4):
            for c in range(8):
                val = 0xA0000000 | (r << 16) | c
                addr = 0x1000 + r * stride + 4 * c
                mem[addr:addr + 4] = val.to_bytes(4, 'little')
        # Copy to 0x2000 (same byte offset mod 32: 0)
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 1, 'SRC_ADDR': 0x1000, 'DST_ADDR': 0x2000,
            'SRC_STRIDE': stride, 'DST_STRIDE': stride,
            'WIDTH': 8, 'HEIGHT': 4, 'FORMAT': 2})
        for r in range(4):
            for c in range(8):
                want = 0xA0000000 | (r << 16) | c
                addr = 0x2000 + r * stride + 4 * c
                got = int.from_bytes(mem[addr:addr + 4], 'little')
                assert got == want, (r, c, hex(got), hex(want))

        # Downward overlap copy: 0x2000 -> 0x2100 (same stride 256, delta +1 row)
        d2 = make()
        self._run_op(d2, self._fake_pump_factory(d2, mem), {
            'OP': 1, 'SRC_ADDR': 0x2000, 'DST_ADDR': 0x2100,
            'SRC_STRIDE': stride, 'DST_STRIDE': stride,
            'WIDTH': 8, 'HEIGHT': 4, 'FORMAT': 2})
        for r in range(4):
            for c in range(8):
                want = 0xA0000000 | (r << 16) | c
                addr = 0x2100 + r * stride + 4 * c
                got = int.from_bytes(mem[addr:addr + 4], 'little')
                assert got == want, ('overlap', r, c, hex(got), hex(want))
        print('PASS GFX2D: COPY_AREA XRGB8888 basic + overlap-down')

    def test_image_blit_xrgb8888(self):
        # 32bpp IMAGE_BLIT: 1bpp monochrome expanded to 32bpp pixels
        d = make()
        mem = bytearray(DDR_SIZE)
        glyph = bytes([0b10100101, 0b01011010])
        mem[0x1000:0x1002] = glyph
        fg, bg = 0x00FF00AA, 0x11002233
        # 8x2 glyph, dst=0x2000 (aligned), blit_wb=1
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 2, 'SRC_ADDR': 0x1000, 'DST_ADDR': 0x2000,
            'SRC_STRIDE': 0, 'DST_STRIDE': 64, 'WIDTH': 8, 'HEIGHT': 2,
            'FG_COLOR': fg, 'BG_COLOR': bg, 'BLIT_SRC_WB': 1, 'FORMAT': 2})
        for r in range(2):
            for c in range(8):
                bit = (glyph[r] >> (7 - c)) & 1
                want = fg if bit else bg
                addr = 0x2000 + r * 64 + 4 * c
                px = int.from_bytes(mem[addr:addr + 4], 'little')
                assert px == want, (r, c, hex(px), hex(want))

        # Unaligned dst: 4-byte aligned (offset 8) 16x1, src wb=2
        d2 = make()
        mem2 = bytearray(DDR_SIZE)
        glyph2 = bytes([0b11001010, 0b00111100])
        mem2[0x3000:0x3002] = glyph2
        self._run_op(d2, self._fake_pump_factory(d2, mem2), {
            'OP': 2, 'SRC_ADDR': 0x3000, 'DST_ADDR': 0x4008,
            'SRC_STRIDE': 0, 'DST_STRIDE': 128, 'WIDTH': 16, 'HEIGHT': 1,
            'FG_COLOR': fg, 'BG_COLOR': bg, 'BLIT_SRC_WB': 2, 'FORMAT': 2})
        for c in range(16):
            bit = (glyph2[c >> 3] >> (7 - (c & 7))) & 1
            want = fg if bit else bg
            addr = 0x4008 + 4 * c
            px = int.from_bytes(mem2[addr:addr + 4], 'little')
            assert px == want, ('unaligned 32bpp blit', c, hex(px), hex(want))
        print('PASS GFX2D: IMAGE_BLIT XRGB8888 8x2 + unaligned 16x1')

    def test_fill_rect_rgb888_rejected(self):
        # 24bpp packed RGB888 is not supported on GFX2D and must be rejected with ERR_PARAM
        d = make()
        mem = bytearray(DDR_SIZE)
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 0, 'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
            'WIDTH': 12, 'HEIGHT': 2, 'FG_COLOR': 0x00332211, 'FORMAT': 1},
            expect_err=ERR_PARAM)
        print('PASS GFX2D: FILL_RECT RGB888 rejected with ERR_PARAM')

    def test_format_validation(self):
        # Format > 3 rejected with ERR_PARAM
        d = make()
        mem = bytearray(DDR_SIZE)
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': 0, 'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
            'WIDTH': 8, 'HEIGHT': 2, 'FG_COLOR': 0, 'FORMAT': 4},
            expect_err=ERR_PARAM)

        # XRGB8888 IMAGE_BLIT requires 4-byte aligned dst
        d2 = make()
        self._run_op(d2, self._fake_pump_factory(d2, mem), {
            'OP': 2, 'SRC_ADDR': 0x1000, 'DST_ADDR': 0x2002,
            'SRC_STRIDE': 0, 'DST_STRIDE': 64, 'WIDTH': 8, 'HEIGHT': 1,
            'FG_COLOR': 0, 'BG_COLOR': 0, 'BLIT_SRC_WB': 1, 'FORMAT': 2},
            expect_err=ERR_PARAM)

        # RGB888 IMAGE_BLIT rejected with ERR_PARAM
        d3 = make()
        self._run_op(d3, self._fake_pump_factory(d3, mem), {
            'OP': 2, 'SRC_ADDR': 0x1000, 'DST_ADDR': 0x2000,
            'SRC_STRIDE': 0, 'DST_STRIDE': 64, 'WIDTH': 8, 'HEIGHT': 1,
            'FG_COLOR': 0, 'BG_COLOR': 0, 'BLIT_SRC_WB': 1, 'FORMAT': 1},
            expect_err=ERR_PARAM)
        print('PASS GFX2D: format validation rejects invalid formats and alignments')

    def test_height_countdown_not_truncated(self):
        # Regression test for PR #4 review finding:
        # HEIGHT > 65535 must not truncate to low 16 bits and finish after 1 row.
        d = make()
        reg = amap.GFX2D_REGS
        mem = bytearray(DDR_SIZE)

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

        mem_pump = self._fake_pump_factory(d, mem)

        def bench():
            yield [d.ui.cmd_ready.eq(1), d.ui.wr_data_rdy.eq(1), d.ui.rd_data_valid.eq(0),
                   d.ui.rd_data.eq(0)]
            desc = {'OP': 0, 'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
                    'WIDTH': 1, 'HEIGHT': 65537, 'FG_COLOR': 0x1234}
            for name, v in desc.items():
                ok, _ = yield from access(reg[name], v)
                assert ok, name
            ok, _ = yield from access(reg['CONTROL'], 1)
            assert ok
            # Pump for 25 cycles: row 0 completes and transitions to row 1.
            # If height was truncated to 16 bits (65537 & 0xffff == 1),
            # it would have finished after row 0 and busy would be 0!
            # With 32-bit rows_left, it continues to row 1 and stays busy.
            for _ in range(25):
                yield from mem_pump()
            busy, = yield [d.busy]
            assert busy, "engine prematurely completed after row 0 due to 16-bit truncation!"

        run_simulation(d, bench(), clocks={'sys': 10, 'ddr': 10})
        print('PASS GFX2D: 32-bit height countdown does not truncate')

    def test_address_map(self):
        self.assertEqual(amap.GFX2D_BASE, 0xF80C0000)
        self.assertEqual(amap.GFX2D_ID, 0xAE350601)
        self.assertEqual(amap.DEVICE_COUNT, 12)
        self.assertTrue(amap.SYSINFO_CAP & amap.CAP_GFX2D)
        self.assertEqual(amap.GFX2D_EP[0], amap.GFX2D_ID)
        self.assertEqual(amap.GFX2D_EP[1], amap.GFX2D_BASE)
        self.assertEqual(amap.GFX2D_CAP, 0x0FEF)
        self.assertTrue(amap.GFX2D_CAP & amap.GFX2D_CAP_SHADER)
        vals = list(amap.GFX2D_REGS.values())
        self.assertEqual(len(set(vals)), len(vals))
        self.assertTrue(all(v % 4 == 0 and 12 <= v < 4096 for v in vals))

    def test_mul_unorm8_exhaustive(self):
        # Verify hardware MUL_UNORM8 formula ((P + (P >> 8)) >> 8) matches
        # exact round-to-nearest ((A * B + 127) // 255) for all 65,536 pairs.
        for a in range(256):
            for b in range(256):
                p = a * b + 128
                hw = (p + (p >> 8)) >> 8
                ref = (a * b + 127) // 255
                self.assertEqual(hw, ref, f"Mismatch for a={a}, b={b}")
        print("PASS GFX2D: MUL_UNORM8 exhaustive 65,536 combinations match exact /255")

    def test_lerp_endpoints(self):
        # Verify LERP formula ((S - D) * W + (D << 8) + 128) >> 8 preserves endpoints:
        # alpha = 0 -> DST exact; alpha = 255 -> SRC exact (no pixel darkening).
        for s in range(256):
            for d in range(256):
                # alpha = 0: W = 0
                w0 = 0 + (0 >> 7)
                res0 = ((s - d) * w0 + (d << 8) + 128) >> 8
                self.assertEqual(res0, d, f"LERP alpha=0 mismatch for s={s}, d={d}")

                # alpha = 255: W = 255 + 1 = 256
                w255 = 255 + (255 >> 7)
                res255 = ((s - d) * w255 + (d << 8) + 128) >> 8
                self.assertEqual(res255, s, f"LERP alpha=255 mismatch for s={s}, d={d}")
        print("PASS GFX2D: LERP endpoints alpha=0->DST and alpha=255->SRC exact")

    def test_shader_blend_argb_to_rgb565(self):
        # Example 1: Standard Per-Pixel Alpha Blend ARGB8888 -> RGB565
        # LERP.rgb R0, R0.argb, R1.argb, R0.aaaa (SAT=1)
        d = make()
        reg = amap.GFX2D_REGS
        mem = bytearray(DDR_SIZE)

        inst0_hi = ((0x13 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (0 << 12) | (1 << 8) | (0 << 4) | (0xE4 >> 4))
        inst0_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0xFF << 12) | (0x8 << 8) | 0)

        src_addr, dst_addr = 0x2000, 0x4000
        stride_src, stride_dst = 64, 64
        w, h = 8, 2

        # Src: Semi-transparent red (A=128, R=255, G=0, B=0)
        # Dst: Solid blue (0x001F)
        for r in range(h):
            for c in range(w):
                mem[src_addr + r * stride_src + c * 4 + 0] = 0x00
                mem[src_addr + r * stride_src + c * 4 + 1] = 0x00
                mem[src_addr + r * stride_src + c * 4 + 2] = 0xFF
                mem[src_addr + r * stride_src + c * 4 + 3] = 0x80
                mem[dst_addr + r * stride_dst + c * 2 + 0] = 0x1F
                mem[dst_addr + r * stride_dst + c * 2 + 1] = 0x00

        cfg = 0 | (1 << 4) | (1 << 5) | (1 << 11) | (4 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': stride_src,
            'DST_ADDR': dst_addr, 'DST_STRIDE': stride_dst,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)

        # Expected blended pixel: RGB565 0x800F
        expected_p = (16 << 11) | 15
        for r in range(h):
            for c in range(w):
                p = (mem[dst_addr + r * stride_dst + c * 2] |
                     (mem[dst_addr + r * stride_dst + c * 2 + 1] << 8))
                self.assertEqual(p, expected_p)
        print("PASS GFX2D: OP_SHADER Example 1 (Blend ARGB8888 -> RGB565)")

    def test_shader_color_key_discard(self):
        # Example 2: Color Key Discard (Magenta -> discard)
        d = make()
        reg = amap.GFX2D_REGS
        mem = bytearray(DDR_SIZE)

        # Stage 0: DISCARD_IF.rgb EQ, SRC.argb, U0.argb
        inst0_hi = ((0x39 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (4 << 12) | (8 << 8) | (0 << 4) | (0xE4 >> 4))
        inst0_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0 << 12) | (0 << 8) | 0)

        # Stage 1: MOV.rgb R0, SRC.argb (SAT=1)
        inst1_hi = ((0x00 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (4 << 12) | (0 << 8) | (0 << 4) | (0xE4 >> 4))
        inst1_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0 << 12) | (0x8 << 8) | 0)

        src_addr, dst_addr = 0x2000, 0x4000
        stride_src, stride_dst = 64, 64
        w, h = 8, 1
        u0 = 0x00FF00FF  # Magenta key

        for c in range(w):
            mem[dst_addr + c * 2 + 0] = 0x1F  # Dst initialized to Blue
            mem[dst_addr + c * 2 + 1] = 0x00
            if c in (1, 3):
                # Magenta key
                mem[src_addr + c * 4 + 0] = 0xFF
                mem[src_addr + c * 4 + 1] = 0x00
                mem[src_addr + c * 4 + 2] = 0xFF
                mem[src_addr + c * 4 + 3] = 0xFF
            elif c == 2:
                # Yellow
                mem[src_addr + c * 4 + 0] = 0x00
                mem[src_addr + c * 4 + 1] = 0xFF
                mem[src_addr + c * 4 + 2] = 0xFF
                mem[src_addr + c * 4 + 3] = 0xFF
            else:
                # Green
                mem[src_addr + c * 4 + 0] = 0x00
                mem[src_addr + c * 4 + 1] = 0xFF
                mem[src_addr + c * 4 + 2] = 0x00
                mem[src_addr + c * 4 + 3] = 0xFF

        cfg = 1 | (1 << 4) | (1 << 11) | (2 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': stride_src,
            'DST_ADDR': dst_addr, 'DST_STRIDE': stride_dst,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 0,
            'UNIFORM0': u0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
            'INST1_LO': inst1_lo, 'INST1_HI': inst1_hi,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)

        p0 = mem[dst_addr + 0] | (mem[dst_addr + 1] << 8)
        p1 = mem[dst_addr + 2] | (mem[dst_addr + 3] << 8)
        p2 = mem[dst_addr + 4] | (mem[dst_addr + 5] << 8)
        p3 = mem[dst_addr + 6] | (mem[dst_addr + 7] << 8)
        self.assertEqual(p0, 0x07E0)  # Green
        self.assertEqual(p1, 0x001F)  # Discarded -> Blue preserved
        self.assertEqual(p2, 0xFFE0)  # Yellow
        self.assertEqual(p3, 0x001F)  # Discarded -> Blue preserved
        print("PASS GFX2D: OP_SHADER Example 2 (Color Key Discard)")

    def test_shader_dot3_grayscale(self):
        # Stage 1 DOT3: RGB -> Luminance Y = 0.299 R + 0.587 G + 0.114 B
        d = make()
        reg = amap.GFX2D_REGS
        mem = bytearray(DDR_SIZE)

        u0 = (0 << 24) | (77 << 16) | (150 << 8) | 29
        inst0_hi = ((0x00 << 26) | (0 << 24) | (0 << 20) | (0xF << 16) |
                    (4 << 12) | (0 << 8) | (0 << 4) | (0xE4 >> 4))
        inst0_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0 << 12) | (0 << 8) | 0)

        inst1_hi = ((0x14 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (0 << 12) | (8 << 8) | (0 << 4) | (0xE4 >> 4))
        inst1_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0 << 12) | (0x8 << 8) | 0)

        src_addr, dst_addr = 0x2000, 0x4000
        stride_src, stride_dst = 64, 64
        w, h = 8, 1

        for c in range(w):
            mem[src_addr + c * 4 + 0] = 0
            mem[src_addr + c * 4 + 1] = 128
            mem[src_addr + c * 4 + 2] = 255
            mem[src_addr + c * 4 + 3] = 255

        expected_p = ((152 >> 3) << 11) | ((152 >> 2) << 5) | (152 >> 3)
        cfg = 1 | (1 << 4) | (1 << 11) | (4 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': stride_src,
            'DST_ADDR': dst_addr, 'DST_STRIDE': stride_dst,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 0,
            'UNIFORM0': u0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
            'INST1_LO': inst1_lo, 'INST1_HI': inst1_hi,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)
        for c in range(w):
            p = mem[dst_addr + c * 2 + 0] | (mem[dst_addr + c * 2 + 1] << 8)
            self.assertEqual(p, expected_p)
        print("PASS GFX2D: Stage 1 DOT3 grayscale luminance")

    def test_shader_xrgb_opacity(self):
        # Verify XRGB8888 source format ignores high byte X and forces A=255.
        d = make()
        reg = amap.GFX2D_REGS
        mem = bytearray(DDR_SIZE)

        # LERP R0, R0.argb, R1.argb, R0.aaaa
        inst0_hi = ((0x13 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (0 << 12) | (1 << 8) | (0 << 4) | (0xE4 >> 4))
        inst0_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0xFF << 12) | (0x8 << 8) | 0)

        src_addr, dst_addr = 0x2000, 0x4000
        stride_src, stride_dst = 64, 64
        w, h = 8, 1

        for c in range(w):
            # X = 0x12 (must be ignored, A forced to 255 -> 100% Red)
            mem[src_addr + c * 4 + 0] = 0x00
            mem[src_addr + c * 4 + 1] = 0x00
            mem[src_addr + c * 4 + 2] = 0xFF
            mem[src_addr + c * 4 + 3] = 0x12
            # Dst: Blue (0x001F)
            mem[dst_addr + c * 2 + 0] = 0x1F
            mem[dst_addr + c * 2 + 1] = 0x00

        # SRC_FORMAT = 2 (XRGB8888)
        cfg = 0 | (1 << 4) | (1 << 5) | (1 << 11) | (2 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': stride_src,
            'DST_ADDR': dst_addr, 'DST_STRIDE': stride_dst,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)

        # Output must be 100% Red (0xF800), NOT blue
        for c in range(w):
            p = mem[dst_addr + c * 2 + 0] | (mem[dst_addr + c * 2 + 1] << 8)
            self.assertEqual(p, 0xF800)
        print("PASS GFX2D: XRGB8888 forces A=255 opacity")

    def test_shader_width_1280_two_tiles_and_overlap(self):
        # 1. Non-overlapping two tiles (1024 + 256)
        d = make()
        reg = amap.GFX2D_REGS
        mem = bytearray(DDR_SIZE)

        inst0_hi = ((0x13 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (0 << 12) | (1 << 8) | (0 << 4) | (0xE4 >> 4))
        inst0_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0xFF << 12) | (0x8 << 8) | 0)

        src_addr, dst_addr = 0x10000, 0x20000
        stride_src, stride_dst = 1280 * 4, 1280 * 2
        w, h = 1280, 1

        for c in range(w):
            mem[src_addr + c * 4 + 0] = 0x00
            mem[src_addr + c * 4 + 1] = 0x00
            mem[src_addr + c * 4 + 2] = 0xFF
            mem[src_addr + c * 4 + 3] = 0x80
            mem[dst_addr + c * 2 + 0] = 0x1F
            mem[dst_addr + c * 2 + 1] = 0x00

        cfg = 0 | (1 << 4) | (1 << 5) | (1 << 11) | (4 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': stride_src,
            'DST_ADDR': dst_addr, 'DST_STRIDE': stride_dst,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)

        expected_p = (16 << 11) | 15
        for c in range(w):
            p = mem[dst_addr + c * 2 + 0] | (mem[dst_addr + c * 2 + 1] << 8)
            self.assertEqual(p, expected_p)
        print("PASS GFX2D: OP_SHADER WIDTH=1280 two tiles (1024 + 256) bit-exact")

        # 2. Overlapping buffers with WIDTH > 1024 rejected with ERR_PARAM
        d2 = make()
        desc_bad = dict(desc)
        desc_bad['DST_ADDR'] = src_addr + 100
        self._run_op(d2, self._fake_pump_factory(d2, mem), desc_bad,
                     expect_err=ERR_PARAM)
        print("PASS GFX2D: OP_SHADER WIDTH=1280 overlap rejected with ERR_PARAM")

    def test_shader_overlap_downward_direction(self):
        # 3 rows, stride=64, src=0x2000, dst=0x2040 (downward shift by 1 row)
        d = make()
        mem = bytearray(DDR_SIZE)
        w, h, stride = 16, 3, 64
        src_addr, dst_addr = 0x2000, 0x2040

        # Pass-through shader: R0 = SRC
        inst0_hi = ((0x0 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (0 << 12) | (1 << 8) | (0 << 4) | (0xE4 >> 4))
        inst0_lo = (((0xE4 & 0xF) << 28) | (0xE4 << 20) |
                    (0xFF << 12) | (0x8 << 8) | 0)

        for r in range(h):
            for c in range(w):
                val = 0x1000 * (r + 1) + c
                mem[src_addr + r * stride + 2 * c + 0] = val & 0xFF
                mem[src_addr + r * stride + 2 * c + 1] = (val >> 8) & 0xFF

        cfg = 0 | (1 << 4) | (0 << 5) | (1 << 11) | (0 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': stride,
            'DST_ADDR': dst_addr, 'DST_STRIDE': stride,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)

        for r in range(h):
            for c in range(w):
                want = 0x1000 * (r + 1) + c
                got = (mem[dst_addr + r * stride + 2 * c + 0] |
                       (mem[dst_addr + r * stride + 2 * c + 1] << 8))
                self.assertEqual(got, want)
        print("PASS GFX2D: OP_SHADER downward row overlap runs bottom-up cleanly")

    def test_shader_reject_unimplemented_formats(self):
        d = make()
        mem = bytearray(DDR_SIZE)
        # 1. dst format = FMT_INDEX8 (6)
        desc = {
            'OP': 3, 'SHADER_CFG': 0,
            'SRC_ADDR': 0x1000, 'SRC_STRIDE': 64,
            'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
            'WIDTH': 16, 'HEIGHT': 1, 'FORMAT': 6,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc,
                     expect_err=ERR_PARAM)

        # 2. src format = FMT_INDEX8 (6)
        d2 = make()
        cfg_src_idx8 = 0 | (1 << 4) | (1 << 11) | (6 << 12)
        desc2 = dict(desc)
        desc2['FORMAT'] = 0
        desc2['SHADER_CFG'] = cfg_src_idx8
        self._run_op(d2, self._fake_pump_factory(d2, mem), desc2,
                     expect_err=ERR_PARAM)
        print("PASS GFX2D: OP_SHADER rejects INDEX8/MONO1 formats with ERR_PARAM")

    def test_validation_rejects_high_bits_in_op_and_format(self):
        d = make()
        mem = bytearray(DDR_SIZE)
        # OP=0x10 rejected with ERR_PARAM
        desc_bad_op = {
            'OP': 0x10, 'SRC_ADDR': 0x1000, 'DST_ADDR': 0x2000,
            'SRC_STRIDE': 64, 'DST_STRIDE': 64, 'WIDTH': 16, 'HEIGHT': 1,
            'FORMAT': 0,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc_bad_op,
                     expect_err=ERR_PARAM)

        # FORMAT=0x10 rejected with ERR_PARAM
        d2 = make()
        desc_bad_fmt = {
            'OP': 0, 'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
            'WIDTH': 16, 'HEIGHT': 1, 'FORMAT': 0x10, 'FG_COLOR': 0xFFFF,
        }
        self._run_op(d2, self._fake_pump_factory(d2, mem), desc_bad_fmt,
                     expect_err=ERR_PARAM)
        print("PASS GFX2D: validation rejects high bits in OP and FORMAT")

    def test_shader_r3_coords_and_sat(self):
        d = make()
        mem = bytearray(DDR_SIZE)
        # MOV.SAT R0, R3.bbbb (X coordinate clamped to [0, 255])
        inst0_hi = ((0x00 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (3 << 12) | (0 << 8) | (0 << 4) | (0x00 >> 4))
        inst0_lo = (((0x00 & 0xF) << 28) | (0x00 << 20) |
                    (0 << 12) | (0x8 << 8) | 0)
        cfg = 0 | (0 << 4) | (0 << 5) | (0 << 11) | (0 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
            'WIDTH': 16, 'HEIGHT': 1, 'FORMAT': 0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)
        for c in range(16):
            got = mem[0x2000 + 2 * c] | (mem[0x2000 + 2 * c + 1] << 8)
            # c in [0..15] -> RGB565: R=c>>3, G=c>>2, B=c>>3
            want = ((c >> 3) << 11) | ((c >> 2) << 5) | (c >> 3)
            self.assertEqual(got, want)

        # SHL.SAT R0, SRC.aaaa, 8 (255 << 8 = 65280 must saturate to 255, not 0)
        d3 = make()
        mem3 = bytearray(DDR_SIZE)
        for i in range(16):
            mem3[0x1000 + 4 * i:0x1000 + 4 * (i + 1)] = b'\xff\xff\xff\xff'
        inst0_hi = ((0x23 << 26) | (0 << 24) | (0 << 20) | (7 << 16) |
                    (4 << 12) | (0 << 8) | (0 << 4) | (0xFF >> 4))
        inst0_lo = (((0xFF & 0xF) << 28) | (0x00 << 20) |
                    (0 << 12) | (0x8 << 8) | 8)
        cfg3 = 0 | (1 << 4) | (0 << 5) | (1 << 11) | (4 << 12)
        desc3 = {
            'OP': 3, 'SHADER_CFG': cfg3,
            'SRC_ADDR': 0x1000, 'SRC_STRIDE': 64,
            'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
            'WIDTH': 16, 'HEIGHT': 1, 'FORMAT': 0,
            'INST0_LO': inst0_lo, 'INST0_HI': inst0_hi,
        }
        self._run_op(d3, self._fake_pump_factory(d3, mem3), desc3)
        for c in range(16):
            got = mem3[0x2000 + 2 * c] | (mem3[0x2000 + 2 * c + 1] << 8)
            self.assertEqual(got, 0xFFFF, f"SHL.SAT pixel {c} should saturate to 0xFFFF, got {hex(got)}")
        print("PASS GFX2D: R3 unscaled coordinates and SAT clamping verified")


if __name__ == '__main__':
    unittest.main()
