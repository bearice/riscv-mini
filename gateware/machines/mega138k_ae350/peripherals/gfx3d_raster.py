"""Stage 3 (PS1-class Affine 3D Triangle Rasterizer) for mega138k_ae350 GFX3D.

Implements docs/platforms/mega138k_ae350/gfx_3d.md §4.2 and §4.3:
- 96-byte (24 x 32-bit word) Hardware Triangle Packet decoding & validation
- 35-bit signed area (Q27.8) with degenerate/scissor early-out and CW
  normalization (V1 <-> V2 swap when E_area < 0)
- Exact Top-Left Fill Rule span bounds (ceil_div on Q12.4 edge equations)
  and scissor clamping (guaranteed 0 gaps, 0 double-blends on shared edges)
- CLZ-normalized 1/E_area + shared multi-cycle multiplier for 64-bit Q36.28
  planar gradients (dS, dT, dZ, dR, dG, dB, dA)
- 82-bit multi-cycle row-start pre-stepping MAC (17b x 64b) into 42-bit Q14.28
  (S, T, R, G, B, A) and 46-bit Q18.28 (Z) pixel accumulators
- II=1 scanline pixel streamer feeding TmemSampler -> Gfx2DEngine.frag3d
"""
from migen import *


def _clamp_u8(sig_signed):
    """Clamp signed 42-bit Q14.28 (rounded to integer) to [0, 255]."""
    rounded = (sig_signed + (1 << 27)) >> 28
    return Mux(rounded < 0, 0, Mux(rounded > 255, 255, rounded[0:8]))


