"""Stage 3 host RTL verification: Affine 3D Triangle Rasterizer + TMEM + Frag3DPort."""
import math
import unittest
from migen import *
from migen.sim import run_simulation

from gateware.machines.mega138k_ae350.peripherals.gfx2d import (
    DESC_NAMES,
    Gfx2DEngine,
)
from gateware.machines.mega138k_ae350.peripherals.gfx3d_raster import TriRasterizer
from gateware.machines.mega138k_ae350.peripherals.gfx3d_tmem import (
    Tmem4Bank,
    TmemSampler,
    WRAP_REPEAT,
)

OP_MUL_UNORM8 = 0x10
SRC_R0, SRC_R2, SRC_SRC = 0, 2, 4
SWZ_ID = 0xE4


def insn(op, dst=0, wr=0xF, s0=0, s1=0, s2=0, swz0=SWZ_ID, swz1=SWZ_ID, swz2=SWZ_ID, sub_op=0):
    return (
        ((op & 0x3F) << 58)
        | ((dst & 0xF) << 52)
        | ((wr & 0xF) << 48)
        | ((s0 & 0xF) << 44)
        | ((s1 & 0xF) << 40)
        | ((s2 & 0xF) << 36)
        | ((swz0 & 0xFF) << 28)
        | ((swz1 & 0xFF) << 20)
        | ((swz2 & 0xFF) << 12)
        | ((sub_op & 3) << 8)
    )


def pack_rgba(b, g, r, a):
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def q12_4(v):
    return int(round(v * 16.0)) & 0xFFFF


def pack_xy(x, y):
    return (q12_4(y) << 16) | q12_4(x)


def q12_20(v):
    return int(round(v * (1 << 20))) & 0xFFFFFFFF


def build_tri_words(v0, v1, v2, scissor=(0, 0, 127, 127), gouraud=1, tex_en=1):
    """Build 24-word triangle packet; each vertex is (x, y, z_q16, s, t, rgba)."""
    w = [0] * 24
    w[0] = 5 | (1 << 9) | ((gouraud & 1) << 11)
    w[1] = (tex_en & 1) | (4 << 12) | (4 << 16)
    for idx, (vx, vy, vz, vs, vt, vrgba) in enumerate((v0, v1, v2)):
        base = 3 + idx * 6
        w[base + 0] = pack_xy(vx, vy)
        w[base + 1] = vz & 0xFFFFFFFF
        w[base + 2] = q12_20(vs)
        w[base + 3] = q12_20(vt)
        w[base + 4] = 0x40000000
        w[base + 5] = vrgba
    w[21] = (scissor[1] << 16) | scissor[0]
    w[22] = (scissor[3] << 16) | scissor[2]
    w[23] = 0
    return w


class Stage3EndToEndHarness(Module):
    def __init__(self):
        pal = Memory(32, 256)
        luts = [Memory(8, 256) for _ in range(4)]
        self.specials += [pal, *luts]
        pal_p = pal.get_port(clock_domain="ddr")
        lut_ps = [m.get_port(clock_domain="ddr") for m in luts]
        self.specials += [pal_p, *lut_ps]

        self.tri = TriRasterizer()
        self.tmem = Tmem4Bank(depth_per_bank=1024, clock_domain="ddr")
        self.sampler = TmemSampler(self.tmem)
        eng_raw = Gfx2DEngine(pal_p, lut_ps)
        self.eng = eng_raw

        self.submodules += [
            ClockDomainsRenamer("ddr")(self.tri),
            ClockDomainsRenamer("ddr")(self.tmem),
            ClockDomainsRenamer("ddr")(self.sampler),
            ClockDomainsRenamer("ddr")(eng_raw),
        ]

        x_d1, x_d2 = Signal(12), Signal(12)
        y_d1, y_d2 = Signal(12), Signal(12)
        z_d1, z_d2 = Signal(32), Signal(32)
        rgba_d1, rgba_d2 = Signal(32), Signal(32)
        self.sync.ddr += If(~self.sampler.stall,
            x_d1.eq(self.tri.x_out), x_d2.eq(x_d1),
            y_d1.eq(self.tri.y_out), y_d2.eq(y_d1),
            z_d1.eq(self.tri.z_out), z_d2.eq(z_d1),
            rgba_d1.eq(self.tri.rgba_out), rgba_d2.eq(rgba_d1),
        )

        self.comb += [
            self.tri.ready.eq(eng_raw.frag3d.ready),
            self.sampler.stall.eq(~eng_raw.frag3d.ready),
            self.sampler.valid_in.eq(self.tri.valid_out),
            self.sampler.u_q32_16.eq(self.tri.s_q32_16),
            self.sampler.v_q32_16.eq(self.tri.t_q32_16),

            eng_raw.frag3d.valid.eq(self.sampler.valid_out),
            eng_raw.frag3d.p00.eq(self.sampler.p00),
            eng_raw.frag3d.p10.eq(self.sampler.p10),
            eng_raw.frag3d.p01.eq(self.sampler.p01),
            eng_raw.frag3d.p11.eq(self.sampler.p11),
            eng_raw.frag3d.wu.eq(self.sampler.wu),
            eng_raw.frag3d.wv.eq(self.sampler.wv),
            eng_raw.frag3d.bounds_discard.eq(self.sampler.bounds_discard),
            eng_raw.frag3d.x.eq(x_d2),
            eng_raw.frag3d.y.eq(y_d2),
            eng_raw.frag3d.z_new.eq(z_d2[16:32]),
            eng_raw.frag3d.z_pass.eq(1),
            eng_raw.frag3d.rgba.eq(rgba_d2),
            eng_raw.frag3d.uses_dst.eq(0),
            eng_raw.frag3d.dst_adr.eq(0),
        ]


