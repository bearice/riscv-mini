"""Stage 2 host RTL verification: Mode 7 scanline affine + 4-bank 2D TMEM + Frag3DPort."""
import math
import unittest
from migen import *
from migen.sim import run_simulation

from gateware.machines.mega138k_ae350.peripherals.gfx2d import (
    BilinearFilter,
    DESC_NAMES,
    Gfx2DEngine,
)
from gateware.machines.mega138k_ae350.peripherals.gfx3d_tmem import (
    Mode7ScanlineStepper,
    Tmem4Bank,
    TmemSampler,
    WRAP_BORDER_DISCARD,
    WRAP_CLAMP,
    WRAP_REPEAT,
)

OP_MOV = 0x00
SRC_R0, SRC_SRC = 0, 4
SWZ_ID = 0xE4


def insn_mov(dst=SRC_R0, s0=SRC_SRC, wr=0xF):
    return (
        ((OP_MOV & 0x3F) << 58)
        | ((dst & 0xF) << 52)
        | ((wr & 0xF) << 48)
        | ((s0 & 0xF) << 44)
        | (SWZ_ID << 28)
        | (SWZ_ID << 20)
        | (SWZ_ID << 12)
    )


def pack_rgba(b, g, r, a):
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def unpack_rgba(v):
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF)


def to_s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if (v & 0x80000000) else v


def ref_lerp_ch(s, d, w):
    return ((s - d) * w + (d << 8) + 128) >> 8


def ref_bilinear(p00, p10, p01, p11, wu, wv):
    c00, c10, c01, c11 = map(unpack_rgba, (p00, p10, p01, p11))
    out = []
    for c in range(4):
        top = ref_lerp_ch(c10[c], c00[c], wu) & 0xFF
        bot = ref_lerp_ch(c11[c], c01[c], wu) & 0xFF
        out.append(ref_lerp_ch(bot, top, wv) & 0xFF)
    return pack_rgba(*out)


def ref_mode7_scanline(tex, tex_w, tex_h, u0_q16, v0_q16, du_q16, dv_q16, width,
                       bilinear_en, wrap_u, wrap_v, border_kill_en, bg_color):
    """Software golden model matching Stage 2 Mode 7 + TMEM contract."""
    u = to_s32(u0_q16)
    v = to_s32(v0_q16)
    du = to_s32(du_q16)
    dv = to_s32(dv_q16)

    def wrap_axis(raw, size, mode):
        oob = (raw < 0) or (raw >= size)
        if mode == WRAP_REPEAT:
            return raw & (size - 1), False
        clamped = 0 if raw < 0 else (size - 1 if raw >= size else raw)
        return clamped, (oob if mode == WRAP_BORDER_DISCARD else False)

    out = []
    for x in range(width):
        u_raw0 = u >> 16
        v_raw0 = v >> 16
        u_raw1 = u_raw0 + 1
        v_raw1 = v_raw0 + 1
        wu = ((u >> 8) & 0xFF) if bilinear_en else 0
        wv = ((v >> 8) & 0xFF) if bilinear_en else 0

        u0, ou0 = wrap_axis(u_raw0, tex_w, wrap_u)
        u1, ou1 = wrap_axis(u_raw1, tex_w, wrap_u)
        v0, ov0 = wrap_axis(v_raw0, tex_h, wrap_v)
        v1, ov1 = wrap_axis(v_raw1, tex_h, wrap_v)

        oobs = [ou0 or ov0, ou1 or ov0, ou0 or ov1, ou1 or ov1]
        coords = [(u0, v0), (u1, v0), (u0, v1), (u1, v1)]
        bdisc = 1 if (border_kill_en and oobs[0]) else 0

        pix = []
        for (cx, cy), oob in zip(coords, oobs):
            if oob and not border_kill_en:
                pix.append(bg_color)
            else:
                pix.append(tex[cy * tex_w + cx])

        rgba = ref_bilinear(pix[0], pix[1], pix[2], pix[3], wu, wv)
        out.append((x, rgba, bdisc))
        u += du
        v += dv
    return out


