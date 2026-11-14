"""Integrated GFX3D pipeline host verification (TriRasterizer + Persp + TMEM + Z + Frag3DPort)."""
import unittest
from migen import *
from migen.sim import run_simulation

from gateware.machines.mega138k_ae350.peripherals.gfx2d import (
    DESC_NAMES,
    Gfx2DEngine,
)
from gateware.machines.mega138k_ae350.peripherals.gfx3d_persp import Z_LESS
from gateware.machines.mega138k_ae350.peripherals.gfx3d_pipeline import Gfx3DPipeline
from gateware.machines.mega138k_ae350.peripherals.gfx3d_tmem import WRAP_REPEAT

OP_MUL_UNORM8 = 0x10
SRC_R0, SRC_R2, SRC_SRC = 0, 2, 4
SWZ_ID = 0xE4


def insn(op, dst=0, wr=0xF, s0=0, s1=0, s2=0):
    return (
        ((op & 0x3F) << 58)
        | ((dst & 0xF) << 52)
        | ((wr & 0xF) << 48)
        | ((s0 & 0xF) << 44)
        | ((s1 & 0xF) << 40)
        | ((s2 & 0xF) << 36)
        | (SWZ_ID << 28)
        | (SWZ_ID << 20)
        | (SWZ_ID << 12)
    )


def pack_rgba(b, g, r, a):
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def q12_4(v):
    return int(round(v * 16.0)) & 0xFFFF


def pack_xy(x, y):
    return (q12_4(y) << 16) | q12_4(x)


def q12_20(v):
    return int(round(v * (1 << 20))) & 0xFFFFFFFF


def uq2_30(v):
    return int(round(v * (1 << 30))) & 0xFFFFFFFF


def build_tri(verts, scissor=(0, 0, 15, 15), persp=1, gouraud=1):
    w_min = min(v[5] for v in verts)
    w = [0] * 24
    w[0] = 5 | (1 << 9) | ((persp & 1) << 10) | ((gouraud & 1) << 11)
    w[1] = 1 | (3 << 12) | (3 << 16)
    for idx, (vx, vy, vz, vu, vv, vw, vrgba) in enumerate(verts):
        q_i = (w_min / vw) if persp else 1.0
        s_i = vu * q_i if persp else vu
        t_i = vv * q_i if persp else vv
        base = 3 + idx * 6
        w[base + 0] = pack_xy(vx, vy)
        w[base + 1] = vz & 0xFFFFFFFF
        w[base + 2] = q12_20(s_i)
        w[base + 3] = q12_20(t_i)
        w[base + 4] = uq2_30(q_i)
        w[base + 5] = vrgba
    w[21] = (scissor[1] << 16) | scissor[0]
    w[22] = (scissor[3] << 16) | scissor[2]
    w[23] = 0
    return w


class PipelineHarness(Module):
    def __init__(self):
        pal = Memory(32, 256)
        luts = [Memory(8, 256) for _ in range(4)]
        self.specials += [pal, *luts]
        pal_p = pal.get_port(clock_domain="ddr")
        lut_ps = [m.get_port(clock_domain="ddr") for m in luts]
        self.specials += [pal_p, *lut_ps]

        self.eng = Gfx2DEngine(pal_p, lut_ps)
        self.g3d = Gfx3DPipeline(self.eng.frag3d, tmem_depth_per_bank=256, z_max_width=64)
        self.submodules += [
            ClockDomainsRenamer("ddr")(self.eng),
            ClockDomainsRenamer("ddr")(self.g3d),
        ]


