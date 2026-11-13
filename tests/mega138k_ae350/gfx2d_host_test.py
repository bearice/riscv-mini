"""GFX2D Step 1-3: APB ID/ABI/CAP, descriptor latch, FILL/COPY/BLIT via fake UI."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Signal
from migen.sim import run_simulation
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.peripherals.gfx2d import (
    Gfx2D, ShaderALU, BilinearFilter, ERR_NONE, ERR_UNSUPPORTED, ERR_PARAM, ERR_ADDR,
    OP_SHADER, OP_SCALE_BLIT)

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

    def test_fill_stride_changes_alignment(self):
        # Strides that change the byte alignment from row to row: each row's
        # pattern rotation must follow that row's own start offset.
        for fmt, bpp, stride, fg in ((0, 2, 3, 0x1234), (2, 4, 6, 0x11223344)):
            d = make()
            mem = bytearray(DDR_SIZE)
            dst, h = 0x2000, 4
            self._run_op(d, self._fake_pump_factory(d, mem), {
                'OP': 0, 'DST_ADDR': dst, 'DST_STRIDE': stride,
                'WIDTH': 1, 'HEIGHT': h, 'FG_COLOR': fg, 'FORMAT': fmt})
            for r in range(h):
                base = dst + r * stride
                px = int.from_bytes(mem[base:base + bpp], 'little')
                assert px == fg, (fmt, r, hex(px), hex(fg))
        print('PASS GFX2D: FILL_RECT per-row alignment with odd stride')

    def _run_op(self, d, mem_pump, desc, expect_err=0, writes=(), max_cycles=None):
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

        if max_cycles is None:
            max_cycles = max(5000, desc.get('WIDTH', 0) * desc.get('HEIGHT', 1) * 8 + 2000)

        def bench():
            yield [d.ui.cmd_ready.eq(1), d.ui.wr_data_rdy.eq(1), d.ui.rd_data_valid.eq(0),
                   d.ui.rd_data.eq(0)]
            for name, v in list(writes) + list(desc.items()):
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

    def _fake_pump_factory(self, d, mem, reads=None, tile_ws=None):
        def pump():
            yield
            en, wen, addr, data, mask, cmd = yield [d.ui.cmd_en, d.ui.wr_data_en,
                                                    d.ui.addr, d.ui.wr_data,
                                                    d.ui.wr_data_mask, d.ui.cmd]
            if tile_ws is not None:
                tw = yield d.engine.tile_w
                if not tile_ws or tile_ws[-1] != tw:
                    tile_ws.append(tw)
            yield [d.ui.rd_data_valid.eq(0)]
            if en and wen:
                base = addr << 2
                for i in range(32):
                    if not ((mask >> i) & 1):
                        mem[base + i] = (data >> (8 * i)) & 0xff
            elif en and cmd == 1:
                base = addr << 2
                if reads is not None:
                    reads.append(base)
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

    def test_shader_alu_arith_and_compare(self):
        # Directly drive one ALU stage with signed operands that leave the
        # unorm8 range, covering SAT and every CMP_SET sub-op.
        import random
        rng = random.Random(0x6B)
        edges = [-32768, -256, -1, 0, 1, 127, 255, 256, 32767]

        def s16(v):
            v &= 0xFFFF
            return v - 0x10000 if v & 0x8000 else v

        def sat(v):
            return 0 if v < 0 else 255 if v > 255 else v

        arith = {
            0x00: lambda a, b, c: a,
            0x01: lambda a, b, c: a + b,
            0x02: lambda a, b, c: a - b,
            0x03: lambda a, b, c: abs(a - b),
            0x04: lambda a, b, c: min(a, b),
            0x05: lambda a, b, c: max(a, b),
            0x06: lambda a, b, c: min(max(a, b), c),
        }
        logic = {
            0x20: lambda a, b, k: a & b,
            0x21: lambda a, b, k: a | b,
            0x22: lambda a, b, k: a ^ b,
            0x23: lambda a, b, k: a << k,
            0x24: lambda a, b, k: a >> k,
        }
        cmps = [lambda a, b: a == b, lambda a, b: a != b,
                lambda a, b: a < b, lambda a, b: a >= b]

        def operands(lo_le_hi):
            vals = [[rng.choice(edges) if rng.random() < 0.4 else
                     rng.randint(-32768, 32767) for _ in range(3)]
                    for _ in range(4)]
            if lo_le_hi:
                for v in vals:
                    v[1], v[2] = min(v[1], v[2]), max(v[1], v[2])
            return vals

        cases = []
        for op in arith:
            for s in (0, 1):
                if op == 0x06 and s:
                    continue
                for _ in range(24):
                    cases.append(('arith', op, s, 0, operands(op == 0x06)))
        for op in logic:
            for s in (0, 1):
                for k in range(16):
                    for _ in range(2 if op >= 0x23 else 1):
                        cases.append(('logic', op, s, k, operands(False)))
        for sub_op in range(4):
            for _ in range(24):
                vals = operands(False)
                if rng.random() < 0.5:
                    for v in vals:
                        v[1] = v[0]
                cases.append(('cmp', 0x38, 0, sub_op, vals))

        alu = ShaderALU(0)

        def gen():
            yield alu.last_stage.eq(0)
            yield alu.p_in.eq(0)
            yield alu.discard_in.eq(0)
            for kind, op, s, arg, vals in cases:
                lo, hi = enc(op, dst=0, wr=0xF, s0=1, s1=2, s2=3, sat=s,
                             sub_op=arg if kind == 'cmp' else 0,
                             imm=arg if kind == 'logic' else 0)
                yield alu.inst.eq((hi << 32) | lo)
                for c in range(4):
                    for r in range(3):
                        yield alu.r_in[r + 1][c].eq(vals[c][r])
                for _ in range(8):
                    yield
                if kind in ('arith', 'logic'):
                    for c in range(4):
                        a, b, cc = vals[c]
                        if kind == 'arith':
                            want = arith[op](a, b, cc)
                        else:
                            want = logic[op](a, b, arg)
                        want = sat(want) if s and op != 0x06 and op not in (
                            0x20, 0x21, 0x22) else s16(want)
                        got = yield alu.r_out[0][c]
                        self.assertEqual(got, want, (hex(op), s, arg, vals[c]))
                else:
                    want = int(all(cmps[arg](v[0], v[1]) for v in vals))
                    got = yield alu.p_out
                    self.assertEqual(got, want, (arg, vals))

        run_simulation(alu, gen())
        print("PASS GFX2D: ALU arithmetic, SAT and CMP_SET sub-ops")

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

        # 2. src format = FMT_MONO1 (7)
        d2 = make()
        cfg_src_mono = 0 | (1 << 4) | (1 << 11) | (7 << 12)
        desc2 = dict(desc)
        desc2['FORMAT'] = 0
        desc2['SHADER_CFG'] = cfg_src_mono
        self._run_op(d2, self._fake_pump_factory(d2, mem), desc2,
                     expect_err=ERR_PARAM)
        print("PASS GFX2D: OP_SHADER rejects INDEX8 dst / MONO1 src with ERR_PARAM")

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

    def test_shader_4stage_index8_dot3_lut_lerp(self):
        # Example 3: INDEX8 -> PAL_RAM -> MOV -> DOT3 -> LUT_FETCH -> LERP(DST)
        d = make()
        mem = bytearray(DDR_SIZE)
        pal = [pal_entry(i) for i in range(256)]
        lut = [lut_entry(i) for i in range(256)]
        writes = ([('LUT_ADDR', 0x100)] + [('LUT_DATA', v) for v in pal] +
                  [('LUT_ADDR', 0)] + [('LUT_DATA', v) for v in lut])

        src_addr, src_stride = 0x2003, 61
        dst_addr, dst_stride = 0x4000, 256
        w, h = 40, 2
        u0, u1 = 0x004D961D, 0xC0C0C0C0
        for r in range(h):
            for c in range(w):
                mem[src_addr + r * src_stride + c] = (r * 7 + c * 13) & 0xFF
                dpx = 0x10000000 * ((c + r) & 0xF) | (c * 0x10203) & 0xFFFFFF
                mem[dst_addr + r * dst_stride + 4 * c:
                    dst_addr + r * dst_stride + 4 * c + 4] = dpx.to_bytes(4, 'little')
        golden = {}
        for r in range(h):
            for c in range(w):
                s = split_argb(pal[mem[src_addr + r * src_stride + c]])
                dpx = split_argb(int.from_bytes(
                    mem[dst_addr + r * dst_stride + 4 * c:
                        dst_addr + r * dst_stride + 4 * c + 4], 'little'))
                y = min(255, (s[1] * 77 + s[2] * 150 + s[3] * 29 + 128) >> 8)
                mapped = split_argb(lut[y])
                wgt = 0xC0 + (0xC0 >> 7)
                out = [s[0]] + [((mapped[k] - dpx[k]) * wgt + (dpx[k] << 8) + 128) >> 8
                                for k in (1, 2, 3)]
                golden[(r, c)] = (out[0] << 24) | (out[1] << 16) | (out[2] << 8) | out[3]

        i0 = enc(0x00, wr=0xF, s0=4)
        i1 = enc(0x14, wr=0x7, s0=0, s1=8, sat=1)
        i2 = enc(0x30, wr=0x7, s0=0)
        i3 = enc(0x13, wr=0x7, s0=0, s1=5, s2=9, swz2=0xFF, sat=1)
        cfg = 3 | (1 << 4) | (1 << 5) | (1 << 11) | (6 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': src_stride,
            'DST_ADDR': dst_addr, 'DST_STRIDE': dst_stride,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 4,
            'UNIFORM0': u0, 'UNIFORM1': u1,
            **inst_regs(i0, i1, i2, i3),
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc, writes=writes)
        for (r, c), want in golden.items():
            a = dst_addr + r * dst_stride + 4 * c
            got = int.from_bytes(mem[a:a + 4], 'little')
            self.assertEqual(got, want, (r, c, hex(got), hex(want)))
        print("PASS GFX2D: 4-stage INDEX8/PAL -> DOT3 -> LUT_FETCH -> LERP bit-exact")

    def test_shader_4stage_predicate_chain(self):
        # P set in Stage 0 must survive Stages 1..2 and gate Stage 3.
        d = make()
        mem = bytearray(DDR_SIZE)
        src_addr, dst_addr, w = 0x2000, 0x4000, 16
        fg = 0x00123456
        for c in range(w):
            px = (c << 16) | ((200 + c) << 8) | (c * 16)
            mem[src_addr + 4 * c:src_addr + 4 * c + 4] = px.to_bytes(4, 'little')
        i0 = enc(0x38, wr=0x1, s0=4, s1=8, sub_op=2)
        i1 = enc(0x01, wr=0x7, s0=4, use_imm=1, imm=10, sat=1)
        i2 = enc(0x02, wr=0x7, s0=0, use_imm=1, imm=3, sat=1)
        i3 = enc(0x00, wr=0x7, s0=6, pred=1)
        cfg = 3 | (1 << 4) | (1 << 11) | (2 << 12)
        desc = {
            'OP': 3, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': 64,
            'DST_ADDR': dst_addr, 'DST_STRIDE': 64,
            'WIDTH': w, 'HEIGHT': 1, 'FORMAT': 2,
            'FG_COLOR': fg, 'UNIFORM0': 100,
            **inst_regs(i0, i1, i2, i3),
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc)
        for c in range(w):
            if c * 16 < 100:
                want = 0xFF000000 | fg
            else:
                r, g, b = (min(255, v + 10) - 3 for v in (c, 200 + c, c * 16))
                want = 0xFF000000 | (r << 16) | (g << 8) | b
            got = int.from_bytes(mem[dst_addr + 4 * c:dst_addr + 4 * c + 4], 'little')
            self.assertEqual(got, want, (c, hex(got), hex(want)))
        print("PASS GFX2D: 4-stage predicate chain (CMP_SET -> ADD -> SUB -> MOV IF_TRUE)")

    def test_shader_stage_placement_validation(self):
        mem = bytearray(DDR_SIZE)
        base = {
            'OP': 3, 'SRC_ADDR': 0x1000, 'SRC_STRIDE': 64,
            'DST_ADDR': 0x2000, 'DST_STRIDE': 64,
            'WIDTH': 8, 'HEIGHT': 1, 'FORMAT': 2,
        }
        mov = enc(0x00, wr=0xF, s0=4)
        # LUT_FETCH outside Stage 2 while active
        d = make()
        self._run_op(d, self._fake_pump_factory(d, mem), {
            **base, 'SHADER_CFG': 3 | (1 << 4),
            **inst_regs(enc(0x30, s0=4), mov, mov, mov)}, expect_err=ERR_PARAM)
        # DOT3 outside Stage 1 while active
        d = make()
        self._run_op(d, self._fake_pump_factory(d, mem), {
            **base, 'SHADER_CFG': 3 | (1 << 4),
            **inst_regs(mov, mov, mov, enc(0x14, s0=4, s1=8))}, expect_err=ERR_PARAM)
        # Misplaced opcode beyond LAST_STAGE is ignored; Stage 3 is bypassed
        d = make()
        fg = 0x00ABCDEF
        self._run_op(d, self._fake_pump_factory(d, mem), {
            **base, 'SHADER_CFG': 2 | (1 << 4), 'FG_COLOR': fg,
            **inst_regs(mov, mov, enc(0x00, wr=0x7, s0=6),
                        enc(0x30, wr=0xF, s0=4))})
        for c in range(8):
            got = int.from_bytes(mem[0x2000 + 4 * c:0x2000 + 4 * c + 4], 'little')
            self.assertEqual(got, 0xFF000000 | fg, (c, hex(got)))
        print("PASS GFX2D: DOT3/LUT_FETCH stage placement validated; inactive stages bypassed")

    def test_shader_index8_single_pixel_tiles(self):
        # WIDTH=1 makes the trailing PAL -> SRC_LB write land right before compute.
        d = make()
        mem = bytearray(DDR_SIZE)
        pal = [pal_entry(i) for i in range(256)]
        writes = [('LUT_ADDR', 0x100)] + [('LUT_DATA', v) for v in pal]
        src_addr, src_stride, h = 0x3005, 33, 3
        for r in range(h):
            mem[src_addr + r * src_stride] = (0x40 + r * 77) & 0xFF
        desc = {
            'OP': 3, 'SHADER_CFG': 0 | (1 << 4) | (1 << 11) | (6 << 12),
            'SRC_ADDR': src_addr, 'SRC_STRIDE': src_stride,
            'DST_ADDR': 0x5000, 'DST_STRIDE': 32,
            'WIDTH': 1, 'HEIGHT': h, 'FORMAT': 4,
            **inst_regs(enc(0x00, wr=0xF, s0=4)),
        }
        self._run_op(d, self._fake_pump_factory(d, mem), desc, writes=writes)
        for r in range(h):
            got = int.from_bytes(mem[0x5000 + 32 * r:0x5000 + 32 * r + 4], 'little')
            want = pal[(0x40 + r * 77) & 0xFF]
            self.assertEqual(got, want, (r, hex(got), hex(want)))
        print("PASS GFX2D: INDEX8 single-pixel tiles expand through PAL_RAM")

    def test_bilinear_filter_unit(self):
        import random
        rng = random.Random(6)
        dut = BilinearFilter()
        vecs = [[rng.getrandbits(32) for _ in range(4)] +
                [rng.choice((0, 1, 127, 128, 255, rng.randrange(256))) for _ in range(2)]
                for _ in range(300)]

        def bench():
            got = []
            # Generator writes land on the next edge, so vector i is at the
            # output after the LATENCY-th following edge.
            for i in range(len(vecs) + BilinearFilter.LATENCY):
                if i < len(vecs):
                    p00, p10, p01, p11, fu, fv = vecs[i]
                    yield [dut.p00.eq(p00), dut.p10.eq(p10), dut.p01.eq(p01),
                           dut.p11.eq(p11), dut.wu.eq(fu + (fu >> 7)),
                           dut.wv.eq(fv + (fv >> 7))]
                yield
                if i >= BilinearFilter.LATENCY:
                    got.append((yield dut.out))
            got.append((yield dut.out))
            for k, (p00, p10, p01, p11, fu, fv) in enumerate(vecs):
                want = bilinear_ref(p00, p10, p01, p11, fu, fv)
                assert got[k] == want, (k, hex(got[k]), hex(want))
        run_simulation(dut, bench())
        print('PASS GFX2D: BilinearFilter matches LERP(LERP(P11,P01,u),LERP(P10,P00,u),v)')

    def _scale_case(self, w, h, sw, sh, u0, du, v0, dv, bil, op=OP_SCALE_BLIT,
                    src_addr=0x1000, src_stride=64, dst_addr=0x8000, dst_stride=None,
                    reads=None, tile_ws=None, max_cycles=None):
        # ARGB8888 texture -> ARGB8888 destination through a MOV shader.
        d = make()
        mem = bytearray(DDR_SIZE)
        dst_stride = dst_stride or 4 * w

        def texel(u, v):
            return ((u * 37 + v * 101 + 13) & 0xFF) << 24 | ((u * 11 + 7) & 0xFF) << 16 | \
                   ((v * 29 + u * 3) & 0xFF) << 8 | ((u ^ (v << 3)) & 0xFF)
        for v in range(sh):
            for u in range(sw):
                a = src_addr + v * src_stride + 4 * u
                mem[a:a + 4] = texel(u, v).to_bytes(4, 'little')
        cfg = (1 << 4) | (bil << 6) | (1 << 8) | (1 << 11) | (4 << 12)
        desc = {
            'OP': op, 'SHADER_CFG': cfg,
            'SRC_ADDR': src_addr, 'SRC_STRIDE': src_stride,
            'DST_ADDR': dst_addr, 'DST_STRIDE': dst_stride,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 4,
            'SRC_WIDTH': sw, 'SRC_HEIGHT': sh,
            'UV_START_U': u0, 'UV_DU_DX': du, 'UV_START_V': v0, 'UV_DV_DY': dv,
            **inst_regs(enc(0x00, wr=0xF, s0=4)),
        }
        self._run_op(d, self._fake_pump_factory(d, mem, reads, tile_ws), desc,
                     max_cycles=max_cycles)
        for y in range(h):
            for x in range(w):
                want = scale_ref(texel, sw, sh, u0, du, v0, dv, bil, x, y)
                a = dst_addr + y * dst_stride + 4 * x
                got = int.from_bytes(mem[a:a + 4], 'little')
                self.assertEqual(got, want, (bil, x, y, hex(got), hex(want)))

    def test_scale_upscale_row_cache(self):
        # 2x upscale: V_int = 0,0,1,1,2,2,3,3. Bilinear rows (y0, y1) reuse
        # the cached slots, so only rows 0,1 / 2 / 3 are ever fetched.
        for bil in (0, 1):
            reads = []
            self._scale_case(12, 8, 6, 4, 0, 0x8000, 0, 0x8000, bil, reads=reads)
            src_reads = [a for a in reads if 0x1000 <= a < 0x1000 + 4 * 64]
            self.assertEqual(len(src_reads), 4, (bil, src_reads))
        # OP_SHADER with SCALE_EN takes the same path.
        self._scale_case(12, 8, 6, 4, 0x4000, 0x8000, 0x2000, 0x8000, 1, op=OP_SHADER)
        print('PASS GFX2D: scaled nearest/bilinear upscale bit-exact; row cache hits')

    def test_scale_tile_span_counterexample(self):
        # U_first=0xFFFF, dU=1+1/65536, bilinear: W=1023 would need a
        # 1025-pixel span, so the step choice must pick W=1022.
        tile_ws = []
        self._scale_case(1100, 1, 1200, 2, 0xFFFF, 0x10001, 0, 0, 1,
                         src_stride=1200 * 4, dst_addr=0x4000,
                         tile_ws=tile_ws, max_cycles=20000)
        self.assertEqual([t for t in tile_ws if t], [1022, 78])
        print('PASS GFX2D: U_first=0xFFFF dU=0x10001 bilinear tiles as 1022 + 78')

    def test_scale_48bit_uv_no_wrap(self):
        # U0/V0 = 0xFFFF0000 with unit steps cross 2^32 in UQ16.16; the
        # 48-bit path keeps clamping to the right/bottom edge.
        for bil in (0, 1):
            self._scale_case(4, 3, 8, 4, 0xFFFF0000, 0x10000, 0xFFFF0000, 0x10000, bil)
        print('PASS GFX2D: 48-bit UV clamps at the edges instead of wrapping')

    def test_scale_downscale_multi_tile(self):
        # 2x horizontal downscale: W_step=512, so 600 pixels are 512 + 88
        # and U_first advances across the tile boundary.
        for bil in (0, 1):
            tile_ws = []
            self._scale_case(600, 2, 1210, 4, 0x8000, 0x20000, 0x4000, 0x18000, bil,
                             src_stride=1210 * 4, dst_addr=0x10000,
                             tile_ws=tile_ws, max_cycles=40000)
            self.assertEqual([t for t in tile_ws if t][:2], [512, 88])
        print('PASS GFX2D: 2x downscale over two tiles (512 + 88) bit-exact')

    def test_scale_r3_coords(self):
        # MOV.SAT R0, R3 exposes {V_frac, U_frac, V_clamp, U_clamp}.
        d = make()
        mem = bytearray(DDR_SIZE)
        w, h, sw, sh = 8, 2, 12, 3
        u0, du, v0, dv = 0x00028000, 0x00018000, 0x00014000, 0x00014000
        cfg = (1 << 4) | (1 << 8) | (1 << 11) | (4 << 12)
        self._run_op(d, self._fake_pump_factory(d, mem), {
            'OP': OP_SCALE_BLIT, 'SHADER_CFG': cfg,
            'SRC_ADDR': 0x1000, 'SRC_STRIDE': 64,
            'DST_ADDR': 0x4000, 'DST_STRIDE': 64,
            'WIDTH': w, 'HEIGHT': h, 'FORMAT': 4,
            'SRC_WIDTH': sw, 'SRC_HEIGHT': sh,
            'UV_START_U': u0, 'UV_DU_DX': du, 'UV_START_V': v0, 'UV_DV_DY': dv,
            **inst_regs(enc(0x00, wr=0xF, s0=3, sat=1)),
        })
        for y in range(h):
            for x in range(w):
                U, V = u0 + x * du, v0 + y * dv
                want = (((V >> 8) & 0xFF) << 24 | ((U >> 8) & 0xFF) << 16 |
                        min(V >> 16, sh - 1) << 8 | min(U >> 16, sw - 1))
                a = 0x4000 + y * 64 + 4 * x
                got = int.from_bytes(mem[a:a + 4], 'little')
                self.assertEqual(got, want, (x, y, hex(got), hex(want)))
        print('PASS GFX2D: scaled R3 = {V_frac, U_frac, V_clamp, U_clamp}')

    def test_scale_validation(self):
        mem = bytearray(DDR_SIZE)
        base = {
            'OP': OP_SCALE_BLIT, 'SRC_ADDR': 0x1000, 'SRC_STRIDE': 64,
            'DST_ADDR': 0x8000, 'DST_STRIDE': 64, 'WIDTH': 8, 'HEIGHT': 2,
            'FORMAT': 2, 'SRC_WIDTH': 8, 'SRC_HEIGHT': 2,
            'UV_DU_DX': 0x10000, 'UV_DV_DY': 0x10000,
            **inst_regs(enc(0x00, wr=0xF, s0=4)),
        }
        src, scale, bil = 1 << 4, 1 << 8, 1 << 6
        cases = [
            ({'SHADER_CFG': src}, ERR_PARAM),                       # OP 4 without SCALE_EN
            ({'OP': OP_SHADER, 'SHADER_CFG': scale}, ERR_PARAM),    # SCALE_EN without USES_SRC
            ({'OP': OP_SHADER, 'SHADER_CFG': src | bil}, ERR_PARAM),  # BILINEAR without SCALE
            ({'SHADER_CFG': src | scale, 'SRC_WIDTH': 0}, ERR_PARAM),
            ({'SHADER_CFG': src | scale, 'SRC_HEIGHT': 16385}, ERR_PARAM),
            ({'SHADER_CFG': src | scale, 'DST_ADDR': 0x1040}, ERR_PARAM),  # overlap
            ({'SHADER_CFG': src | scale, 'SRC_ADDR': DDR_SIZE - 64}, ERR_ADDR),
        ]
        for extra, err in cases:
            d = make()
            self._run_op(d, self._fake_pump_factory(d, mem), {**base, **extra},
                         expect_err=err)
        self.assertEqual(bytes(mem[0x8000:0x8100]), bytes(0x100))
        print('PASS GFX2D: scale mode dependencies, SRC dims, overlap and bounds rejected')


def lerp8(s, d, a):
    return ((s - d) * (a + (a >> 7)) + (d << 8) + 128) >> 8


def bilinear_ref(p00, p10, p01, p11, fu, fv):
    out = 0
    for sh in (0, 8, 16, 24):
        def ch(p):
            return (p >> sh) & 0xFF
        top = lerp8(ch(p10), ch(p00), fu)
        bot = lerp8(ch(p11), ch(p01), fu)
        out |= lerp8(bot, top, fv) << sh
    return out


def scale_ref(texel, sw, sh, u0, du, v0, dv, bil, x, y):
    U, V = u0 + x * du, v0 + y * dv
    ui, vi = U >> 16, V >> 16
    u_0, v_0 = min(ui, sw - 1), min(vi, sh - 1)
    if not bil:
        return texel(u_0, v_0)
    u_1, v_1 = min(ui + 1, sw - 1), min(vi + 1, sh - 1)
    return bilinear_ref(texel(u_0, v_0), texel(u_1, v_0), texel(u_0, v_1),
                        texel(u_1, v_1), (U >> 8) & 0xFF, (V >> 8) & 0xFF)


def enc(op, dst=0, wr=0xF, s0=0, s1=0, s2=0, swz0=0xE4, swz1=0xE4, swz2=0xE4,
        sat=0, use_imm=0, sub_op=0, imm=0, pred=0):
    v = ((op << 58) | (pred << 56) | (dst << 52) | (wr << 48) | (s0 << 44) |
         (s1 << 40) | (s2 << 36) | (swz0 << 28) | (swz1 << 20) | (swz2 << 12) |
         (sat << 11) | (use_imm << 10) | (sub_op << 8) | imm)
    return v & 0xFFFFFFFF, v >> 32


def inst_regs(*insts):
    regs = {}
    for k, (lo, hi) in enumerate(insts):
        regs['INST%d_LO' % k] = lo
        regs['INST%d_HI' % k] = hi
    return regs


def split_argb(v):
    return [(v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF]


def pal_entry(i):
    return (((i * 37 + 11) & 0xFF) << 24) | (((i * 3) & 0xFF) << 16) | \
           (((i * 5 + 17) & 0xFF) << 8) | (255 - i)


def lut_entry(i):
    return ((i ^ 0x5A) << 24) | (min(255, 2 * i) << 16) | (((i * i) >> 8) << 8) | (255 - i)


if __name__ == '__main__':
    unittest.main()
