"""Stage 4 (6-DSP II=1 Perspective Recovery + Early/Late-Z Unit) for mega138k_ae350 GFX3D.

Implements docs/platforms/mega138k_ae350/gfx_3d.md §4.4 and §4.5:
- PerspRecipUnit: 5-cycle II=1 pipeline using 6 MULT27X18-compatible
  multipliers and a 256x17-bit midpoint reciprocal LUT:
    Cycle 1: CLZ normalize Q_clamped (UQ2.30) -> exponent e in [0, 7], 17-bit M
    Cycle 2: 256x17b LUT lookup -> Y0 (UQ1.16)
    Cycle 3: NR mult 1: Pa = Y0 * M, R = round((2^34 - Pa) >> 15) (UQ2.18)
    Cycle 4: NR mult 2: Pb = R * Y0, Y1 = round(Pb >> 16) (UQ2.18, or 1.0 if Q==1.0)
    Cycle 5: Split 16-bit hi/lo multiply of S_q12_20, T_q12_20 by Y1 + << e -> Q32.16
- ZBufferUnit: 16-bit Z_LB line buffer with Early-Z comparison (all 8 Z_FUNC
  modes) and Late-Z atomic write-enable gating.
"""
from migen import *

Z_NEVER = 0
Z_LESS = 1
Z_EQUAL = 2
Z_LEQUAL = 3
Z_GREATER = 4
Z_NOTEQUAL = 5
Z_GEQUAL = 6
Z_ALWAYS = 7


def _build_recip_lut_init():
    """256x17-bit UQ1.16 midpoint reciprocal LUT: round(2^16 / m_mid_k)."""
    init = []
    for k in range(256):
        m_mid = 0.5 + (k + 0.5) / 512.0
        y0 = int(round((1 << 16) / m_mid))
        init.append(y0 & 0x1FFFF)
    return init