class Gfx3DPipelineTest(unittest.TestCase):
    def test_persp_bilinear_gouraud_and_z_overlap(self):
        dut = PipelineHarness()
        eng = dut.eng
        g3d = dut.g3d
        desc_idx = {name: i for i, name in enumerate(DESC_NAMES)}

        fb = {}
        zb = {}

        def run_tri(words, z_test, z_write):
            yield g3d.zbuf.z_test_en.eq(z_test)
            yield g3d.zbuf.z_write_en.eq(z_write)
            yield g3d.zbuf.z_func.eq(Z_LESS)
            yield g3d.zbuf.color_write_en.eq(1)
            for k, val in enumerate(words):
                yield g3d.tri.words[k].eq(val)
            yield g3d.tri.start.eq(1)
            yield
            yield g3d.tri.start.eq(0)

            for _ in range(600):
                yield
                if (yield g3d.zbuf.we_color):
                    px = (yield eng.frag3d.out_x)
                    py = (yield eng.frag3d.out_y)
                    fb[(px, py)] = (yield eng.frag3d.out_rgba)
                if (yield g3d.zbuf.we_z):
                    px = (yield eng.frag3d.out_x)
                    py = (yield eng.frag3d.out_y)
                    zb[(px, py)] = (yield eng.frag3d.out_z)
                if not (yield g3d.pipe_busy):
                    for _ in range(40):
                        yield
                        if (yield g3d.zbuf.we_color):
                            px = (yield eng.frag3d.out_x)
                            py = (yield eng.frag3d.out_y)
                            fb[(px, py)] = (yield eng.frag3d.out_rgba)
                        if (yield g3d.zbuf.we_z):
                            px = (yield eng.frag3d.out_x)
                            py = (yield eng.frag3d.out_y)
                            zb[(px, py)] = (yield eng.frag3d.out_z)
                    break

        # Far triangle (Z=0x8000, blue-ish), then near triangle (Z=0x4000, green-ish)
        far_tri = build_tri([
            (0.0, 2.0, 0x80000000, 0.0, 0.0, 1.0, pack_rgba(255, 64, 64, 255)),
            (12.0, 2.0, 0x80000000, 8.0, 0.0, 4.0, pack_rgba(255, 64, 64, 255)),
            (0.0, 4.0, 0x80000000, 0.0, 8.0, 2.0, pack_rgba(255, 64, 64, 255)),
        ], scissor=(0, 2, 11, 2), persp=1, gouraud=1)

        near_tri = build_tri([
            (4.0, 2.0, 0x40000000, 0.0, 0.0, 1.0, pack_rgba(64, 255, 64, 255)),
            (12.0, 2.0, 0x40000000, 8.0, 0.0, 2.0, pack_rgba(64, 255, 64, 255)),
            (4.0, 4.0, 0x40000000, 0.0, 8.0, 1.0, pack_rgba(64, 255, 64, 255)),
        ], scissor=(0, 2, 11, 2), persp=1, gouraud=1)

        def drive():
            yield eng.frag3d.active.eq(1)
            yield eng.frag3d.tex_en.eq(1)
            yield eng.frag3d.uses_dst.eq(0)
            yield g3d.sampler.tex_en.eq(1)
            yield g3d.sampler.bilinear_en.eq(1)
            yield g3d.sampler.wrap_u.eq(WRAP_REPEAT)
            yield g3d.sampler.wrap_v.eq(WRAP_REPEAT)
            yield g3d.sampler.log2_tex_w.eq(3)
            yield g3d.sampler.log2_tex_h.eq(3)

            yield g3d.tmem.wr_log2_w.eq(3)
            yield g3d.tmem.wr_base.eq(0)
            for ty in range(8):
                for tx in range(8):
                    yield g3d.tmem.wr_u.eq(tx)
                    yield g3d.tmem.wr_v.eq(ty)
                    yield g3d.tmem.wr_dat.eq(0xFFFFFFFF)
                    yield g3d.tmem.wr_en.eq(1)
                    yield
            yield g3d.tmem.wr_en.eq(0)

            yield eng.desc[desc_idx['SHADER_CFG']].eq(0)
            m_i = insn(OP_MUL_UNORM8, dst=SRC_R0, s0=SRC_SRC, s1=SRC_R2)
            yield eng.desc[desc_idx['INST0_LO']].eq(m_i & 0xFFFFFFFF)
            yield eng.desc[desc_idx['INST0_HI']].eq((m_i >> 32) & 0xFFFFFFFF)
            yield
            yield

            yield from run_tri(far_tri, z_test=0, z_write=1)
            yield from run_tri(near_tri, z_test=1, z_write=1)

        run_simulation(dut, {"ddr": drive()}, clocks={"ddr": 10})

        # On y=2: x=0..3 should keep far_tri (Z=0x8000, B=255),
        # x=4..7 should be overwritten by near_tri (Z=0x4000, G=255)
        for px in range(4):
            self.assertEqual(zb[(px, 2)], 0x8000)
            self.assertEqual(fb[(px, 2)] & 0xFF, 255)
        for px in range(4, 8):
            self.assertEqual(zb[(px, 2)], 0x4000)
            self.assertEqual((fb[(px, 2)] >> 8) & 0xFF, 255)
        print("PASS GFX3D Pipeline: integrated persp + bilinear TMEM + Gouraud + Late-Z verified")


if __name__ == "__main__":
    unittest.main()