class Stage3RasterTest(unittest.TestCase):
    def test_shared_edge_seamlessness_and_guardband_area(self):
        dut = TriRasterizer()
        fb_hits = {}

        def run_tri(words, label):
            for k, val in enumerate(words):
                yield dut.words[k].eq(val)
            yield dut.start.eq(1)
            yield
            yield dut.start.eq(0)
            for _ in range(50000):
                yield
                if (yield dut.valid_out):
                    px = (yield dut.x_out)
                    py = (yield dut.y_out)
                    fb_hits[(label, px, py)] = fb_hits.get((label, px, py), 0) + 1
                if not (yield dut.busy):
                    break
            else:
                raise TimeoutError(f"{label} timed out at y={(yield dut.y_out)}")

        # 1) Axis-aligned 64x64 quad [10..74) x [10..74) split into 2 triangles
        ax_t1 = build_tri_words(
            (10, 10, 0x10000, 0, 0, pack_rgba(0, 0, 200, 128)),
            (74, 10, 0x10000, 16, 0, pack_rgba(0, 0, 200, 128)),
            (10, 74, 0x10000, 0, 16, pack_rgba(0, 0, 200, 128)),
            gouraud=0, tex_en=0,
        )
        ax_t2 = build_tri_words(
            (74, 10, 0x10000, 16, 0, pack_rgba(0, 0, 200, 128)),
            (74, 74, 0x10000, 16, 16, pack_rgba(0, 0, 200, 128)),
            (10, 74, 0x10000, 0, 16, pack_rgba(0, 0, 200, 128)),
            gouraud=0, tex_en=0,
        )

        # 2) 30-degree rotated 64x64 quad centered at (64, 64) split into 2 triangles
        ang = math.radians(30.0)
        ca, sa = math.cos(ang), math.sin(ang)
        def rot_pt(lx, ly):
            return (64.0 + lx * ca - ly * sa, 64.0 + lx * sa + ly * ca)
        p00 = rot_pt(-32, -32)
        p10 = rot_pt(32, -32)
        p01 = rot_pt(-32, 32)
        p11 = rot_pt(32, 32)
        r_t1 = build_tri_words(
            (*p00, 0x10000, 0, 0, pack_rgba(100, 200, 50, 255)),
            (*p10, 0x10000, 16, 0, pack_rgba(100, 200, 50, 255)),
            (*p01, 0x10000, 0, 16, pack_rgba(100, 200, 50, 255)),
            gouraud=0, tex_en=0,
        )
        r_t2 = build_tri_words(
            (*p10, 0x10000, 16, 0, pack_rgba(100, 200, 50, 255)),
            (*p11, 0x10000, 16, 16, pack_rgba(100, 200, 50, 255)),
            (*p01, 0x10000, 0, 16, pack_rgba(100, 200, 50, 255)),
            gouraud=0, tex_en=0,
        )

        # 3) Guard-band extreme triangle spanning [-2048, +2047]
        gb_tri = build_tri_words(
            (-2047.0, -2047.0, 0x10000, 0, 0, pack_rgba(255, 255, 255, 255)),
            (2047.0, -2047.0, 0x10000, 16, 0, pack_rgba(255, 255, 255, 255)),
            (0.0, 2047.0, 0x10000, 8, 16, pack_rgba(255, 255, 255, 255)),
            scissor=(0, 0, 31, 31), gouraud=0, tex_en=0,
        )

        def drive():
            yield from run_tri(ax_t1, "axis")
            yield from run_tri(ax_t2, "axis")
            yield from run_tri(r_t1, "rot30")
            yield from run_tri(r_t2, "rot30")
            yield from run_tri(gb_tri, "guardband")

        run_simulation(dut, drive())

        axis_pixels = {(px, py): cnt for (lbl, px, py), cnt in fb_hits.items() if lbl == "axis"}
        self.assertEqual(len(axis_pixels), 64 * 64)
        for py in range(10, 74):
            for px in range(10, 74):
                self.assertEqual(axis_pixels.get((px, py), 0), 1, f"axis gap/double at {(px, py)}")

        rot_pixels = {(px, py): cnt for (lbl, px, py), cnt in fb_hits.items() if lbl == "rot30"}
        self.assertGreater(len(rot_pixels), 3800)
        for (px, py), cnt in rot_pixels.items():
            self.assertEqual(cnt, 1, f"rot30 double-blend at {(px, py)}")
        for py in range(44, 84):
            for px in range(44, 84):
                self.assertEqual(rot_pixels.get((px, py), 0), 1, f"rot30 gap near center at {(px, py)}")

        gb_pixels = {(px, py): cnt for (lbl, px, py), cnt in fb_hits.items() if lbl == "guardband"}
        self.assertEqual(len(gb_pixels), 32 * 32)
        print("PASS GFX3D Stage 3: shared-edge seamlessness (axis + 30deg) and guard-band 35-bit area verified")

    def test_end_to_end_tri_tmem_frag3d_backend(self):
        dut = Stage3EndToEndHarness()
        eng = dut.eng
        desc_idx = {name: i for i, name in enumerate(DESC_NAMES)}

        # Small 8x8 textured + Gouraud triangle
        tri = build_tri_words(
            (0, 0, 0x10000, 0, 0, pack_rgba(255, 255, 255, 255)),
            (8, 0, 0x20000, 8, 0, pack_rgba(255, 0, 0, 255)),
            (0, 8, 0x30000, 0, 8, pack_rgba(0, 255, 0, 255)),
            scissor=(0, 0, 15, 15), gouraud=1, tex_en=1,
        )
        observed = []

        def drive():
            yield eng.frag3d.active.eq(1)
            yield eng.frag3d.tex_en.eq(1)
            yield dut.sampler.tex_en.eq(1)
            yield dut.sampler.bilinear_en.eq(0)
            yield dut.sampler.wrap_u.eq(WRAP_REPEAT)
            yield dut.sampler.wrap_v.eq(WRAP_REPEAT)
            yield dut.sampler.log2_tex_w.eq(3)
            yield dut.sampler.log2_tex_h.eq(3)

            # White 8x8 texture in TMEM
            yield dut.tmem.wr_log2_w.eq(3)
            yield dut.tmem.wr_base.eq(0)
            for ty in range(8):
                for tx in range(8):
                    yield dut.tmem.wr_u.eq(tx)
                    yield dut.tmem.wr_v.eq(ty)
                    yield dut.tmem.wr_dat.eq(0xFFFFFFFF)
                    yield dut.tmem.wr_en.eq(1)
                    yield
            yield dut.tmem.wr_en.eq(0)

            yield eng.desc[desc_idx['SHADER_CFG']].eq(0)
            m_i = insn(OP_MUL_UNORM8, dst=SRC_R0, s0=SRC_SRC, s1=SRC_R2)
            yield eng.desc[desc_idx['INST0_LO']].eq(m_i & 0xFFFFFFFF)
            yield eng.desc[desc_idx['INST0_HI']].eq((m_i >> 32) & 0xFFFFFFFF)
            yield
            yield

            for k, val in enumerate(tri):
                yield dut.tri.words[k].eq(val)
            yield dut.tri.start.eq(1)
            yield
            yield dut.tri.start.eq(0)

            for _ in range(4000):
                yield
                if (yield eng.frag3d.out_valid) and (yield eng.frag3d.out_we_color):
                    observed.append((
                        (yield eng.frag3d.out_x),
                        (yield eng.frag3d.out_y),
                        (yield eng.frag3d.out_rgba),
                    ))
                if not (yield dut.tri.busy) and not (yield eng.frag3d.pipe_busy) and not (yield dut.sampler.valid_out):
                    for _ in range(300):
                        yield
                        if (yield eng.frag3d.out_valid) and (yield eng.frag3d.out_we_color):
                            observed.append((
                                (yield eng.frag3d.out_x),
                                (yield eng.frag3d.out_y),
                                (yield eng.frag3d.out_rgba),
                            ))
                    break

        run_simulation(dut, {"ddr": drive()}, clocks={"ddr": 10})
        # 8x8 right triangle under Top-Left rule: 7+6+5+4+3+2+1 = 28 pixels
        # (hypotenuse x+y=8 is an exclusive Right edge)
        self.assertEqual(len(observed), 28)
        print("PASS GFX3D Stage 3: end-to-end TriRasterizer -> TMEM -> Frag3DPort verified (28 pixels)")


if __name__ == "__main__":
    unittest.main()
