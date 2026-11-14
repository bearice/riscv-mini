"""Stage 2a host RTL verification for the shared 2D/3D pixel backend."""
import unittest
from migen import *
from migen.sim import run_simulation

from gateware.machines.mega138k_ae350.peripherals.gfx2d import (
    BilinearFilter,
    DESC_NAMES,
    Gfx2DEngine,
)

# Shader ISA opcodes (matches gfx2d.py / gfx2d-shader-core.md)
OP_MOV = 0x00
OP_MUL_UNORM8 = 0x10
OP_LERP = 0x13
OP_DISCARD_IF = 0x39

SRC_R0, SRC_R1, SRC_R2, SRC_SRC, SRC_DST = 0, 1, 2, 4, 5
SRC_U0 = 8
SWZ_ID = 0xE4  # ch0=0, ch1=1, ch2=2, ch3=3
SWZ_AAAA = 0xFF  # all channels read ch3 (.a)


def insn(op, dst=0, wr=0xF, s0=0, s1=0, s2=0, swz0=SWZ_ID, swz1=SWZ_ID, swz2=SWZ_ID,
         pred=0, sat=0, use_imm=0, sub_op=0, imm8=0):
    return (
        ((op & 0x3F) << 58)
        | ((pred & 0x3) << 56)
        | ((dst & 0xF) << 52)
        | ((wr & 0xF) << 48)
        | ((s0 & 0xF) << 44)
        | ((s1 & 0xF) << 40)
        | ((s2 & 0xF) << 36)
        | ((swz0 & 0xFF) << 28)
        | ((swz1 & 0xFF) << 20)
        | ((swz2 & 0xFF) << 12)
        | ((sat & 1) << 11)
        | ((use_imm & 1) << 10)
        | ((sub_op & 3) << 8)
        | (imm8 & 0xFF)
    )


def pack_rgba(b, g, r, a):
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def unpack_rgba(v):
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF)


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


def ref_mul_unorm8(a, b):
    ca, cb = unpack_rgba(a), unpack_rgba(b)
    out = []
    for c in range(4):
        raw = ca[c] * cb[c] + 128
        out.append((raw + (raw >> 8)) >> 8)
    return pack_rgba(*out)


def ref_lerp_rgba(s, d, w_rgba):
    cs, cd, cw = unpack_rgba(s), unpack_rgba(d), unpack_rgba(w_rgba)
    out = []
    for c in range(4):
        w = cw[c] + (cw[c] >> 7)
        v = ref_lerp_ch(cs[c], cd[c], w)
        out.append(max(0, min(255, v)))
    return pack_rgba(*out)


class Stage2aHarness(Module):
    def __init__(self):
        pal = Memory(32, 256)
        luts = [Memory(8, 256) for _ in range(4)]
        self.specials += [pal, *luts]
        pal_p = pal.get_port(clock_domain="ddr")
        lut_ps = [m.get_port(clock_domain="ddr") for m in luts]
        self.specials += [pal_p, *lut_ps]

        eng_raw = Gfx2DEngine(pal_p, lut_ps)
        self.eng = eng_raw
        self.submodules.eng_ddr = ClockDomainsRenamer("ddr")(eng_raw)
        # Expose write port to DST_LB for testbench preload
        self.dst_w = eng_raw.dst_lb.get_port(write_capable=True, clock_domain="ddr")
        self.specials += self.dst_w