class Stage2Harness(Module):
    def __init__(self):
        pal = Memory(32, 256)
        luts = [Memory(8, 256) for _ in range(4)]
        self.specials += [pal, *luts]
        pal_p = pal.get_port(clock_domain="ddr")
        lut_ps = [m.get_port(clock_domain="ddr") for m in luts]
        self.specials += [pal_p, *lut_ps]

        self.tmem = Tmem4Bank(depth_per_bank=1024, clock_domain="ddr")
        self.sampler = TmemSampler(self.tmem)
        self.stepper = Mode7ScanlineStepper()
        eng_raw = Gfx2DEngine(pal_p, lut_ps)
        self.eng = eng_raw

        # Wrap submodules in ddr clock domain
        self.submodules += [
            ClockDomainsRenamer("ddr")(self.tmem),
            ClockDomainsRenamer("ddr")(self.sampler),
            ClockDomainsRenamer("ddr")(self.stepper),
            ClockDomainsRenamer("ddr")(eng_raw),
        ]

        # Wire Mode7ScanlineStepper -> TmemSampler -> Gfx2DEngine.frag3d
        # Sideband x, y delayed by TmemSampler.LATENCY (2 cycles)
        x_d1 = Signal(12)
        y_d1 = Signal(12)
        x_d2 = Signal(12)
        y_d2 = Signal(12)
        self.sync.ddr += If(~self.sampler.stall,
            x_d1.eq(self.stepper.x_out),
            y_d1.eq(self.stepper.y_out),
            x_d2.eq(x_d1),
            y_d2.eq(y_d1),
        )

        self.comb += [
            self.stepper.ready.eq(eng_raw.frag3d.ready),
            self.sampler.stall.eq(~eng_raw.frag3d.ready),
            self.sampler.valid_in.eq(self.stepper.valid_out),
            self.sampler.u_q32_16.eq(self.stepper.u_q32_16),
            self.sampler.v_q32_16.eq(self.stepper.v_q32_16),

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
            eng_raw.frag3d.z_pass.eq(1),
            eng_raw.frag3d.z_new.eq(0),
            eng_raw.frag3d.rgba.eq(0xFFFFFFFF),
            eng_raw.frag3d.uses_dst.eq(0),
            eng_raw.frag3d.dst_adr.eq(0),
        ]


