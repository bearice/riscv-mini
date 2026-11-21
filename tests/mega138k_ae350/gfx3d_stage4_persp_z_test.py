"""Stage 4 host RTL verification: 6-DSP 1/Q perspective recovery + Late-Z consistency."""
import unittest
from migen import *
from migen.sim import run_simulation

from gateware.machines.mega138k_ae350.peripherals.gfx2d import (
    DESC_NAMES,
    Gfx2DEngine,
)
from gateware.machines.mega138k_ae350.peripherals.gfx3d_persp import (
    PerspRecipUnit,
    ZBufferUnit,
    Z_LESS,
)
from gateware.machines.mega138k_ae350.peripherals.gfx3d_raster import TriRasterizer
from gateware.machines.mega138k_ae350.peripherals.gfx3d_tmem import (
    Tmem4Bank,
    TmemSampler,
    WRAP_REPEAT,
)

OP_MOV = 0x00
OP_LERP = 0x13
OP_DISCARD_IF = 0x39
SRC_R0, SRC_R2, SRC_SRC, SRC_DST, SRC_U0 = 0, 2, 4, 5, 8
SWZ_ID = 0xE4
SWZ_AAAA = 0xFF


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


def uq2_30(v):
    return int(round(v * (1 << 30))) & 0xFFFFFFFF


def s48_to_float(v):
    if v & (1 << 47):
        v -= (1 << 48)
    return v / 65536.0


def build_persp_tri_words(verts, scissor=(0, 0, 63, 63), persp=1, gouraud=0):
    """verts: list of 3 (x, y, z_q16, u_tex, v_tex, w_depth, rgba)."""
    w_min = min(v[5] for v in verts)
    w = [0] * 24
    w[0] = 5 | (1 << 9) | ((persp & 1) << 10) | ((gouraud & 1) << 11)
    w[1] = 1 | (4 << 12) | (4 << 16)
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


class PerspPipelineHarness(Module):
    def __init__(self):
        self.tri = ClockDomainsRenamer("ddr")(TriRasterizer())
        self.persp = ClockDomainsRenamer("ddr")(PerspRecipUnit(clock_domain="ddr"))
        self.submodules += [self.tri, self.persp]

        # Delay x, y by PerspRecipUnit.LATENCY (5 cycles)
        x_d = [Signal(12) for _ in range(PerspRecipUnit.LATENCY)]
        y_d = [Signal(12) for _ in range(PerspRecipUnit.LATENCY)]
        self.sync.ddr += If(~self.persp.stall,
            x_d[0].eq(self.tri.x_out),
            y_d[0].eq(self.tri.y_out),
            *[x_d[i].eq(x_d[i - 1]) for i in range(1, PerspRecipUnit.LATENCY)],
            *[y_d[i].eq(y_d[i - 1]) for i in range(1, PerspRecipUnit.LATENCY)],
        )
        self.x_out = x_d[-1]
        self.y_out = y_d[-1]

        self.comb += [
            self.persp.valid_in.eq(self.tri.valid_out),
            self.persp.persp_en.eq(self.tri.persp_en_out),
            self.persp.s_q12_20.eq(self.tri.s_q12_20),
            self.persp.t_q12_20.eq(self.tri.t_q12_20),
            self.persp.q_clamped.eq(self.tri.q_clamped),
        ]