class PerspRecipUnit(Module):
    """5-cycle II=1 perspective 1/Q Newton-Raphson + split hi/lo multiply."""

    LATENCY = 5

    def __init__(self, clock_domain="ddr"):
        self.valid_in = Signal()
        self.stall = Signal()
        self.persp_en = Signal()

        self.s_q12_20 = Signal((32, True))
        self.t_q12_20 = Signal((32, True))
        self.q_clamped = Signal(32)  # UQ2.30 in [0x0040_0000, 0x4000_0000]

        self.valid_out = Signal()
        self.u_q32_16 = Signal((48, True))
        self.v_q32_16 = Signal((48, True))

        lut = Memory(17, 256, init=_build_recip_lut_init())
        self.specials += lut
        lut_port = lut.get_port(clock_domain=clock_domain)
        self.specials += lut_port

        # --- Cycle 1: CLZ normalization ---
        exact_one_c = Signal()
        e_c = Signal(4)
        m_c = Signal(17)
        q_shifted = Signal(32)

        # Priority encoder for MSB in bits [29:22]
        e_expr = 7
        for bit in range(22, 30):
            e_expr = Mux(self.q_clamped[bit], 29 - bit, e_expr)

        self.comb += [
            exact_one_c.eq(self.q_clamped == 0x40000000),
            e_c.eq(Mux(exact_one_c, 0, e_expr)),
            q_shifted.eq(self.q_clamped << e_c),
            m_c.eq(q_shifted[13:30]),
        ]

        # Pipeline registers through Cycles 1..4
        v1 = Signal()
        v2 = Signal()
        v3 = Signal()
        v4 = Signal()
        s1, s2, s3, s4 = [Signal((32, True), reset_less=True) for _ in range(4)]
        t1, t2, t3, t4 = [Signal((32, True), reset_less=True) for _ in range(4)]
        e1, e2, e3, e4 = [Signal(4, reset_less=True) for _ in range(4)]
        eo1, eo2, eo3, eo4 = [Signal(reset_less=True) for _ in range(4)]
        pe1, pe2, pe3, pe4 = [Signal(reset_less=True) for _ in range(4)]
        m1, m2 = [Signal(17, reset_less=True) for _ in range(2)]
        y0_2 = Signal(17)
        y0_3 = Signal(17, reset_less=True)
        r_3 = Signal(20, reset_less=True)
        y1_4 = Signal(20, reset_less=True)

        # Cycle 2 LUT address from m1[8:16]; lut_port.dat_r is valid alongside m2
        self.comb += [
            lut_port.adr.eq(m1[8:16]),
            y0_2.eq(lut_port.dat_r),
        ]

        # Cycle 3 NR mult 1 (DSP #0): Pa = y0_2 * m2
        pa_3 = Signal(34)
        r_full_3 = Signal(35)
        r_rnd_3 = Signal(20)
        self.comb += [
            pa_3.eq(y0_2 * m2),
            r_full_3.eq((2 << 33) - pa_3),
            r_rnd_3.eq((r_full_3 + (1 << 14)) >> 15),
        ]

        # Cycle 4 NR mult 2 (DSP #1): Pb = r_3 * y0_3
        pb_4 = Signal(37)
        y1_rnd_4 = Signal(20)
        self.comb += [
            pb_4.eq(r_3 * y0_3),
            y1_rnd_4.eq((pb_4 + (1 << 15)) >> 16),
        ]

        # Cycle 5 Split 16-bit hi/lo multiply (DSP #2..#5) + << e4 + round >>> 22
        y1_s = Signal((27, True))
        s4_hi = Signal((16, True))
        s4_lo = Signal(16)
        s4_lo_s = Signal((18, True))
        t4_hi = Signal((16, True))
        t4_lo = Signal(16)
        t4_lo_s = Signal((18, True))
        pu_hi = Signal((44, True))
        pu_lo = Signal((44, True))
        pv_hi = Signal((44, True))
        pv_lo = Signal((44, True))
        u_prod = Signal((54, True))
        v_prod = Signal((54, True))
        u_shift = Signal((62, True))
        v_shift = Signal((62, True))
        u_out_c = Signal((48, True))
        v_out_c = Signal((48, True))

        self.comb += [
            y1_s.eq(y1_4),
            s4_hi.eq(s4[16:32]),
            s4_lo.eq(s4[0:16]),
            s4_lo_s.eq(s4_lo),
            t4_hi.eq(t4[16:32]),
            t4_lo.eq(t4[0:16]),
            t4_lo_s.eq(t4_lo),

            pu_hi.eq(y1_s * s4_hi),
            pu_lo.eq(y1_s * s4_lo_s),
            pv_hi.eq(y1_s * t4_hi),
            pv_lo.eq(y1_s * t4_lo_s),

            u_prod.eq((pu_hi << 16) + pu_lo),
            v_prod.eq((pv_hi << 16) + pv_lo),
            u_shift.eq(u_prod << e4),
            v_shift.eq(v_prod << e4),
            u_out_c.eq((u_shift + (1 << 21)) >> 22),
            v_out_c.eq((v_shift + (1 << 21)) >> 22),
        ]

        # Affine bypass when persp_en == 0: s4/t4 are Q12.20 -> round >> 4 to Q32.16
        u_aff_c = Signal((48, True))
        v_aff_c = Signal((48, True))
        self.comb += [
            u_aff_c.eq((s4 + 8) >> 4),
            v_aff_c.eq((t4 + 8) >> 4),
        ]

        self.sync += If(~self.stall,
            # Cycle 1
            v1.eq(self.valid_in),
            s1.eq(self.s_q12_20),
            t1.eq(self.t_q12_20),
            e1.eq(e_c),
            eo1.eq(exact_one_c),
            pe1.eq(self.persp_en),
            m1.eq(m_c),

            # Cycle 2 (lut_port.dat_r updates on this edge alongside m2)
            v2.eq(v1),
            s2.eq(s1),
            t2.eq(t1),
            e2.eq(e1),
            eo2.eq(eo1),
            pe2.eq(pe1),
            m2.eq(m1),

            # Cycle 3
            v3.eq(v2),
            s3.eq(s2),
            t3.eq(t2),
            e3.eq(e2),
            eo3.eq(eo2),
            pe3.eq(pe2),
            y0_3.eq(y0_2),
            r_3.eq(r_rnd_3),

            # Cycle 4
            v4.eq(v3),
            s4.eq(s3),
            t4.eq(t3),
            e4.eq(e3),
            eo4.eq(eo3),
            pe4.eq(pe3),
            y1_4.eq(Mux(eo3, 0x40000, y1_rnd_4)),

            # Cycle 5
            self.valid_out.eq(v4),
            self.u_q32_16.eq(Mux(pe4, u_out_c, u_aff_c)),
            self.v_q32_16.eq(Mux(pe4, v_out_c, v_aff_c)),
        )


