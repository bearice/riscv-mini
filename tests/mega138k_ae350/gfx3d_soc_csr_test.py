"""Host simulation test for GFX3D integrated into Gfx2D APB + DDR3UI."""
import unittest

from migen import *
from migen.sim import passive, run_simulation

from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.peripherals.gfx2d import Gfx2D


class TestGfx3DSoCCSR(unittest.TestCase):
    def test_csr_textured_triangle_to_ddr3(self):
        dut = Gfx2D(ddr_size=1 << 20)
        ddr_mem = {}  # beat_addr (32B aligned) -> 256-bit int

        def apb_write(off, val):
            yield dut.selected.eq(1)
            yield dut.write.eq(1)
            yield dut.off.eq(off)
            yield dut.wdata.eq(val)
            yield dut.commit.eq(0)
            yield
            yield dut.commit.eq(1)
            yield
            yield dut.commit.eq(0)
            yield dut.selected.eq(0)
            yield dut.write.eq(0)

        def apb_read(off):
            yield dut.selected.eq(1)
            yield dut.write.eq(0)
            yield dut.off.eq(off)
            yield dut.commit.eq(0)
            yield
            val = yield dut.rdata
            yield dut.selected.eq(0)
            return val

        @passive
        def ddr_responder():
            yield dut.ui.cmd_ready.eq(1)
            yield dut.ui.wr_data_rdy.eq(1)
            yield dut.ui.rd_data_valid.eq(0)
            while True:
                cmd_en = yield dut.ui.cmd_en
                cmd = yield dut.ui.cmd
                wr_en = yield dut.ui.wr_data_en
                if cmd_en and cmd == 0 and wr_en:
                    addr = yield dut.ui.addr
                    wdata = yield dut.ui.wr_data
                    mask_n = yield dut.ui.wr_data_mask  # active-low
                    byte_addr = addr << 2
                    beat = ddr_mem.get(byte_addr, 0)
                    for b in range(32):
                        if not ((mask_n >> b) & 1):
                            byte_val = (wdata >> (8 * b)) & 0xFF
                            beat = (beat & ~(0xFF << (8 * b))) | (byte_val << (8 * b))
                    ddr_mem[byte_addr] = beat
                    yield
                elif cmd_en and cmd == 1:
                    addr = yield dut.ui.addr
                    byte_addr = addr << 2
                    beat = ddr_mem.get(byte_addr, 0)
                    yield dut.ui.rd_data.eq(beat)
                    yield dut.ui.rd_data_valid.eq(1)
                    yield
                    yield dut.ui.rd_data_valid.eq(0)
                else:
                    yield

        def cpu():
            reg = amap.GFX3D_REGS
            fb_addr = 0x10000
            stride = 64

            # Configure ShaderALU INST0 = MUL_UNORM8(R0, SRC, R2) so textured
            # (with RGBA=0xFFFFFFFF) and Gouraud (with tex_en=0) both output R0.
            reg2d = amap.GFX2D_REGS
            yield from apb_write(reg2d["SHADER_CFG"], 0)
            yield from apb_write(reg2d["INST0_LO"], 0x4E4E4000)
            yield from apb_write(reg2d["INST0_HI"], 0x400F420E)

            # Configure 4x4 RGBA8888 texture in TMEM (log2_w=2, log2_h=2, tex_en=1, bilinear=0)
            tex_cfg = (1 << 0) | (2 << 7) | (2 << 10)
            yield from apb_write(reg["FB_ADDR"], fb_addr)
            yield from apb_write(reg["FB_STRIDE"], stride)
            yield from apb_write(reg["TEX_CFG"], tex_cfg)
            yield from apb_write(reg["BG_COLOR"], 0xFF000000)
            yield from apb_write(reg["Z_CFG"], (0 << 0) | (0 << 1) | (7 << 2) | (1 << 5))
            yield from apb_write(reg["TMEM_ADDR"], 0)
            for v in range(4):
                for u in range(4):
                    texel = 0xFF000000 | (u * 60) | ((v * 60) << 8) | (0x80 << 16)
                    yield from apb_write(reg["TMEM_DATA"], texel)

            # Wait a few cycles for TMEM CDC writes to settle in ddr domain
            for _ in range(10):
                yield

            # Flat-top / right-angle triangle: (2,2), (6,2), (2,6)
            # 12.4 fixed-point coords
            x0, y0 = 2 << 4, 2 << 4
            x1, y1 = 6 << 4, 2 << 4
            x2, y2 = 2 << 4, 6 << 4
            tri_words = [0] * 24
            # V0 = (2,2), V1 = (6,2), V2 = (2,6)
            tri_words[3] = (y0 << 16) | x0
            tri_words[4] = 0x10000000   # Z0
            tri_words[5] = 0            # S0 = 0
            tri_words[6] = 0            # T0 = 0
            tri_words[7] = 1 << 30      # Q0 = 1.0 (UQ2.30)
            tri_words[8] = 0xFFFFFFFF   # RGBA0

            tri_words[9] = (y1 << 16) | x1
            tri_words[10] = 0x10000000  # Z1
            tri_words[11] = 3 << 20     # S1 = 3.0 (Q12.20)
            tri_words[12] = 0           # T1 = 0
            tri_words[13] = 1 << 30     # Q1 = 1.0
            tri_words[14] = 0xFFFFFFFF  # RGBA1

            tri_words[15] = (y2 << 16) | x2
            tri_words[16] = 0x10000000  # Z2
            tri_words[17] = 0           # S2 = 0
            tri_words[18] = 3 << 20     # T2 = 3.0
            tri_words[19] = 1 << 30     # Q2 = 1.0
            tri_words[20] = 0xFFFFFFFF  # RGBA2

            tri_words[21] = (0 << 16) | 0        # SC_MIN (0, 0)
            tri_words[22] = (63 << 16) | 63      # SC_MAX (63, 63)
            tri_words[23] = 0

            for i, w in enumerate(tri_words):
                yield from apb_write(reg[f"TRI{i}"], w)

            yield from apb_write(reg["CTRL"], 1)

            for it in range(600):
                st = yield from apb_read(reg["STATUS"])
                if not (st & 1):
                    break
                yield
            else:
                self.fail("GFX3D timed out")

            st = yield from apb_read(reg["STATUS"])
            pix = yield from apb_read(reg["PIXELS_OUT"])
            self.assertEqual(st & 0x7, 0x2, f"unexpected status 0x{st:x}")
            self.assertGreater(pix, 0)

            # Read pixel (2, 2) from DDR3 (should be texel (0,0) = 0xFF800000)
            def read_fb(px, py):
                p_addr = fb_addr + (py * stride + px) * 4
                beat = ddr_mem.get(p_addr & ~31, 0)
                shift = 8 * (p_addr & 31)
                return (beat >> shift) & 0xFFFFFFFF

            def read_zb(zbuf_addr, px, py):
                z_addr = zbuf_addr + (py * stride + px) * 2
                beat = ddr_mem.get(z_addr & ~31, 0)
                shift = 8 * (z_addr & 31)
                return (beat >> shift) & 0xFFFF

            def init_zb(zbuf_addr, rows, val=0xFFFF):
                fill_beat = 0
                for i in range(16):
                    fill_beat |= (val << (16 * i))
                for py in range(rows):
                    for blk in range(stride // 16):
                        ddr_mem[zbuf_addr + (py * stride + blk * 16) * 2] = fill_beat

            def submit_and_wait(words, max_it=1200):
                for i, w in enumerate(words):
                    yield from apb_write(reg[f"TRI{i}"], w)
                yield from apb_write(reg["CTRL"], 1)
                for _ in range(max_it):
                    st = yield from apb_read(reg["STATUS"])
                    if not (st & 1):
                        break
                    yield
                else:
                    self.fail("GFX3D timed out")
                st = yield from apb_read(reg["STATUS"])
                pix = yield from apb_read(reg["PIXELS_OUT"])
                return st, pix

            px_val = read_fb(2, 2)
            self.assertEqual(px_val, 0xFF800000, f"got 0x{px_val:08x}")

            # 2. Single-pixel triangle (verifies intermediate valid stages in pipe_busy)
            one_px = [0] * 24
            one_px[0] = 1 << 11  # GOURAUD_EN
            one_px[3] = (10 << 20) | (10 << 4)
            one_px[4] = 0x20000000
            one_px[7] = 1 << 30
            one_px[8] = 0xFF112233
            one_px[9] = (10 << 20) | ((11 << 4) + 8)
            one_px[10] = 0x20000000
            one_px[13] = 1 << 30
            one_px[14] = 0xFF112233
            one_px[15] = ((11 << 4) + 8) << 16 | (10 << 4)
            one_px[16] = 0x20000000
            one_px[19] = 1 << 30
            one_px[20] = 0xFF112233
            one_px[21] = 0
            one_px[22] = (63 << 16) | 63
            yield from apb_write(reg["TEX_CFG"], 0)
            yield from apb_write(reg["Z_CFG"], (0 << 0) | (0 << 1) | (7 << 2) | (1 << 5))
            st, pix = yield from submit_and_wait(one_px)
            self.assertEqual(st & 0x7, 0x2, f"1px status 0x{st:x}")
            self.assertEqual(pix, 1, f"expected 1 pixel, got {pix}")
            self.assertEqual(read_fb(10, 10), 0xFF112233)

            # 3. TEX_CFG[23] (uses_dst) must be rejected with ERR_PARAM
            yield from apb_write(reg["TEX_CFG"], 1 << 23)
            st, pix = yield from submit_and_wait(one_px)
            self.assertEqual(st & 0x7, 0x6, f"expected DONE|ERR_PARAM, got 0x{st:x}")
            self.assertEqual(pix, 0)
            yield from apb_write(reg["TEX_CFG"], 0)

            # 3b. 2-bit wrap mode (WRAP_BORDER_DISCARD=2) with BG_COLOR substitution
            # Sample S=-1.0 (out of bounds) -> must output BG_COLOR 0xFF123456
            b_tri = list(one_px)
            b_tri[0] = 0
            b_tri[5] = b_tri[11] = b_tri[17] = (-1 << 20) & 0xFFFFFFFF  # S = -1.0
            b_tri[6] = b_tri[12] = b_tri[18] = 0
            b_tri[8] = b_tri[14] = b_tri[20] = 0xFFFFFFFF
            yield from apb_write(reg["BG_COLOR"], 0xFF123456)
            yield from apb_write(reg["TEX_CFG"], (1 << 0) | (2 << 2) | (2 << 4) | (2 << 7) | (2 << 10))
            st, pix = yield from submit_and_wait(b_tri)
            self.assertEqual(st & 0x7, 0x2, f"border status 0x{st:x}")
            self.assertEqual(pix, 1)
            self.assertEqual(read_fb(10, 10), 0xFF123456)
            yield from apb_write(reg["TEX_CFG"], 0)

            # 4. Multi-row DDR3 Z-buffer preservation + depth-only pass (COLOR_WRITE_EN=0)
            zbuf_addr = 0x40000
            init_zb(zbuf_addr, 32, 0xFFFF)
            yield from apb_write(reg["ZBUF_ADDR"], zbuf_addr)

            # Depth-only pass (color_write_en=0, z_test_en=1, z_write_en=1, Z_LESS=1)
            # Multi-row triangle covering y=20..23, x=4..7 at Z=0x4000
            z_tri = [0] * 24
            z_tri[0] = 0
            z_tri[3] = (20 << 20) | (4 << 4)
            z_tri[4] = 0x40000000
            z_tri[7] = 1 << 30
            z_tri[8] = 0xFFAA5500
            z_tri[9] = (20 << 20) | (8 << 4)
            z_tri[10] = 0x40000000
            z_tri[13] = 1 << 30
            z_tri[14] = 0xFFAA5500
            z_tri[15] = (24 << 20) | (4 << 4)
            z_tri[16] = 0x40000000
            z_tri[19] = 1 << 30
            z_tri[20] = 0xFFAA5500
            z_tri[21] = 0
            z_tri[22] = (63 << 16) | 63
            yield from apb_write(reg["Z_CFG"], (1 << 0) | (1 << 1) | (1 << 2) | (0 << 5))
            st, pix = yield from submit_and_wait(z_tri, max_it=2500)
            self.assertEqual(st & 0x7, 0x2, f"depth-only status 0x{st:x}")
            self.assertEqual(pix, 0, "depth-only pass must not push color pixels")
            self.assertEqual(read_fb(4, 20), 0, "depth-only pass must not write FB")
            self.assertEqual(read_zb(zbuf_addr, 4, 20), 0x4000)
            self.assertEqual(read_zb(zbuf_addr, 4, 21), 0x4000)
            self.assertEqual(read_zb(zbuf_addr, 4, 22), 0x4000)
            self.assertEqual(read_zb(zbuf_addr, 4, 19), 0xFFFF, "row above must remain untouched")

            # Now draw a FARTHER triangle (Z=0x6000) at y=20..22 with Z_LESS + COLOR_WRITE_EN=1:
            # must be occluded on row 20 AND row 21, while row 24 (where Z=0xFFFF) passes!
            # Also verify shared shader registers stay locked while g3d_busy=1.
            far_tri = list(z_tri)
            far_tri[4] = far_tri[10] = far_tri[16] = 0x60000000
            far_tri[8] = far_tri[14] = far_tri[20] = 0xFF00CCFF
            far_tri[15] = (26 << 20) | (4 << 4)
            yield from apb_write(reg["Z_CFG"], (1 << 0) | (1 << 1) | (1 << 2) | (1 << 5))
            for i, w in enumerate(far_tri):
                yield from apb_write(reg[f"TRI{i}"], w)
            yield from apb_write(reg["CTRL"], 1)
            # Attempt to overwrite INST0_LO and UNIFORM0 while 3D command is busy
            yield from apb_write(reg2d["INST0_LO"], 0xDEADBEEF)
            yield from apb_write(reg2d["UNIFORM0"], 0xCAFEBABE)
            for _ in range(2500):
                st = yield from apb_read(reg["STATUS"])
                if not (st & 1):
                    break
                yield
            else:
                self.fail("GFX3D timed out")
            self.assertEqual((yield from apb_read(reg2d["INST0_LO"])), 0x4E4E4000, "INST0_LO must stay locked during g3d_busy")
            self.assertEqual((yield from apb_read(reg2d["UNIFORM0"])), 0, "UNIFORM0 must stay locked during g3d_busy")
            st = yield from apb_read(reg["STATUS"])
            self.assertEqual(st & 0x7, 0x2, f"far tri status 0x{st:x}")
            self.assertEqual(read_fb(4, 20), 0, "row 20 must be occluded by Z=0x4000")
            self.assertEqual(read_fb(4, 21), 0, "row 21 must be occluded by Z=0x4000")
            self.assertEqual(read_fb(4, 24), 0xFF00CCFF, "row 24 must pass against Z=0xFFFF")
            self.assertEqual(read_zb(zbuf_addr, 4, 24), 0x6000)

        run_simulation(
            dut,
            {"sys": cpu(), "ddr": ddr_responder()},
            clocks={"sys": 20, "ddr": 10},
        )


if __name__ == "__main__":
    unittest.main()