class LateZHarness(Module):
    def __init__(self):
        pal = Memory(32, 256)
        luts = [Memory(8, 256) for _ in range(4)]
        self.specials += [pal, *luts]
        pal_p = pal.get_port(clock_domain="ddr")
        lut_ps = [m.get_port(clock_domain="ddr") for m in luts]
        self.specials += [pal_p, *lut_ps]

        self.tri = TriRasterizer()
        self.tmem = Tmem4Bank(depth_per_bank=256, clock_domain="ddr")
        self.sampler = TmemSampler(self.tmem)
        self.zbuf = ZBufferUnit(max_width=64, clock_domain="ddr")
        eng_raw = Gfx2DEngine(pal_p, lut_ps)
        self.eng = eng_raw

        self.submodules += [
            ClockDomainsRenamer("ddr")(self.tri),
            ClockDomainsRenamer("ddr")(self.tmem),
            ClockDomainsRenamer("ddr")(self.sampler),
            ClockDomainsRenamer("ddr")(self.zbuf),
            ClockDomainsRenamer("ddr")(eng_raw),
        ]

        # Delay sideband (x, y, z, rgba) by TmemSampler.LATENCY (3 cycles)
        # Note: ZBufferUnit Early-Z read takes 1 cycle, so rd_x reads at cycle 2
        # (x_d2) and z_pass_out is ready at cycle 3 alongside sampler.valid_out!
        x_d1, x_d2, x_d3 = Signal(12), Signal(12), Signal(12)
        y_d1, y_d2, y_d3 = Signal(12), Signal(12), Signal(12)
        z_d1, z_d2, z_d3 = Signal(32), Signal(32), Signal(32)
        rgba_d1, rgba_d2, rgba_d3 = Signal(32), Signal(32), Signal(32)
        self.sync.ddr += If(~self.sampler.stall,
            x_d1.eq(self.tri.x_out), x_d2.eq(x_d1), x_d3.eq(x_d2),
            y_d1.eq(self.tri.y_out), y_d2.eq(y_d1), y_d3.eq(y_d2),
            z_d1.eq(self.tri.z_out), z_d2.eq(z_d1), z_d3.eq(z_d2),
            rgba_d1.eq(self.tri.rgba_out), rgba_d2.eq(rgba_d1), rgba_d3.eq(rgba_d2),
        )

        self.comb += [
            self.tri.ready.eq(eng_raw.frag3d.ready),
            self.sampler.stall.eq(~eng_raw.frag3d.ready),
            self.zbuf.stall.eq(~eng_raw.frag3d.ready),
            self.sampler.valid_in.eq(self.tri.valid_out),
            self.sampler.u_q32_16.eq(self.tri.s_q32_16),
            self.sampler.v_q32_16.eq(self.tri.t_q32_16),

            # Early-Z read at cycle 2 (x_d2, z_d2[16:32]) -> z_pass_out ready at cycle 3
            self.zbuf.rd_x.eq(x_d2),
            self.zbuf.z_new_in.eq(z_d2[16:32]),

            eng_raw.frag3d.valid.eq(self.sampler.valid_out),
            eng_raw.frag3d.p00.eq(self.sampler.p00),
            eng_raw.frag3d.p10.eq(self.sampler.p10),
            eng_raw.frag3d.p01.eq(self.sampler.p01),
            eng_raw.frag3d.p11.eq(self.sampler.p11),
            eng_raw.frag3d.wu.eq(self.sampler.wu),
            eng_raw.frag3d.wv.eq(self.sampler.wv),
            eng_raw.frag3d.bounds_discard.eq(self.sampler.bounds_discard),
            eng_raw.frag3d.x.eq(x_d3),
            eng_raw.frag3d.y.eq(y_d3),
            eng_raw.frag3d.z_new.eq(z_d3[16:32]),
            eng_raw.frag3d.z_pass.eq(self.zbuf.z_pass_out),
            eng_raw.frag3d.rgba.eq(rgba_d3),
            eng_raw.frag3d.dst_adr.eq(x_d3[0:8]),

            # Late-Z commit
            self.zbuf.out_valid.eq(eng_raw.frag3d.out_valid),
            self.zbuf.out_x.eq(eng_raw.frag3d.out_x),
            self.zbuf.out_z_new.eq(eng_raw.frag3d.out_z),
            self.zbuf.out_we_color_raw.eq(eng_raw.frag3d.out_we_color),
            self.zbuf.out_we_z_raw.eq(eng_raw.frag3d.out_we_z),
        ]

        # Expose DST_LB write port so testbench can preload/mirror framebuffer row
        self.dst_w = eng_raw.dst_lb.get_port(write_capable=True, clock_domain="ddr")
        self.specials += self.dst_w