class Stage2aBackendTest(unittest.TestCase):
    def test_3d_fragment_injection_dst_lb_sideband_and_drain(self):
        dut = Stage2aHarness()
        eng = dut.eng
        desc_idx = {name: i for i, name in enumerate(DESC_NAMES)}

        # Command 1: Texture * Gouraud(R2), DISCARD_IF(R0 == U0, sub_op=0 EQ),
        # then LERP(R0, DST, R2.a) into R0
        colorkey = pack_rgba(0x12, 0x34, 0x56, 0xFF)
        cmd1_insts = [
            insn(OP_MUL_UNORM8, dst=SRC_R0, s0=SRC_SRC, s1=SRC_R2),
            insn(OP_DISCARD_IF, s0=SRC_R0, s1=SRC_U0, sub_op=0),
            insn(OP_LERP, dst=SRC_R0, s0=SRC_R0, s1=SRC_DST, s2=SRC_R2, swz2=SWZ_AAAA),
            insn(OP_MOV, wr=0),
        ]

        # Command 2 (after drain): tex_en=0 -> SRC=0xFFFFFFFF, MUL_UNORM8(SRC, R2) == R2
        cmd2_insts = [
            insn(OP_MUL_UNORM8, dst=SRC_R0, s0=SRC_SRC, s1=SRC_R2),
            insn(OP_MOV, wr=0),
            insn(OP_MOV, wr=0),
            insn(OP_MOV, wr=0),
        ]

        vecs_cmd1 = [
            # (p00, p10, p01, p11, wu, wv, rgba_r2, dst_bg, x, y, z, z_pass, bdisc, stall_before)
            (pack_rgba(100, 150, 200, 255), pack_rgba(120, 160, 210, 255),
             pack_rgba(110, 170, 220, 255), pack_rgba(130, 180, 230, 255),
             64, 128, pack_rgba(255, 128, 64, 128), pack_rgba(20, 40, 60, 255),
             10, 20, 0x1111, 1, 0, False),
            # z_pass = 0 -> should kill fragment
            (pack_rgba(200, 100, 50, 255), pack_rgba(200, 100, 50, 255),
             pack_rgba(200, 100, 50, 255), pack_rgba(200, 100, 50, 255),
             0, 0, pack_rgba(255, 255, 255, 255), pack_rgba(10, 10, 10, 255),
             11, 20, 0x2222, 0, 0, False),
            # bounds_discard = 1 -> should kill fragment
            (pack_rgba(80, 90, 100, 255), pack_rgba(80, 90, 100, 255),
             pack_rgba(80, 90, 100, 255), pack_rgba(80, 90, 100, 255),
             0, 0, pack_rgba(255, 255, 255, 200), pack_rgba(30, 40, 50, 255),
             12, 20, 0x3333, 1, 1, True),
            # shader_discard via colorkey: tex=colorkey, R2=white -> R0==colorkey -> discard
            (colorkey, colorkey, colorkey, colorkey,
             0, 0, pack_rgba(255, 255, 255, 255), pack_rgba(0, 0, 0, 255),
             13, 20, 0x4444, 1, 0, False),
        ]

        vecs_cmd2 = [
            (pack_rgba(1, 2, 3, 4), 0, 0, 0, 0, 0,
             pack_rgba(42, 84, 126, 255), pack_rgba(99, 99, 99, 255),
             100, 200, 0x5555, 1, 0, False),
        ]

        observed = []

        def set_cmd(insts, last_stage, u0=0):
            yield eng.desc[desc_idx['SHADER_CFG']].eq(last_stage & 3)
            yield eng.desc[desc_idx['UNIFORM0']].eq(u0)
            for k, val in enumerate(insts):
                yield eng.desc[desc_idx[f'INST{k}_LO']].eq(val & 0xFFFFFFFF)
                yield eng.desc[desc_idx[f'INST{k}_HI']].eq((val >> 32) & 0xFFFFFFFF)

        def drive():
            yield eng.frag3d.active.eq(1)
            # Preload DST_LB slots 0..3
            for idx, v in enumerate(vecs_cmd1):
                yield dut.dst_w.adr.eq(idx)
                yield dut.dst_w.dat_w.eq(v[7])
                yield dut.dst_w.we.eq(1)
                yield
            yield dut.dst_w.we.eq(0)

            # Load Command 1
            yield from set_cmd(cmd1_insts, last_stage=2, u0=colorkey)
            yield
            yield

            # Stream Command 1 fragments
            for idx, v in enumerate(vecs_cmd1):
                p00, p10, p01, p11, wu, wv, rgba, _bg, x, y, z, zp, bd, stall = v
                if stall:
                    yield eng.frag3d.stall_in.eq(1)
                    yield eng.frag3d.valid.eq(1)
                    yield
                    self.assertEqual((yield eng.frag3d.ready), 0)
                    yield eng.frag3d.stall_in.eq(0)
                yield eng.frag3d.valid.eq(1)
                yield eng.frag3d.tex_en.eq(1)
                yield eng.frag3d.uses_dst.eq(1)
                yield eng.frag3d.p00.eq(p00)
                yield eng.frag3d.p10.eq(p10)
                yield eng.frag3d.p01.eq(p01)
                yield eng.frag3d.p11.eq(p11)
                yield eng.frag3d.wu.eq(wu)
                yield eng.frag3d.wv.eq(wv)
                yield eng.frag3d.rgba.eq(rgba)
                yield eng.frag3d.dst_adr.eq(idx)
                yield eng.frag3d.x.eq(x)
                yield eng.frag3d.y.eq(y)
                yield eng.frag3d.z_new.eq(z)
                yield eng.frag3d.z_pass.eq(zp)
                yield eng.frag3d.bounds_discard.eq(bd)
                yield

            yield eng.frag3d.valid.eq(0)
            # Drain until pipe_busy == 0 before switching to Command 2
            while (yield eng.frag3d.pipe_busy):
                yield

            # Switch to Command 2 (tex_en=0, untextured Gouraud pass-through)
            yield from set_cmd(cmd2_insts, last_stage=0, u0=0)
            yield
            yield
            p00, p10, p01, p11, wu, wv, rgba, _bg, x, y, z, zp, bd, _ = vecs_cmd2[0]
            yield eng.frag3d.valid.eq(1)
            yield eng.frag3d.tex_en.eq(0)
            yield eng.frag3d.uses_dst.eq(0)
            yield eng.frag3d.p00.eq(p00)
            yield eng.frag3d.rgba.eq(rgba)
            yield eng.frag3d.x.eq(x)
            yield eng.frag3d.y.eq(y)
            yield eng.frag3d.z_new.eq(z)
            yield eng.frag3d.z_pass.eq(zp)
            yield eng.frag3d.bounds_discard.eq(bd)
            yield
            yield eng.frag3d.valid.eq(0)
            while (yield eng.frag3d.pipe_busy):
                yield
            for _ in range(4):
                yield

        def monitor():
            for _ in range(200):
                if (yield eng.frag3d.out_valid):
                    observed.append((
                        (yield eng.frag3d.out_rgba),
                        (yield eng.frag3d.out_x),
                        (yield eng.frag3d.out_y),
                        (yield eng.frag3d.out_z),
                        (yield eng.frag3d.out_z_pass),
                        (yield eng.frag3d.out_bounds_discard),
                        (yield eng.frag3d.out_shader_discard),
                        (yield eng.frag3d.out_frag_kill),
                        (yield eng.frag3d.out_we_color),
                        (yield eng.frag3d.out_we_z),
                    ))
                yield

        run_simulation(dut, {"ddr": [drive(), monitor()]}, clocks={"ddr": 10})

        self.assertEqual(len(observed), 5)

        # Verify Command 1 outputs bit-exact
        for idx, v in enumerate(vecs_cmd1):
            p00, p10, p01, p11, wu, wv, rgba, bg, x, y, z, zp, bd, _ = v
            tex = ref_bilinear(p00, p10, p01, p11, wu, wv)
            mod = ref_mul_unorm8(tex, rgba)
            sdisc = 1 if mod == colorkey else 0
            alpha_w = pack_rgba(*([unpack_rgba(rgba)[3]] * 4))
            final_rgba = ref_lerp_rgba(mod, bg, alpha_w)
            kill = 1 if (zp == 0 or sdisc == 1 or bd == 1) else 0
            we = 0 if kill else 1
            self.assertEqual(
                observed[idx],
                (final_rgba, x, y, z, zp, bd, sdisc, kill, we, we),
                f"mismatch at cmd1 idx={idx}",
            )

        # Verify Command 2 output (untextured R2 pass-through, no residual discard/state)
        _, _, _, _, _, _, rgba2, _, x2, y2, z2, zp2, bd2, _ = vecs_cmd2[0]
        self.assertEqual(observed[4], (rgba2, x2, y2, z2, zp2, bd2, 0, 0, 1, 1))


if __name__ == "__main__":
    unittest.main()
