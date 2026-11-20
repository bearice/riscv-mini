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

        def cpu():
            reg = amap.GFX3D_REGS
            fb_addr = 0x10000
            stride = 64

            # Configure 4x4 RGBA8888 texture in TMEM (log2_w=2, log2_h=2, tex_en=1, bilinear=0)
            tex_cfg = (1 << 0) | (2 << 5) | (2 << 8)
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
            p_addr = fb_addr + (2 * stride + 2) * 4
            beat = ddr_mem.get(p_addr & ~31, 0)
            shift = 8 * (p_addr & 31)
            px_val = (beat >> shift) & 0xFFFFFFFF
            self.assertEqual(px_val, 0xFF800000, f"got 0x{px_val:08x}")

        run_simulation(
            dut,
            {"sys": cpu(), "ddr": ddr_responder()},
            clocks={"sys": 20, "ddr": 10},
        )


if __name__ == "__main__":
    unittest.main()