class Stage4PerspZTest(unittest.TestCase):
    def test_75deg_perspective_accuracy_gate(self):
        dut = PerspPipelineHarness()
        # 75-degree pitch ground triangle: Q spans [2^-8, 1.0], |U|, |V| <= 1024
        # Post-snap Q12.4 screen vertices:
        verts = [
            (0.0, 48.0, 0x1000, 0.0, 0.0, 1.0, 0xFFFFFFFF),
            (48.0, 48.0, 0x1000, 1024.0, 0.0, 1.0, 0xFFFFFFFF),
            (24.0, 0.0, 0xF000, 512.0, 1024.0, 256.0, 0xFFFFFFFF),
        ]
        tri = build_persp_tri_words(verts, scissor=(0, 0, 47, 47), persp=1, gouraud=0)
        obs = []

        def drive():
            for k, val in enumerate(tri):
                yield dut.tri.words[k].eq(val)
            yield dut.tri.start.eq(1)
            yield
            yield dut.tri.start.eq(0)
            for _ in range(25000):
                yield
                if (yield dut.persp.valid_out):
                    obs.append((
                        (yield dut.x_out),
                        (yield dut.y_out),
                        s48_to_float((yield dut.persp.u_q32_16)),
                        s48_to_float((yield dut.persp.v_q32_16)),
                    ))
                if not (yield dut.tri.busy):
                    for _ in range(12):
                        yield
                        if (yield dut.persp.valid_out):
                            obs.append((
                                (yield dut.x_out),
                                (yield dut.y_out),
                                s48_to_float((yield dut.persp.u_q32_16)),
                                s48_to_float((yield dut.persp.v_q32_16)),
                            ))
                    break

        run_simulation(dut, {"ddr": drive()}, clocks={"ddr": 10})
        self.assertGreater(len(obs), 1000)

        # Post-snap Q12.4 floating-point golden model
        x0, y0, _, u0, v0, w0, _ = verts[0]
        x1, y1, _, u1, v1, w1, _ = verts[1]
        x2, y2, _, u2, v2, w2, _ = verts[2]
        area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)

        max_err_u, max_err_v = 0.0, 0.0
        for px, py, u_hw, v_hw in obs:
            xs, ys = px + 0.5, py + 0.5
            l0 = ((x1 - xs) * (y2 - ys) - (y1 - ys) * (x2 - xs)) / area
            l1 = ((x2 - xs) * (y0 - ys) - (y2 - ys) * (x0 - xs)) / area
            l2 = 1.0 - l0 - l1
            q_g = l0 / w0 + l1 / w1 + l2 / w2
            u_g = (l0 * (u0 / w0) + l1 * (u1 / w1) + l2 * (u2 / w2)) / q_g
            v_g = (l0 * (v0 / w0) + l1 * (v1 / w1) + l2 * (v2 / w2)) / q_g
            max_err_u = max(max_err_u, abs(u_hw - u_g))
            max_err_v = max(max_err_v, abs(v_hw - v_g))

        self.assertLessEqual(max_err_u, 0.5)
        self.assertLessEqual(max_err_v, 0.5)
        print(
            f"PASS GFX3D Stage 4: 75deg Q in [2^-8, 1.0] perspective gate "
            f"({len(obs)} px, max_err_u={max_err_u:.5f}, max_err_v={max_err_v:.5f} <= 0.5 texel)"
        )

    def test_translucent_and_discard_late_z_consistency(self):
        dut = LateZHarness()
        eng = dut.eng
        desc_idx = {name: i for i, name in enumerate(DESC_NAMES)}

        # Single scanline y=2, x in [0..7]
        # 1) Opaque wall at Z=0xCCCC (0.8), color=0xFF112233
        # 2) Fence at Z=0x4CCC (0.3), DISCARD_IF(SRC == U0):
        #    even x has colorkey 0xFF00FF00 (discarded!), odd x has 0xFF445566 (solid!)
        # 3) Translucent glass at Z=0x8000 (0.5), Z_TEST=LESS, Z_WRITE=0, 50% LERP over DST
        colorkey = 0xFF00FF00
        solid_fence = 0xFF445566
        wall_color = 0xFF112233
        glass_color = 0x80CCCCCC

        fb_row = [0] * 8
        z_row = [0xFFFF] * 8  # Cleared to far Z=0xFFFF

        def run_pass(words, shader_insts, last_stage, uses_dst, z_test, z_write, c_write, u0=0):
            # Sync DST_LB and Z_LB from software row mirror before pass
            for px in range(8):
                yield dut.dst_w.adr.eq(px)
                yield dut.dst_w.dat_w.eq(fb_row[px])
                yield dut.dst_w.we.eq(1)
                yield
            yield dut.dst_w.we.eq(0)

            yield eng.frag3d.uses_dst.eq(uses_dst)
            yield dut.zbuf.z_test_en.eq(z_test)
            yield dut.zbuf.z_write_en.eq(z_write)
            yield dut.zbuf.z_func.eq(Z_LESS)
            yield dut.zbuf.color_write_en.eq(c_write)

            yield eng.desc[desc_idx['SHADER_CFG']].eq(last_stage)
            yield eng.desc[desc_idx['UNIFORM0']].eq(u0)
            for k, val in enumerate(shader_insts):
                yield eng.desc[desc_idx[f'INST{k}_LO']].eq(val & 0xFFFFFFFF)
                yield eng.desc[desc_idx[f'INST{k}_HI']].eq((val >> 32) & 0xFFFFFFFF)
            yield
            yield

            for k, val in enumerate(words):
                yield dut.tri.words[k].eq(val)
            yield dut.tri.start.eq(1)
            yield
            yield dut.tri.start.eq(0)

            for _ in range(1500):
                yield
                if (yield dut.zbuf.we_color):
                    px = (yield eng.frag3d.out_x)
                    fb_row[px] = (yield eng.frag3d.out_rgba)
                if (yield dut.zbuf.we_z):
                    px = (yield eng.frag3d.out_x)
                    z_row[px] = (yield eng.frag3d.out_z)
                if not (yield dut.tri.busy):
                    for _ in range(400):
                        yield
                        if (yield dut.zbuf.we_color):
                            px = (yield eng.frag3d.out_x)
                            fb_row[px] = (yield eng.frag3d.out_rgba)
                        if (yield dut.zbuf.we_z):
                            px = (yield eng.frag3d.out_x)
                            z_row[px] = (yield eng.frag3d.out_z)
                    break

        # Triangle covering y=2, x=0..7 cleanly (clipped by scissor x=0..7)
        def make_span_tri(z_val, rgba_val):
            return build_persp_tri_words(
                [(0.0, 2.0, z_val, 0.0, 0.0, 1.0, rgba_val),
                 (16.0, 2.0, z_val, 16.0, 0.0, 1.0, rgba_val),
                 (0.0, 4.0, z_val, 0.0, 2.0, 1.0, rgba_val)],
                scissor=(0, 2, 7, 2), persp=0, gouraud=0,
            )

        def drive():
            yield eng.frag3d.active.eq(1)
            yield eng.frag3d.tex_en.eq(1)
            yield dut.sampler.tex_en.eq(1)
            yield dut.sampler.bilinear_en.eq(0)
            yield dut.sampler.wrap_u.eq(WRAP_REPEAT)
            yield dut.sampler.wrap_v.eq(WRAP_REPEAT)
            yield dut.sampler.log2_tex_w.eq(3)
            yield dut.sampler.log2_tex_h.eq(3)

            # Load fence texture into TMEM: even x = colorkey, odd x = solid_fence
            yield dut.tmem.wr_log2_w.eq(3)
            yield dut.tmem.wr_base.eq(0)
            for tx in range(8):
                yield dut.tmem.wr_u.eq(tx)
                yield dut.tmem.wr_v.eq(0)
                yield dut.tmem.wr_dat.eq(colorkey if (tx % 2 == 0) else solid_fence)
                yield dut.tmem.wr_en.eq(1)
                yield
            yield dut.tmem.wr_en.eq(0)

            mov_r2 = [insn(OP_MOV, dst=SRC_R0, s0=SRC_R2), insn(OP_MOV, wr=0), insn(OP_MOV, wr=0), insn(OP_MOV, wr=0)]
            fence_sh = [
                insn(OP_DISCARD_IF, s0=SRC_SRC, s1=SRC_U0, sub_op=0),
                insn(OP_MOV, dst=SRC_R0, s0=SRC_SRC),
                insn(OP_MOV, wr=0),
                insn(OP_MOV, wr=0),
            ]
            glass_sh = [
                insn(OP_LERP, dst=SRC_R0, s0=SRC_R2, s1=SRC_DST, s2=SRC_R2, swz2=SWZ_AAAA),
                insn(OP_MOV, wr=0),
                insn(OP_MOV, wr=0),
                insn(OP_MOV, wr=0),
            ]

            # Pass 1: Opaque wall Z=0xCCCC (z_test=0, z_write=1)
            yield from run_pass(make_span_tri(0xCCCC0000, wall_color), mov_r2, 0, 0, 0, 1, 1)
            # Pass 2: Fence Z=0x4CCC with DISCARD_IF
            yield from run_pass(make_span_tri(0x4CCC0000, 0xFFFFFFFF), fence_sh, 1, 0, 1, 1, 1, u0=colorkey)
            # Pass 3: Translucent glass Z=0x8000, Z_WRITE=0
            yield from run_pass(make_span_tri(0x80000000, glass_color), glass_sh, 0, 1, 1, 0, 1)

        run_simulation(dut, {"ddr": drive()}, clocks={"ddr": 10})

        # Verify:
        # - Odd x (solid fence at Z=0x4CCC): occluded glass -> color=solid_fence, Z=0x4CCC
        # - Even x (discarded fence): fence did NOT write Z (remained 0xCCCC),
        #   so glass (Z=0x8000 < 0xCCCC) passed and blended over wall_color,
        #   and Z remained 0xCCCC (because glass has Z_WRITE_EN=0)!
        for px in range(8):
            if px % 2 == 1:
                self.assertEqual(fb_row[px], solid_fence, f"px={px} solid fence occluded")
                self.assertEqual(z_row[px], 0x4CCC, f"px={px} solid fence Z")
            else:
                self.assertNotEqual(fb_row[px], wall_color, f"px={px} glass should blend through fence hole")
                self.assertEqual(z_row[px], 0xCCCC, f"px={px} Z must remain wall Z=0xCCCC")
        print("PASS GFX3D Stage 4: DISCARD_IF + Late-Z + translucent Z_WRITE=0 consistency verified")

    def test_persp_recip_stall_varying_q(self):
        from gateware.machines.mega138k_ae350.peripherals.gfx3d_persp import PerspRecipUnit

        def run_seq(stalls):
            dut = ClockDomainsRenamer("ddr")(PerspRecipUnit(clock_domain="ddr"))
            samples = [
                (1 << 20, 2 << 20, 0x40000000),  # Q=1.0
                (3 << 20, 1 << 20, 0x20000000),  # Q=0.5
                (5 << 20, 7 << 20, 0x10000000),  # Q=0.25
                (2 << 20, 3 << 20, 0x08000000),  # Q=0.125
                (4 << 20, 5 << 20, 0x30000000),  # Q=0.75
            ]
            out = []

            def sim():
                idx = 0
                cyc = 0
                yield dut.persp_en.eq(1)
                while len(out) < len(samples) and cyc < 200:
                    st = stalls[cyc] if cyc < len(stalls) else 0
                    yield dut.stall.eq(st)
                    if idx < len(samples):
                        s, t, q = samples[idx]
                        yield dut.valid_in.eq(1)
                        yield dut.s_q12_20.eq(s)
                        yield dut.t_q12_20.eq(t)
                        yield dut.q_clamped.eq(q)
                        if not st:
                            idx += 1
                    else:
                        yield dut.valid_in.eq(0)
                    yield
                    if not st and (yield dut.valid_out):
                        out.append(((yield dut.u_q32_16), (yield dut.v_q32_16)))
                    cyc += 1

            run_simulation(dut, {"ddr": sim()}, clocks={"ddr": 10})
            return out

        ref = run_seq([0] * 40)
        stalled = run_seq([0, 0, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0])
        self.assertEqual(len(stalled), len(ref))
        self.assertEqual(stalled, ref)
        print("PASS GFX3D Stage 4: PerspRecipUnit LUT freeze under stall verified")


if __name__ == "__main__":
    unittest.main()