class Stage2Mode7TmemTest(unittest.TestCase):
    def test_mode7_rotations_and_4corner_wrap_modes(self):
        dut = Stage2Harness()
        eng = dut.eng
        desc_idx = {name: i for i, name in enumerate(DESC_NAMES)}

        log2_w, log2_h = 4, 4  # 16x16 texture
        tex_w, tex_h = 1 << log2_w, 1 << log2_h
        tex = [
            pack_rgba((x * 17 + y * 5) & 0xFF,
                      (x * 7 + y * 23) & 0xFF,
                      (x * 31 + y * 11) & 0xFF,
                      0xFF)
            for y in range(tex_h) for x in range(tex_w)
        ]
        bg_color = pack_rgba(0xAA, 0xBB, 0xCC, 0xDD)

        def q16(f):
            return int(round(f * 65536.0)) & 0xFFFFFFFF

        # Test cases covering 90/180/270/37 deg rotations, negative gradients,
        # rightmost/bottommost REPEAT, CLAMP broadcast, and BORDER_DISCARD
        cases = [
            # (name, u0, v0, du, dv, width, bil, wu_mode, wv_mode, bkill)
            ("rot_90_repeat", 15.5, 0.25, 0.0, 1.0, 16, 1, WRAP_REPEAT, WRAP_REPEAT, 0),
            ("rot_180_repeat", 15.25, 15.75, -1.0, 0.0, 16, 1, WRAP_REPEAT, WRAP_REPEAT, 0),
            ("rot_270_repeat", 0.5, 15.25, 0.0, -1.0, 16, 1, WRAP_REPEAT, WRAP_REPEAT, 0),
            ("rot_37_repeat", 2.3, 1.7, math.cos(math.radians(37)), math.sin(math.radians(37)),
             24, 1, WRAP_REPEAT, WRAP_REPEAT, 0),
            ("rot_37_neg_nearest", 14.0, 13.0, -math.cos(math.radians(37)), -math.sin(math.radians(37)),
             20, 0, WRAP_REPEAT, WRAP_REPEAT, 0),
            ("edge_clamp_broadcast", 14.25, 14.75, 0.5, 0.25, 8, 1, WRAP_CLAMP, WRAP_CLAMP, 0),
            ("neg_clamp_broadcast", -1.5, -0.5, 0.5, 0.5, 8, 1, WRAP_CLAMP, WRAP_CLAMP, 0),
            ("border_bg_replace", -1.25, 14.5, 0.5, 0.25, 10, 1, WRAP_BORDER_DISCARD, WRAP_BORDER_DISCARD, 0),
            ("border_kill_en", -1.25, 14.5, 0.5, 0.25, 10, 1, WRAP_BORDER_DISCARD, WRAP_BORDER_DISCARD, 1),
        ]

        results = {}

        def drive_and_check():
            yield eng.frag3d.active.eq(1)
            # Load shader: Stage 0 = MOV R0, SRC
            yield eng.desc[desc_idx['SHADER_CFG']].eq(0)
            mov_i = insn_mov(dst=SRC_R0, s0=SRC_SRC, wr=0xF)
            yield eng.desc[desc_idx['INST0_LO']].eq(mov_i & 0xFFFFFFFF)
            yield eng.desc[desc_idx['INST0_HI']].eq((mov_i >> 32) & 0xFFFFFFFF)

            # Load 16x16 texture into Tmem4Bank
            yield dut.tmem.wr_log2_w.eq(log2_w)
            yield dut.tmem.wr_base.eq(0)
            for ty in range(tex_h):
                for tx in range(tex_w):
                    yield dut.tmem.wr_u.eq(tx)
                    yield dut.tmem.wr_v.eq(ty)
                    yield dut.tmem.wr_dat.eq(tex[ty * tex_w + tx])
                    yield dut.tmem.wr_en.eq(1)
                    yield
            yield dut.tmem.wr_en.eq(0)
            yield

            for cname, u0, v0, du, dv, width, bil, wu_m, wv_m, bkill in cases:
                yield dut.sampler.tex_en.eq(1)
                yield eng.frag3d.tex_en.eq(1)
                yield dut.sampler.bilinear_en.eq(bil)
                yield dut.sampler.wrap_u.eq(wu_m)
                yield dut.sampler.wrap_v.eq(wv_m)
                yield dut.sampler.border_kill_en.eq(bkill)
                yield dut.sampler.log2_tex_w.eq(log2_w)
                yield dut.sampler.log2_tex_h.eq(log2_h)
                yield dut.sampler.tmem_base.eq(0)
                yield dut.sampler.bg_color.eq(bg_color)

                yield dut.stepper.width.eq(width)
                yield dut.stepper.y_in.eq(0)
                yield dut.stepper.u0_q16_16.eq(q16(u0))
                yield dut.stepper.v0_q16_16.eq(q16(v0))
                yield dut.stepper.du_dx_q16_16.eq(q16(du))
                yield dut.stepper.dv_dx_q16_16.eq(q16(dv))
                yield dut.stepper.start.eq(1)
                yield
                yield dut.stepper.start.eq(0)

                obs = []
                # Collect until pipe_busy goes low after stepper finishes
                for _ in range(width + 45):
                    yield
                    if (yield eng.frag3d.out_valid):
                        obs.append((
                            (yield eng.frag3d.out_x),
                            (yield eng.frag3d.out_rgba),
                            (yield eng.frag3d.out_bounds_discard),
                        ))
                    if not (yield dut.stepper.busy) and not (yield eng.frag3d.pipe_busy) and len(obs) == width:
                        break
                results[cname] = (obs, (u0, v0, du, dv, width, bil, wu_m, wv_m, bkill))

        run_simulation(dut, {"ddr": drive_and_check()}, clocks={"ddr": 10})

        for cname, (obs, params) in results.items():
            u0, v0, du, dv, width, bil, wu_m, wv_m, bkill = params
            exp = ref_mode7_scanline(
                tex, tex_w, tex_h, q16(u0), q16(v0), q16(du), q16(dv),
                width, bil, wu_m, wv_m, bkill, bg_color,
            )
            self.assertEqual(len(obs), len(exp), f"{cname}: count mismatch")
            for idx, (o, e) in enumerate(zip(obs, exp)):
                self.assertEqual(
                    o, e,
                    f"{cname} px={idx}: got (x={o[0]}, rgba=0x{o[1]:08x}, bdisc={o[2]}) "
                    f"expected (x={e[0]}, rgba=0x{e[1]:08x}, bdisc={e[2]})",
                )
        print(f"PASS GFX3D Stage 2: {len(cases)} Mode 7 + 4-Bank TMEM scanlines bit-exact")


if __name__ == "__main__":
    unittest.main()