def eval_z_func(z_new, z_old, z_func, z_test_en):
    """Combinational Z_FUNC evaluation (§4.2 / §4.4)."""
    res = Signal()
    cmp_res = Signal()
    cases = {
        Z_NEVER: cmp_res.eq(0),
        Z_LESS: cmp_res.eq(z_new < z_old),
        Z_EQUAL: cmp_res.eq(z_new == z_old),
        Z_LEQUAL: cmp_res.eq(z_new <= z_old),
        Z_GREATER: cmp_res.eq(z_new > z_old),
        Z_NOTEQUAL: cmp_res.eq(z_new != z_old),
        Z_GEQUAL: cmp_res.eq(z_new >= z_old),
        Z_ALWAYS: cmp_res.eq(1),
    }
    return res, [Case(z_func, cases), res.eq(Mux(z_test_en, cmp_res, 1))]


class ZBufferUnit(Module):
    """16-bit Z_LB line depth buffer with Early-Z compare and Late-Z writeback."""

    def __init__(self, max_width=2048, clock_domain="ddr"):
        self.z_lb = Memory(16, max_width)
        self.specials += self.z_lb
        self.rd_port = self.z_lb.get_port(clock_domain=clock_domain)
        self.wr_port = self.z_lb.get_port(write_capable=True, clock_domain=clock_domain)
        self.specials += [self.rd_port, self.wr_port]

        # Config
        self.z_test_en = Signal()
        self.z_write_en = Signal()
        self.z_func = Signal(3)
        self.color_write_en = Signal()

        # Early-Z read (1 cycle before Frag3DPort injection)
        self.rd_x = Signal(12)
        self.z_new_in = Signal(16)
        self.z_pass_out = Signal()

        # Late-Z commit (from Frag3DPort output)
        self.out_valid = Signal()
        self.out_x = Signal(12)
        self.out_z_new = Signal(16)
        self.out_we_color_raw = Signal()
        self.out_we_z_raw = Signal()

        self.we_color = Signal()
        self.we_z = Signal()

        # Synchronous read of Z_LB[rd_x]; z_new_q aligns with rd_port.dat_r
        z_new_q = Signal(16)
        self.sync += z_new_q.eq(self.z_new_in)
        self.comb += self.rd_port.adr.eq(self.rd_x)

        z_pass_sig, z_comb_stmts = eval_z_func(
            z_new_q, self.rd_port.dat_r, self.z_func, self.z_test_en
        )
        self.comb += z_comb_stmts
        self.comb += self.z_pass_out.eq(z_pass_sig)

        # Late-Z atomic writeback gating
        self.comb += [
            self.we_color.eq(self.out_valid & self.color_write_en & self.out_we_color_raw),
            self.we_z.eq(self.out_valid & self.z_write_en & self.out_we_z_raw),
            self.wr_port.adr.eq(self.out_x),
            self.wr_port.dat_w.eq(self.out_z_new),
            self.wr_port.we.eq(self.we_z),
        ]