class TriRasterizer(Module):
    """Hardware Triangle Setup, Edge/Span Walker, and II=1 Affine DDA."""

    def __init__(self):
        # Control & status
        self.start = Signal()
        self.busy = Signal()
        self.done = Signal()
        self.err_param = Signal()

        # 24-word (96-byte) Hardware Triangle Packet (§4.2)
        self.words = [Signal(32) for _ in range(24)]

        # Output pixel stream (to TmemSampler + Gfx2DEngine.frag3d)
        self.ready = Signal(reset=1)
        self.valid_out = Signal()
        self.x_out = Signal(12)
        self.y_out = Signal(12)
        self.z_out = Signal(32)       # UQ16.16
        self.s_q32_16 = Signal((48, True))
        self.t_q32_16 = Signal((48, True))
        self.rgba_out = Signal(32)    # {A, R, G, B}

        # Latched triangle packet fields (after CW normalization)
        w0 = self.words[0]
        w21 = self.words[21]
        w22 = self.words[22]
        w23 = self.words[23]

        gouraud_en = Signal()
        sc_min_x = Signal(12)
        sc_min_y = Signal(12)
        sc_max_x = Signal(12)
        sc_max_y = Signal(12)

        x = [Signal((16, True)) for _ in range(3)]
        y = [Signal((16, True)) for _ in range(3)]
        z = [Signal(32) for _ in range(3)]
        s = [Signal((32, True)) for _ in range(3)]
        t = [Signal((32, True)) for _ in range(3)]
        rgba = [Signal(32) for _ in range(3)]

        # Setup temporaries
        e_area = Signal((35, True))
        e_area_abs = Signal(34)
        clz_val = Signal(6)
        e_norm = Signal(34)

        # Sequential divider for inv_e_norm = floor(2^82 / e_norm)
        # 50-bit quotient, 35-bit remainder
        div_rem = Signal(36)
        inv_e_norm = Signal(50)
        div_cnt = Signal(6)

        # Planar gradients (14 values: 7 attrs x {dx, dy}) in 64-bit Q36.28
        # Order: 0:dS_dx, 1:dS_dy, 2:dT_dx, 3:dT_dy, 4:dZ_dx, 5:dZ_dy,
        #        6:dR_dx, 7:dR_dy, 8:dG_dx, 9:dG_dy, 10:dB_dx, 11:dB_dy,
        #        12:dA_dx, 13:dA_dy
        grads = [Signal((64, True), reset_less=True) for _ in range(14)]
        grad_idx = Signal(4)

        # Multi-cycle 64x50 -> 114-bit gradient multiplier (4 chunks of 16b x 50b)
        gmul_acc = Signal(114)
        gmul_a = Signal(64)
        gmul_sign = Signal()
        gmul_step = Signal(3)

        # Span Y/X state
        y_cur = Signal((13, True))
        y_end = Signal((13, True))
        x_start = Signal((13, True))
        x_end = Signal((13, True))
        x_cur = Signal(12)

        # Edge walker divider for x_start / x_end (16-bit quotient)
        edge_idx = Signal(2)
        ediv_num = Signal(36)
        ediv_den = Signal(22)
        ediv_rem = Signal(36)
        ediv_q = Signal(16)
        ediv_cnt = Signal(5)
        edge_x_left = Signal((13, True))
        edge_x_right = Signal((13, True))
        has_left = Signal()
        has_right = Signal()

        # 82-bit Row-start MAC (multi-cycle: 7 attributes x 2 axes)
        # 0:S, 1:T, 2:Z, 3:R, 4:G, 5:B, 6:A
        mac_attr = Signal(3)
        mac_phase = Signal(3)
        mac_acc = Signal((82, True))
        dx_q13_4 = Signal((17, True))
        dy_q13_4 = Signal((17, True))

        # Pixel DDA accumulators
        s_acc = Signal((42, True), reset_less=True)
        t_acc = Signal((42, True), reset_less=True)
        z_acc = Signal((46, True), reset_less=True)
        r_acc = Signal((42, True), reset_less=True)
        g_acc = Signal((42, True), reset_less=True)
        b_acc = Signal((42, True), reset_less=True)
        a_acc = Signal((42, True), reset_less=True)

        # Combinational edge vectors
        dx10 = Signal((17, True))
        dy10 = Signal((17, True))
        dx20 = Signal((17, True))
        dy20 = Signal((17, True))
        p1 = Signal((34, True))
        p2 = Signal((34, True))
        area_comb = Signal((35, True))
        self.comb += [
            dx10.eq(x[1] - x[0]),
            dy10.eq(y[1] - y[0]),
            dx20.eq(x[2] - x[0]),
            dy20.eq(y[2] - y[0]),
            p1.eq(dx10 * dy20),
            p2.eq(dy10 * dx20),
            area_comb.eq(p1 - p2),
        ]

        # Combinational Y bounding box with scissor clamp
        y_min_q = Signal((16, True))
        y_max_q = Signal((16, True))
        y_min_s17 = Signal((17, True))
        y_max_s17 = Signal((17, True))
        y_start_c = Signal((13, True))
        y_end_c = Signal((13, True))
        self.comb += [
            y_min_q.eq(Mux(y[0] < y[1], Mux(y[0] < y[2], y[0], y[2]),
                           Mux(y[1] < y[2], y[1], y[2]))),
            y_max_q.eq(Mux(y[0] > y[1], Mux(y[0] > y[2], y[0], y[2]),
                           Mux(y[1] > y[2], y[1], y[2]))),
            y_min_s17.eq(y_min_q),
            y_max_s17.eq(y_max_q),
        ]
        ceil_ymin = (y_min_s17 + 7) >> 4
        ceil_ymax = (y_max_s17 + 7) >> 4
        sc_miny_s = Signal((13, True))
        sc_maxy_s = Signal((13, True))
        sc_minx_s = Signal((13, True))
        sc_maxx_s = Signal((13, True))
        self.comb += [
            sc_miny_s.eq(sc_min_y),
            sc_maxy_s.eq(sc_max_y),
            sc_minx_s.eq(sc_min_x),
            sc_maxx_s.eq(sc_max_x),
            y_start_c.eq(Mux(ceil_ymin > sc_miny_s, ceil_ymin, sc_miny_s)),
            y_end_c.eq(Mux((ceil_ymax - 1) < sc_maxy_s, ceil_ymax - 1, sc_maxy_s)),
        ]

        # Combinational CLZ for 34-bit e_area_abs
        clz_comb = Signal(6)
        clz_expr = 34
        for bit in range(34):
            clz_expr = Mux(e_area_abs[bit], 33 - bit, clz_expr)
        self.comb += clz_comb.eq(clz_expr)

        # Gradient numerator selector (for grad_idx 0..13)
        # attr_val in Q12.20 (or Z in UQ16.16)
        attr_0 = Signal((33, True))
        attr_1 = Signal((33, True))
        attr_2 = Signal((33, True))
        grad_is_dy = Signal()
        grad_is_z = Signal()
        self.comb += grad_is_dy.eq(grad_idx[0])
        self.comb += grad_is_z.eq(grad_idx[1:4] == 2)

        def _ch_q12_20(v, shift_bits):
            return Cat(Constant(0, 20), v[shift_bits:shift_bits + 8], Constant(0, 5))

        attr_sel = grad_idx[1:4]
        self.comb += Case(attr_sel, {
            0: [attr_0.eq(s[0]), attr_1.eq(s[1]), attr_2.eq(s[2])],
            1: [attr_0.eq(t[0]), attr_1.eq(t[1]), attr_2.eq(t[2])],
            2: [attr_0.eq(Cat(z[0], 0)), attr_1.eq(Cat(z[1], 0)), attr_2.eq(Cat(z[2], 0))],
            3: [attr_0.eq(_ch_q12_20(rgba[0], 16)), attr_1.eq(_ch_q12_20(rgba[1], 16)), attr_2.eq(_ch_q12_20(rgba[2], 16))],
            4: [attr_0.eq(_ch_q12_20(rgba[0], 8)), attr_1.eq(_ch_q12_20(rgba[1], 8)), attr_2.eq(_ch_q12_20(rgba[2], 8))],
            5: [attr_0.eq(_ch_q12_20(rgba[0], 0)), attr_1.eq(_ch_q12_20(rgba[1], 0)), attr_2.eq(_ch_q12_20(rgba[2], 0))],
            6: [attr_0.eq(_ch_q12_20(rgba[0], 24)), attr_1.eq(_ch_q12_20(rgba[1], 24)), attr_2.eq(_ch_q12_20(rgba[2], 24))],
            "default": [attr_0.eq(0), attr_1.eq(0), attr_2.eq(0)],
        })

        da10 = Signal((34, True))
        da20 = Signal((34, True))
        num_dx = Signal((52, True))
        num_dy = Signal((52, True))
        num_raw = Signal((64, True))
        self.comb += [
            da10.eq(attr_1 - attr_0),
            da20.eq(attr_2 - attr_0),
            num_dx.eq(da10 * dy20 - da20 * dy10),
            num_dy.eq(da20 * dx10 - da10 * dx20),
            # Shift to align to Q36.28 before dividing by e_area (Q27.8):
            # S/T/RGBA (Q12.20 * Q13.4 -> 2^24 / 2^8 = 2^16) => << 12
            # Z (UQ16.16 * Q13.4 -> 2^20 / 2^8 = 2^12) => << 16
            num_raw.eq(
                Mux(grad_is_z,
                    Mux(grad_is_dy, num_dy, num_dx) << 16,
                    Mux(grad_is_dy, num_dy, num_dx) << 12)
            ),
        ]

        # Output pixel assembly
        s_q14_16 = (s_acc + (1 << 11)) >> 12
        t_q14_16 = (t_acc + (1 << 11)) >> 12
        z_rnd = (z_acc + (1 << 11)) >> 12
        z_clamped = Mux(z_rnd < 0, 0, Mux(z_rnd > 0xFFFFFFFF, 0xFFFFFFFF, z_rnd[0:32]))
        r_u8 = _clamp_u8(r_acc)
        g_u8 = _clamp_u8(g_acc)
        b_u8 = _clamp_u8(b_acc)
        a_u8 = _clamp_u8(a_acc)

        self.comb += [
            self.x_out.eq(x_cur),
            self.y_out.eq(y_cur[0:12]),
            self.z_out.eq(z_clamped),
            self.s_q32_16.eq(s_q14_16),
            self.t_q32_16.eq(t_q14_16),
            self.rgba_out.eq(Mux(gouraud_en, Cat(b_u8, g_u8, r_u8, a_u8), rgba[0])),
        ]

        # Edge endpoints for edge_idx 0..2: (V0->V1), (V1->V2), (V2->V0)
        ex0 = Signal((16, True))
        ey0 = Signal((16, True))
        ex1 = Signal((16, True))
        ey1 = Signal((16, True))
        self.comb += Case(edge_idx, {
            0: [ex0.eq(x[0]), ey0.eq(y[0]), ex1.eq(x[1]), ey1.eq(y[1])],
            1: [ex0.eq(x[1]), ey0.eq(y[1]), ex1.eq(x[2]), ey1.eq(y[2])],
            2: [ex0.eq(x[2]), ey0.eq(y[2]), ex1.eq(x[0]), ey1.eq(y[0])],
            "default": [ex0.eq(0), ey0.eq(0), ex1.eq(0), ey1.eq(0)],
        })
        edx = Signal((17, True))
        edy = Signal((17, True))
        y_cur_s17 = Signal((17, True))
        y_sample = Signal((17, True))
        ex0_s18 = Signal((18, True))
        edge_active = Signal()
        edge_is_left = Signal()
        edge_D = Signal(17)
        edge_D_s = Signal((18, True))
        edge_lhs = Signal((36, True))
        self.comb += [
            edx.eq(ex1 - ex0),
            edy.eq(ey1 - ey0),
            y_cur_s17.eq(y_cur),
            y_sample.eq((y_cur_s17 << 4) + 8),
            ex0_s18.eq(ex0),
            edge_is_left.eq(edy < 0),
            edge_D.eq(Mux(edge_is_left, -edy, edy)),
            edge_D_s.eq(edge_D),
            # Edge spans y_sample iff y_sample is in [min(ey0,ey1), max(ey0,ey1)]
            # Note: horizontal edges (edy==0) are skipped; top/bottom vertices
            # are already bounded by [y_start, y_end].
            edge_active.eq(
                (edy != 0) &
                Mux(edge_is_left,
                    (y_sample >= ey1) & (y_sample <= ey0),
                    (y_sample >= ey0) & (y_sample <= ey1))
            ),
            # Left:  ceil_div(D*(ex0 - 8) - edx*(y_sample - ey0), 16*D)
            # Right: ceil_div(D*(ex0 - 8) + edx*(y_sample - ey0), 16*D) - 1
            edge_lhs.eq(
                Mux(edge_is_left,
                    edge_D_s * (ex0_s18 - 8) - edx * (y_sample - ey0),
                    edge_D_s * (ex0_s18 - 8) + edx * (y_sample - ey0))
            ),
        ]

        # MAC attribute selector
        mac_base = Signal((44, True))
        mac_gx = Signal((64, True))
        mac_gy = Signal((64, True))
        def _ch_q12_20_s(v, shift_bits):
            return Cat(Constant(0, 20), v[shift_bits:shift_bits + 8], Constant(0, 16))
        self.comb += Case(mac_attr, {
            0: [mac_base.eq(s[0] << 12), mac_gx.eq(grads[0]), mac_gy.eq(grads[1])],
            1: [mac_base.eq(t[0] << 12), mac_gx.eq(grads[2]), mac_gy.eq(grads[3])],
            2: [mac_base.eq(Cat(Constant(0, 12), z[0], Constant(0, 2))), mac_gx.eq(grads[4]), mac_gy.eq(grads[5])],
            3: [mac_base.eq(_ch_q12_20_s(rgba[0], 16)), mac_gx.eq(grads[6]), mac_gy.eq(grads[7])],
            4: [mac_base.eq(_ch_q12_20_s(rgba[0], 8)), mac_gx.eq(grads[8]), mac_gy.eq(grads[9])],
            5: [mac_base.eq(_ch_q12_20_s(rgba[0], 0)), mac_gx.eq(grads[10]), mac_gy.eq(grads[11])],
            6: [mac_base.eq(_ch_q12_20_s(rgba[0], 24)), mac_gx.eq(grads[12]), mac_gy.eq(grads[13])],
            "default": [mac_base.eq(0), mac_gx.eq(0), mac_gy.eq(0)],
        })

        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm

        self.comb += [
            self.busy.eq(~fsm.ongoing("IDLE")),
            self.valid_out.eq(fsm.ongoing("SPAN_WALK")),
        ]

        fsm.act("IDLE",
            If(self.start,
                NextValue(self.done, 0),
                NextValue(self.err_param, 0),
                If(w23 != 0,
                    NextValue(self.err_param, 1),
                    NextValue(self.done, 1),
                ).Else(
                    NextValue(gouraud_en, w0[11]),
                    NextValue(sc_min_x, w21[0:12]),
                    NextValue(sc_min_y, w21[16:28]),
                    NextValue(sc_max_x, w22[0:12]),
                    NextValue(sc_max_y, w22[16:28]),
                    NextValue(x[0], self.words[3][0:16]),
                    NextValue(y[0], self.words[3][16:32]),
                    NextValue(z[0], self.words[4]),
                    NextValue(s[0], self.words[5]),
                    NextValue(t[0], self.words[6]),
                    NextValue(rgba[0], self.words[8]),
                    NextValue(x[1], self.words[9][0:16]),
                    NextValue(y[1], self.words[9][16:32]),
                    NextValue(z[1], self.words[10]),
                    NextValue(s[1], self.words[11]),
                    NextValue(t[1], self.words[12]),
                    NextValue(rgba[1], self.words[14]),
                    NextValue(x[2], self.words[15][0:16]),
                    NextValue(y[2], self.words[15][16:32]),
                    NextValue(z[2], self.words[16]),
                    NextValue(s[2], self.words[17]),
                    NextValue(t[2], self.words[18]),
                    NextValue(rgba[2], self.words[20]),
                    NextState("SETUP_AREA"),
                ),
            ).Else(
                NextValue(self.done, 0),
            )
        )

        fsm.act("SETUP_AREA",
            If((area_comb == 0) | (sc_min_x > sc_max_x) | (sc_min_y > sc_max_y) | (y_start_c > y_end_c),
                NextValue(self.done, 1),
                NextState("IDLE"),
            ).Else(
                If(area_comb[34],
                    # Swap V1 <-> V2 so E_area > 0
                    NextValue(x[1], x[2]), NextValue(x[2], x[1]),
                    NextValue(y[1], y[2]), NextValue(y[2], y[1]),
                    NextValue(z[1], z[2]), NextValue(z[2], z[1]),
                    NextValue(s[1], s[2]), NextValue(s[2], s[1]),
                    NextValue(t[1], t[2]), NextValue(t[2], t[1]),
                    NextValue(rgba[1], rgba[2]), NextValue(rgba[2], rgba[1]),
                    NextValue(e_area, -area_comb),
                    NextValue(e_area_abs, (-area_comb)[0:34]),
                ).Else(
                    NextValue(e_area, area_comb),
                    NextValue(e_area_abs, area_comb[0:34]),
                ),
                NextValue(y_cur, y_start_c),
                NextValue(y_end, y_end_c),
                NextState("SETUP_CLZ"),
            )
        )

        fsm.act("SETUP_CLZ",
            NextValue(clz_val, clz_comb),
            NextValue(e_norm, e_area_abs << clz_comb),
            # Compute floor(2^82 / e_norm): start with MSB=1 at bit 49 (since 2^33 <= e_norm < 2^34)
            # First trial: rem starts at 1 << 33? Actually 50-bit quotient:
            # At step k=49, dividend bit is 2^82 -> aligned to 2^33*2^49, so initial rem=1
            NextValue(div_rem, 1),
            NextValue(inv_e_norm, 0),
            NextValue(div_cnt, 49),
            NextState("SETUP_DIV"),
        )

        # Restoring divider step: shift div_rem left by 1, compare with e_norm
        rem_next = Signal(36)
        self.comb += rem_next.eq(div_rem << 1)
        fsm.act("SETUP_DIV",
            If(rem_next >= e_norm,
                NextValue(div_rem, rem_next - e_norm),
                NextValue(inv_e_norm, (inv_e_norm << 1) | 1),
            ).Else(
                NextValue(div_rem, rem_next),
                NextValue(inv_e_norm, inv_e_norm << 1),
            ),
            If(div_cnt == 0,
                NextValue(grad_idx, 0),
                NextState("SETUP_GRAD_LOAD"),
            ).Else(
                NextValue(div_cnt, div_cnt - 1),
            )
        )

        # Multi-cycle gradient compute: 4 chunks of 16b x 50b
        fsm.act("SETUP_GRAD_LOAD",
            NextValue(gmul_sign, num_raw[63]),
            NextValue(gmul_a, Mux(num_raw[63], -num_raw, num_raw)),
            NextValue(gmul_acc, 0),
            NextValue(gmul_step, 0),
            NextState("SETUP_GRAD_MUL"),
        )

        chunk16 = Signal(16)
        self.comb += Case(gmul_step, {
            0: chunk16.eq(gmul_a[0:16]),
            1: chunk16.eq(gmul_a[16:32]),
            2: chunk16.eq(gmul_a[32:48]),
            3: chunk16.eq(gmul_a[48:64]),
            "default": chunk16.eq(0),
        })
        partial = Signal(66)
        self.comb += partial.eq(chunk16 * inv_e_norm)
        fsm.act("SETUP_GRAD_MUL",
            NextValue(gmul_acc, gmul_acc + (partial << (gmul_step * 16))),
            If(gmul_step == 3,
                NextState("SETUP_GRAD_STORE"),
            ).Else(
                NextValue(gmul_step, gmul_step + 1),
            )
        )

        shift_amt = 82 - clz_val
        grad_mag = (gmul_acc + (1 << (shift_amt - 1))) >> shift_amt
        grad_signed = Mux(gmul_sign, -grad_mag[0:64], grad_mag[0:64])
        fsm.act("SETUP_GRAD_STORE",
            [If(grad_idx == k, NextValue(grads[k], grad_signed)) for k in range(14)],
            If(grad_idx == (13 if True else 5),
                NextValue(edge_idx, 0),
                NextValue(has_left, 0),
                NextValue(has_right, 0),
                NextState("SPAN_EDGE_PREP"),
            ).Else(
                # Skip RGBA gradients when gouraud_en == 0
                If((grad_idx == 5) & ~gouraud_en,
                    NextValue(edge_idx, 0),
                    NextValue(has_left, 0),
                    NextValue(has_right, 0),
                    NextState("SPAN_EDGE_PREP"),
                ).Else(
                    NextValue(grad_idx, grad_idx + 1),
                    NextState("SETUP_GRAD_LOAD"),
                )
            )
        )

        # Per-scanline edge intersection (3 edges)
        # Compute ceil_div(edge_lhs, 16 * edge_D) cleanly for positive/negative edge_lhs
        ediv_den_comb = Signal(21)
        self.comb += ediv_den_comb.eq(edge_D << 4)
        fsm.act("SPAN_EDGE_PREP",
            If(edge_active,
                # If edge_lhs <= 0, quotient is <= 0 (clamped to >= sc_min_x >= 0 anyway,
                # so any negative x_edge clamps to 0; for ceil_div(lhs, den) when lhs > 0:
                # (lhs + den - 1) // den).
                If(edge_lhs <= 0,
                    If(edge_is_left,
                        NextValue(edge_x_left, 0),
                        NextValue(has_left, 1),
                    ).Else(
                        NextValue(edge_x_right, -1),
                        NextValue(has_right, 1),
                    ),
                    If(edge_idx == 2,
                        NextState("SPAN_CHECK"),
                    ).Else(
                        NextValue(edge_idx, edge_idx + 1),
                    )
                ).Else(
                    NextValue(ediv_num, (edge_lhs + ediv_den_comb - 1)[0:36]),
                    NextValue(ediv_den, ediv_den_comb),
                    NextValue(ediv_rem, 0),
                    NextValue(ediv_q, 0),
                    NextValue(ediv_cnt, 15),
                    NextState("SPAN_EDGE_DIV"),
                )
            ).Else(
                If(edge_idx == 2,
                    NextState("SPAN_CHECK"),
                ).Else(
                    NextValue(edge_idx, edge_idx + 1),
                )
            )
        )

        # 16-bit unsigned restoring division: ediv_num[35:0] // ediv_den[21:0]
        # Note: screen X <= 2048, so quotient <= 4096 (13 bits); start at bit 15
        erem_shift = Signal(37)
        self.comb += erem_shift.eq(Cat(ediv_num[15], ediv_rem[0:35]))
        # Use standard 36-bit shift-left-in from ediv_num
        fsm.act("SPAN_EDGE_DIV",
            # Initialize ediv_rem from upper bits on first step? Simpler: 36-step or
            # check if ediv_num >> 16 fits. Let's do 16 steps starting from ediv_num[16:36]
            If(ediv_cnt == 15,
                # Load upper bits and run first compare
                If(Cat(ediv_num[15], ediv_num[16:36]) >= ediv_den,
                    NextValue(ediv_rem, Cat(ediv_num[15], ediv_num[16:36]) - ediv_den),
                    NextValue(ediv_q, 1),
                ).Else(
                    NextValue(ediv_rem, Cat(ediv_num[15], ediv_num[16:36])),
                    NextValue(ediv_q, 0),
                ),
                NextValue(ediv_num, ediv_num << 1),
                NextValue(ediv_cnt, 14),
            ).Else(
                If(Cat(ediv_num[15], ediv_rem[0:35]) >= ediv_den,
                    NextValue(ediv_rem, Cat(ediv_num[15], ediv_rem[0:35]) - ediv_den),
                    NextValue(ediv_q, (ediv_q << 1) | 1),
                ).Else(
                    NextValue(ediv_rem, Cat(ediv_num[15], ediv_rem[0:35])),
                    NextValue(ediv_q, ediv_q << 1),
                ),
                NextValue(ediv_num, ediv_num << 1),
                If(ediv_cnt == 0,
                    NextState("SPAN_EDGE_STORE"),
                ).Else(
                    NextValue(ediv_cnt, ediv_cnt - 1),
                )
            )
        )

        fsm.act("SPAN_EDGE_STORE",
            If(edge_is_left,
                NextValue(edge_x_left, ediv_q[0:12]),
                NextValue(has_left, 1),
            ).Else(
                NextValue(edge_x_right, ediv_q[0:12] - 1),
                NextValue(has_right, 1),
            ),
            If(edge_idx == 2,
                NextState("SPAN_CHECK"),
            ).Else(
                NextValue(edge_idx, edge_idx + 1),
                NextState("SPAN_EDGE_PREP"),
            )
        )

        xs_clamp = Mux(edge_x_left > sc_minx_s, edge_x_left, sc_minx_s)
        xe_clamp = Mux(edge_x_right < sc_maxx_s, edge_x_right, sc_maxx_s)
        xs_s17 = Signal((17, True))
        self.comb += xs_s17.eq(xs_clamp)
        fsm.act("SPAN_CHECK",
            If(has_left & has_right & (xs_clamp <= xe_clamp),
                NextValue(x_start, xs_clamp),
                NextValue(x_end, xe_clamp),
                NextValue(x_cur, xs_clamp[0:12]),
                NextValue(dx_q13_4, (xs_s17 << 4) + 8 - x[0]),
                NextValue(dy_q13_4, (y_cur_s17 << 4) + 8 - y[0]),
                NextValue(mac_attr, 0),
                NextValue(mac_phase, 0),
                NextState("SPAN_MAC"),
            ).Else(
                If(y_cur >= y_end,
                    NextValue(self.done, 1),
                    NextState("IDLE"),
                ).Else(
                    NextValue(y_cur, y_cur + 1),
                    NextValue(edge_idx, 0),
                    NextValue(has_left, 0),
                    NextValue(has_right, 0),
                    NextState("SPAN_EDGE_PREP"),
                )
            )
        )

        # 82-bit Row-start MAC: 2 phases per attribute (phase 0: base + dx*gx, phase 1: + dy*gy + 8 >>> 4)
        mac_sum_x = Signal((82, True))
        mac_sum_y = Signal((82, True))
        mac_res = Signal((82, True))
        self.comb += [
            mac_sum_x.eq(mac_base + dx_q13_4 * mac_gx),
            mac_sum_y.eq(mac_acc + dy_q13_4 * mac_gy + 8),
            mac_res.eq(mac_sum_y >> 4),
        ]
        fsm.act("SPAN_MAC",
            If(mac_phase == 0,
                NextValue(mac_acc, mac_sum_x),
                NextValue(mac_phase, 1),
            ).Else(
                Case(mac_attr, {
                    0: NextValue(s_acc, mac_res[0:42]),
                    1: NextValue(t_acc, mac_res[0:42]),
                    2: NextValue(z_acc, mac_res[0:46]),
                    3: NextValue(r_acc, mac_res[0:42]),
                    4: NextValue(g_acc, mac_res[0:42]),
                    5: NextValue(b_acc, mac_res[0:42]),
                    6: NextValue(a_acc, mac_res[0:42]),
                }),
                NextValue(mac_phase, 0),
                If((mac_attr == 6) | ((mac_attr == 2) & ~gouraud_en),
                    NextState("SPAN_WALK"),
                ).Else(
                    NextValue(mac_attr, mac_attr + 1),
                )
            )
        )

        # II=1 Pixel Streamer
        fsm.act("SPAN_WALK",
            If(self.ready,
                If(x_cur >= x_end[0:12],
                    If(y_cur >= y_end,
                        NextValue(self.done, 1),
                        NextState("IDLE"),
                    ).Else(
                        NextValue(y_cur, y_cur + 1),
                        NextValue(edge_idx, 0),
                        NextValue(has_left, 0),
                        NextValue(has_right, 0),
                        NextState("SPAN_EDGE_PREP"),
                    )
                ).Else(
                    NextValue(x_cur, x_cur + 1),
                    NextValue(s_acc, s_acc + grads[0][0:42]),
                    NextValue(t_acc, t_acc + grads[2][0:42]),
                    NextValue(z_acc, z_acc + grads[4][0:46]),
                    NextValue(r_acc, r_acc + grads[6][0:42]),
                    NextValue(g_acc, g_acc + grads[8][0:42]),
                    NextValue(b_acc, b_acc + grads[10][0:42]),
                    NextValue(a_acc, a_acc + grads[12][0:42]),
                )
            )
        )
