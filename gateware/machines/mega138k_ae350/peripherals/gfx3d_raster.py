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
        self.s_q12_20 = Signal((32, True))
        self.t_q12_20 = Signal((32, True))
        self.q_clamped = Signal(32)   # UQ2.30 in [0x0040_0000, 0x4000_0000]
        self.persp_en_out = Signal()
        self.rgba_out = Signal(32)    # {A, R, G, B}

        # Optional per-scanline row-sync handshake (e.g. DDR3 Z-buffer load/store)
        self.row_sync_req = Signal()
        self.row_sync_ack = Signal(reset=1)
        self.row_y = Signal(12)
        self.row_x0 = Signal(12)
        self.row_x1 = Signal(12)

        # Triangle packet fields (held static in words[0..23] while busy; swap_v12 swaps V1 <-> V2)
        w0 = self.words[0]
        w21 = self.words[21]
        w22 = self.words[22]
        w23 = self.words[23]

        persp_en = w0[10]
        gouraud_en = w0[11]
        sc_min_x = w21[0:12]
        sc_min_y = w21[16:28]
        sc_max_x = w22[0:12]
        sc_max_y = w22[16:28]

        swap_v12 = Signal()
        swap_v12.attr.add(("syn_maxfan", 16))
        x = [Signal((16, True)) for _ in range(3)]
        y = [Signal((16, True)) for _ in range(3)]
        z = [Signal(32) for _ in range(3)]
        s = [Signal((32, True)) for _ in range(3)]
        t = [Signal((32, True)) for _ in range(3)]
        q = [Signal(32) for _ in range(3)]
        rgba = [Signal(32) for _ in range(3)]
        self.comb += [
            x[0].eq(self.words[3][0:16]),
            y[0].eq(self.words[3][16:32]),
            z[0].eq(self.words[4]),
            s[0].eq(self.words[5]),
            t[0].eq(self.words[6]),
            q[0].eq(self.words[7]),
            rgba[0].eq(self.words[8]),
            x[1].eq(Mux(swap_v12, self.words[15][0:16], self.words[9][0:16])),
            y[1].eq(Mux(swap_v12, self.words[15][16:32], self.words[9][16:32])),
            z[1].eq(Mux(swap_v12, self.words[16], self.words[10])),
            s[1].eq(Mux(swap_v12, self.words[17], self.words[11])),
            t[1].eq(Mux(swap_v12, self.words[18], self.words[12])),
            q[1].eq(Mux(swap_v12, self.words[19], self.words[13])),
            rgba[1].eq(Mux(swap_v12, self.words[20], self.words[14])),
            x[2].eq(Mux(swap_v12, self.words[9][0:16], self.words[15][0:16])),
            y[2].eq(Mux(swap_v12, self.words[9][16:32], self.words[15][16:32])),
            z[2].eq(Mux(swap_v12, self.words[10], self.words[16])),
            s[2].eq(Mux(swap_v12, self.words[11], self.words[17])),
            t[2].eq(Mux(swap_v12, self.words[12], self.words[18])),
            q[2].eq(Mux(swap_v12, self.words[13], self.words[19])),
            rgba[2].eq(Mux(swap_v12, self.words[14], self.words[20])),
        ]

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

        # Planar gradients: dX and dY in 8x50b BSRAMs
        # Order: 0:S, 1:T, 2:Z, 3:R, 4:G, 5:B, 6:A, 7:Q
        dx_mem = Memory(50, 8, name="g3d_dx_mem")
        dx_wr = dx_mem.get_port(write_capable=True)
        dx_rd = dx_mem.get_port()
        dy_mem = Memory(50, 8, name="g3d_dy_mem")
        dy_wr = dy_mem.get_port(write_capable=True)
        dy_rd = dy_mem.get_port()
        self.specials += [dx_mem, dx_wr, dx_rd, dy_mem, dy_wr, dy_rd]
        grad_idx = Signal(4)
        walk_idx = Signal(4)

        # Multi-cycle 64x50 -> 114-bit gradient multiplier (4 chunks of 16b x 50b)
        gmul_acc = Signal(147)
        gmul_a = Signal(64)
        gmul_sign = Signal()
        gmul_step = Signal(3)
        norm_cnt = Signal(6)
        norm_done = Signal()

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
        r_acc = Signal((38, True), reset_less=True)
        g_acc = Signal((38, True), reset_less=True)
        b_acc = Signal((38, True), reset_less=True)
        a_acc = Signal((38, True), reset_less=True)
        q_acc = Signal((40, True), reset_less=True)

        # Combinational edge vectors
        dx10 = Signal((17, True))
        dy10 = Signal((17, True))
        dx20 = Signal((17, True))
        dy20 = Signal((17, True))
        p1 = Signal((34, True), reset_less=True)
        p2 = Signal((34, True), reset_less=True)
        area_comb = Signal((35, True))
        self.comb += [
            dx10.eq(x[1] - x[0]),
            dy10.eq(y[1] - y[0]),
            dx20.eq(x[2] - x[0]),
            dy20.eq(y[2] - y[0]),
            area_comb.eq(p1 - p2),
        ]

        # Combinational Y bounding box with scissor clamp (registered in SETUP_AREA0)
        y_min_q = Signal((16, True))
        y_max_q = Signal((16, True))
        y_min_r = Signal((16, True), reset_less=True)
        y_max_r = Signal((16, True), reset_less=True)
        y_min_s17 = Signal((17, True))
        y_max_s17 = Signal((17, True))
        y_start_c = Signal((13, True))
        y_end_c = Signal((13, True))
        y_start_r = Signal((13, True), reset_less=True)
        y_end_r = Signal((13, True), reset_less=True)
        area_zero = Signal(reset_less=True)
        y1_raw = Signal((16, True))
        y2_raw = Signal((16, True))
        self.comb += [
            y1_raw.eq(self.words[9][16:32]),
            y2_raw.eq(self.words[15][16:32]),
            y_min_q.eq(Mux(y[0] < y1_raw, Mux(y[0] < y2_raw, y[0], y2_raw),
                           Mux(y1_raw < y2_raw, y1_raw, y2_raw))),
            y_max_q.eq(Mux(y[0] > y1_raw, Mux(y[0] > y2_raw, y[0], y2_raw),
                           Mux(y1_raw > y2_raw, y1_raw, y2_raw))),
            y_min_s17.eq(y_min_r),
            y_max_s17.eq(y_max_r),
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
            7: [attr_0.eq(Cat(q[0], 0)), attr_1.eq(Cat(q[1], 0)), attr_2.eq(Cat(q[2], 0))],
            "default": [attr_0.eq(0), attr_1.eq(0), attr_2.eq(0)],
        })

        da10 = Signal((34, True))
        da20 = Signal((34, True))
        mul_d1 = Signal((17, True))
        mul_d2 = Signal((17, True))
        da10_r = Signal((34, True), reset_less=True)
        da20_r = Signal((34, True), reset_less=True)
        mul_d1_r = Signal((17, True), reset_less=True)
        mul_d2_r = Signal((17, True), reset_less=True)
        gp_p1 = Signal((51, True), reset_less=True)
        gp_p2 = Signal((51, True), reset_less=True)
        num_sel = Signal((52, True))
        num_raw = Signal((64, True))
        self.comb += [
            da10.eq(attr_1 - attr_0),
            da20.eq(attr_2 - attr_0),
            mul_d1.eq(Mux(grad_is_dy, -dx20, dy20)),
            mul_d2.eq(Mux(grad_is_dy, -dx10, dy10)),
            num_sel.eq(gp_p1 - gp_p2),
            # Shift to align to Q36.28 before dividing by e_area (Q27.8):
            # S/T/RGBA (Q12.20 * Q13.4 -> 2^24 / 2^8 = 2^16) => << 12
            # Z (UQ16.16 * Q13.4 -> 2^20 / 2^8 = 2^12) => << 16
            num_raw.eq(Mux(grad_is_z, num_sel << 16, num_sel << 12)),
        ]

        # Output pixel assembly (2-stage: SPAN_WALK_RND adds, SPAN_WALK_PRE clamps)
        s_q14_16_r = Signal((36, True), reset_less=True)
        t_q14_16_r = Signal((36, True), reset_less=True)
        s_q14_20_r = Signal((34, True), reset_less=True)
        t_q14_20_r = Signal((34, True), reset_less=True)
        q_rnd_r = Signal((32, True), reset_less=True)
        z_rnd_r = Signal((34, True), reset_less=True)
        s_sat_20 = Mux(s_q14_20_r < -(1 << 31), -(1 << 31),
                       Mux(s_q14_20_r > ((1 << 31) - 1), (1 << 31) - 1, s_q14_20_r[0:32]))
        t_sat_20 = Mux(t_q14_20_r < -(1 << 31), -(1 << 31),
                       Mux(t_q14_20_r > ((1 << 31) - 1), (1 << 31) - 1, t_q14_20_r[0:32]))
        q_clamped_v = Mux(q_rnd_r < 0x00400000, 0x00400000,
                          Mux(q_rnd_r > 0x40000000, 0x40000000, q_rnd_r[0:32]))
        z_clamped = Mux(z_rnd_r < 0, 0, Mux(z_rnd_r > 0xFFFFFFFF, 0xFFFFFFFF, z_rnd_r[0:32]))
        r_u8 = _clamp_u8(r_acc)
        g_u8 = _clamp_u8(g_acc)
        b_u8 = _clamp_u8(b_acc)
        a_u8 = _clamp_u8(a_acc)

        z_out_r = Signal(32, reset_less=True)
        s_q32_16_r = Signal((48, True), reset_less=True)
        t_q32_16_r = Signal((48, True), reset_less=True)
        s_q12_20_r = Signal((32, True), reset_less=True)
        t_q12_20_r = Signal((32, True), reset_less=True)
        q_clamped_r = Signal(32, reset_less=True)
        rgba_out_r = Signal(32, reset_less=True)

        self.comb += [
            self.x_out.eq(x_cur),
            self.y_out.eq(y_cur[0:12]),
            self.z_out.eq(z_out_r),
            self.s_q32_16.eq(s_q32_16_r),
            self.t_q32_16.eq(t_q32_16_r),
            self.s_q12_20.eq(s_q12_20_r),
            self.t_q12_20.eq(t_q12_20_r),
            self.q_clamped.eq(q_clamped_r),
            self.persp_en_out.eq(persp_en),
            self.rgba_out.eq(rgba_out_r),
        ]

        # Edge endpoints for edge_idx 0..2: (V0->V1), (V1->V2), (V2->V0)
        ex0_c = Signal((16, True))
        ey0_c = Signal((16, True))
        ex1_c = Signal((16, True))
        ey1_c = Signal((16, True))
        ex0 = Signal((16, True), reset_less=True)
        ey0 = Signal((16, True), reset_less=True)
        ex1 = Signal((16, True), reset_less=True)
        ey1 = Signal((16, True), reset_less=True)
        self.comb += Case(edge_idx, {
            0: [ex0_c.eq(x[0]), ey0_c.eq(y[0]), ex1_c.eq(x[1]), ey1_c.eq(y[1])],
            1: [ex0_c.eq(x[1]), ey0_c.eq(y[1]), ex1_c.eq(x[2]), ey1_c.eq(y[2])],
            2: [ex0_c.eq(x[2]), ey0_c.eq(y[2]), ex1_c.eq(x[0]), ey1_c.eq(y[0])],
            "default": [ex0_c.eq(0), ey0_c.eq(0), ex1_c.eq(0), ey1_c.eq(0)],
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
        mac_base = Signal((49, True))
        mac_gx = Signal((50, True))
        mac_gy = Signal((50, True))
        def _ch_q12_20_s(v, shift_bits):
            return Cat(Constant(0, 32), v[shift_bits:shift_bits + 8], Constant(0, 9))
        self.comb += [
            dy_rd.adr.eq(mac_attr),
            mac_gx.eq(dx_rd.dat_r),
            mac_gy.eq(dy_rd.dat_r),
        ]
        self.comb += Case(mac_attr, {
            0: mac_base.eq(s[0] << 12),
            1: mac_base.eq(t[0] << 12),
            2: mac_base.eq(Cat(Constant(0, 16), z[0], Constant(0, 1))),
            3: mac_base.eq(_ch_q12_20_s(rgba[0], 16)),
            4: mac_base.eq(_ch_q12_20_s(rgba[0], 8)),
            5: mac_base.eq(_ch_q12_20_s(rgba[0], 0)),
            6: mac_base.eq(_ch_q12_20_s(rgba[0], 24)),
            7: mac_base.eq(Cat(Constant(0, 12), q[0])),
            "default": mac_base.eq(0),
        })

        fsm = FSM(reset_state="IDLE")
        self.submodules += fsm

        self.comb += [
            dx_rd.adr.eq(Mux(fsm.ongoing("SPAN_STEP"), walk_idx[0:3], mac_attr)),
            self.busy.eq(~fsm.ongoing("IDLE")),
            self.valid_out.eq(fsm.ongoing("SPAN_WALK")),
        ]

        q_bad = w0[10] & (
            (self.words[7] < 0x00400000) | (self.words[7] > 0x40000000) |
            (self.words[13] < 0x00400000) | (self.words[13] > 0x40000000) |
            (self.words[19] < 0x00400000) | (self.words[19] > 0x40000000)
        )
        fsm.act("IDLE",
            If(self.start,
                NextValue(self.done, 0),
                NextValue(self.err_param, 0),
                If((w23 != 0) | q_bad,
                    NextValue(self.err_param, 1),
                    NextValue(self.done, 1),
                ).Else(
                    NextValue(swap_v12, 0),
                    NextState("SETUP_AREA0"),
                ),
            ).Else(
                NextValue(self.done, 0),
            )
        )

        # Unswapped edge vectors for SETUP_AREA0 (swap_v12 is always 0 before SETUP_AREA)
        x1_0 = Signal((16, True))
        y1_0 = Signal((16, True))
        x2_0 = Signal((16, True))
        y2_0 = Signal((16, True))
        dx10_0 = Signal((17, True))
        dy10_0 = Signal((17, True))
        dx20_0 = Signal((17, True))
        dy20_0 = Signal((17, True))
        self.comb += [
            x1_0.eq(self.words[9][0:16]),
            y1_0.eq(self.words[9][16:32]),
            x2_0.eq(self.words[15][0:16]),
            y2_0.eq(self.words[15][16:32]),
            dx10_0.eq(x1_0 - x[0]),
            dy10_0.eq(y1_0 - y[0]),
            dx20_0.eq(x2_0 - x[0]),
            dy20_0.eq(y2_0 - y[0]),
        ]
        fsm.act("SETUP_AREA0",
            NextValue(p1, dx10_0 * dy20_0),
            NextValue(p2, dy10_0 * dx20_0),
            NextValue(y_min_r, y_min_q),
            NextValue(y_max_r, y_max_q),
            NextState("SETUP_AREA1"),
        )

        area_bad = Signal(reset_less=True)
        e_area_neg = Signal(34, reset_less=True)
        fsm.act("SETUP_AREA1",
            NextValue(e_area, area_comb),
            NextValue(area_zero, area_comb == 0),
            NextValue(y_start_r, y_start_c),
            NextValue(y_end_r, y_end_c),
            NextState("SETUP_AREA1b"),
        )
        fsm.act("SETUP_AREA1b",
            NextValue(area_bad, area_zero | (sc_min_x > sc_max_x) | (sc_min_y > sc_max_y) | (y_start_r > y_end_r)),
            NextValue(e_area_neg, (-e_area)[0:34]),
            NextState("SETUP_AREA"),
        )

        fsm.act("SETUP_AREA",
            If(area_bad,
                NextValue(self.done, 1),
                NextState("IDLE"),
            ).Else(
                If(e_area[34],
                    # Swap V1 <-> V2 so E_area > 0
                    NextValue(swap_v12, 1),
                    NextValue(e_area_abs, e_area_neg),
                ).Else(
                    NextValue(e_area_abs, e_area[0:34]),
                ),
                NextValue(y_cur, y_start_r),
                NextValue(y_end, y_end_r),
                NextState("SETUP_CLZ"),
            )
        )

        fsm.act("SETUP_CLZ",
            NextValue(clz_val, clz_comb),
            NextState("SETUP_CLZ2"),
        )

        fsm.act("SETUP_CLZ2",
            NextValue(e_norm, e_area_abs << clz_val),
            # Compute floor(2^82 / e_norm): 2^33 <= e_norm < 2^34, so at step 49
            # rem_next = div_rem << 1 must be 2^33 (hence initial div_rem = 1 << 32).
            NextValue(div_rem, 1 << 32),
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
                NextState("SETUP_GRAD_MUX"),
            ).Else(
                NextValue(div_cnt, div_cnt - 1),
            )
        )

        # Multi-cycle gradient compute: mux -> sub -> register 34x17 operands -> multiply -> sub/abs -> 4x 16b*50b
        attr_0_r = Signal((34, True), reset_less=True)
        attr_1_r = Signal((34, True), reset_less=True)
        attr_2_r = Signal((34, True), reset_less=True)
        fsm.act("SETUP_GRAD_MUX",
            NextValue(attr_0_r, attr_0),
            NextValue(attr_1_r, attr_1),
            NextValue(attr_2_r, attr_2),
            NextValue(mul_d1_r, mul_d1),
            NextValue(mul_d2_r, mul_d2),
            NextState("SETUP_GRAD_SUB"),
        )
        fsm.act("SETUP_GRAD_SUB",
            NextValue(da10_r, attr_1_r - attr_0_r),
            NextValue(da20_r, attr_2_r - attr_0_r),
            NextState("SETUP_GRAD_PREP"),
        )
        num_raw_r = Signal((64, True), reset_less=True)
        fsm.act("SETUP_GRAD_PREP",
            NextValue(gp_p1, da10_r * mul_d1_r),
            NextValue(gp_p2, da20_r * mul_d2_r),
            NextState("SETUP_GRAD_DIFF"),
        )
        fsm.act("SETUP_GRAD_DIFF",
            NextValue(num_raw_r, num_raw),
            NextState("SETUP_GRAD_LOAD"),
        )
        gmul_lo33 = Signal(33, reset_less=True)
        fsm.act("SETUP_GRAD_LOAD",
            NextValue(gmul_sign, num_raw_r[63]),
            NextValue(gmul_a, Mux(num_raw_r[63], num_raw_r ^ ((1 << 64) - 1), num_raw_r)),
            NextValue(gmul_acc, 0),
            NextValue(gmul_step, 0),
            NextState("SETUP_GRAD_LOAD2"),
        )
        fsm.act("SETUP_GRAD_LOAD2",
            If(gmul_sign,
                NextValue(gmul_lo33, gmul_a[0:32] + 1),
            ),
            NextState("SETUP_GRAD_LOAD3"),
        )
        fsm.act("SETUP_GRAD_LOAD3",
            If(gmul_sign,
                NextValue(gmul_a, Cat(gmul_lo33[0:32], gmul_a[32:64] + gmul_lo33[32])),
            ),
            NextState("SETUP_GRAD_MUL0"),
        )

        gmul_b16_r = Signal(16, reset_less=True)
        partial_lo_r = Signal(48, reset_less=True)
        partial_hi_r = Signal(34, reset_less=True)
        partial_r = Signal(66, reset_less=True)
        gmul_lo_sum = Signal(51, reset_less=True)
        gmul_lo16_r = Signal(16, reset_less=True)
        gmul_hi0_r = Signal(34, reset_less=True)
        gmul_hi1_r = Signal(34, reset_less=True)
        gmul_hi0_sum = Signal(35, reset_less=True)
        fsm.act("SETUP_GRAD_MUL0",
            NextValue(gmul_b16_r, gmul_a[48:64]),
            NextState("SETUP_GRAD_MUL"),
        )
        fsm.act("SETUP_GRAD_MUL",
            NextValue(partial_lo_r, inv_e_norm[0:32] * gmul_b16_r),
            NextValue(partial_hi_r, inv_e_norm[32:50] * gmul_b16_r),
            NextState("SETUP_GRAD_MUL1b"),
        )
        fsm.act("SETUP_GRAD_MUL1b",
            NextValue(partial_r, Cat(partial_lo_r[0:32], partial_lo_r[32:48] + partial_hi_r)),
            NextState("SETUP_GRAD_MUL2"),
        )
        fsm.act("SETUP_GRAD_MUL2",
            NextValue(gmul_lo16_r, partial_r[0:16]),
            NextValue(gmul_lo_sum, gmul_acc[0:50] + partial_r[16:66]),
            NextValue(gmul_hi0_r, gmul_acc[50:84]),
            NextValue(gmul_hi1_r, gmul_acc[84:118]),
            NextState("SETUP_GRAD_MUL3"),
        )
        fsm.act("SETUP_GRAD_MUL3",
            NextValue(gmul_hi0_sum, gmul_hi0_r + gmul_lo_sum[50]),
            NextState("SETUP_GRAD_MUL4"),
        )
        gmul_hi1_lo = Signal(17)
        gmul_hi1_c = Signal()
        gmul_hi1_hi = Signal(17)
        self.comb += [
            gmul_hi1_lo.eq(gmul_hi1_r[0:17] + gmul_hi0_sum[34]),
            gmul_hi1_c.eq((gmul_hi1_r[0:17] == 0x1FFFF) & gmul_hi0_sum[34]),
            gmul_hi1_hi.eq(Mux(gmul_hi1_c, gmul_hi1_r[17:34] + 1, gmul_hi1_r[17:34])),
        ]
        fsm.act("SETUP_GRAD_MUL4",
            NextValue(gmul_acc, Cat(gmul_lo16_r, gmul_lo_sum[0:50], gmul_hi0_sum[0:34], gmul_hi1_lo, gmul_hi1_hi)),
            If(gmul_step == 3,
                NextValue(norm_cnt, clz_val),
                NextValue(norm_done, clz_val == 0),
                NextState("SETUP_GRAD_NORM"),
            ).Else(
                NextValue(gmul_a, gmul_a << 16),
                NextValue(gmul_step, gmul_step + 1),
                NextState("SETUP_GRAD_MUL0"),
            )
        )

        grad_mag_lo = Signal(27, reset_less=True)
        grad_mag_r = Signal(51, reset_less=True)
        grad_neg_lo = Signal(26, reset_less=True)
        grad_signed_r = Signal(50, reset_less=True)
        fsm.act("SETUP_GRAD_NORM",
            If(norm_done,
                NextValue(grad_mag_lo, gmul_acc[82:108] + gmul_acc[81]),
                NextState("SETUP_GRAD_NORM2"),
            ).Else(
                NextValue(gmul_acc, gmul_acc << 1),
                NextValue(norm_cnt, norm_cnt - 1),
                NextValue(norm_done, norm_cnt == 1),
            )
        )
        fsm.act("SETUP_GRAD_NORM2",
            NextValue(grad_mag_r, Cat(grad_mag_lo[0:26], gmul_acc[108:133] + grad_mag_lo[26])),
            NextState("SETUP_GRAD_SIGN"),
        )

        fsm.act("SETUP_GRAD_SIGN",
            NextValue(grad_neg_lo, (grad_mag_r[0:25] ^ 0x1FFFFFF) + 1),
            NextState("SETUP_GRAD_SIGN2"),
        )
        fsm.act("SETUP_GRAD_SIGN2",
            NextValue(grad_signed_r, Mux(
                gmul_sign,
                Cat(grad_neg_lo[0:25], ((grad_mag_r[25:50] ^ 0x1FFFFFF) + grad_neg_lo[25])[0:25]),
                grad_mag_r[0:50],
            )),
            NextState("SETUP_GRAD_STORE"),
        )
        self.comb += [
            dx_wr.adr.eq(grad_idx[1:4]),
            dx_wr.dat_w.eq(grad_signed_r),
            dx_wr.we.eq(fsm.ongoing("SETUP_GRAD_STORE") & ~grad_idx[0]),
            dy_wr.adr.eq(grad_idx[1:4]),
            dy_wr.dat_w.eq(grad_signed_r),
            dy_wr.we.eq(fsm.ongoing("SETUP_GRAD_STORE") & grad_idx[0]),
        ]
        fsm.act("SETUP_GRAD_STORE",
            If((grad_idx == 15) | ((grad_idx == 13) & ~persp_en) | ((grad_idx == 5) & ~gouraud_en & ~persp_en),
                NextValue(edge_idx, 0),
                NextValue(has_left, 0),
                NextValue(has_right, 0),
                NextState("SPAN_EDGE_SEL"),
            ).Elif((grad_idx == 5) & ~gouraud_en & persp_en,
                NextValue(grad_idx, 14),
                NextState("SETUP_GRAD_MUX"),
            ).Else(
                NextValue(grad_idx, grad_idx + 1),
                NextState("SETUP_GRAD_MUX"),
            )
        )

        # Per-scanline edge intersection (3 edges)
        # Pipelined into SPAN_EDGE_PREP (products) -> SPAN_EDGE_PREP2 (sub/add + ceil_div setup)
        em_D = Signal((18, True), reset_less=True)
        em_ex0 = Signal((18, True), reset_less=True)
        em_edx = Signal((17, True), reset_less=True)
        em_ydiff = Signal((18, True), reset_less=True)
        ep_a = Signal((36, True), reset_less=True)
        ep_b = Signal((36, True), reset_less=True)
        ep_nz = Signal(reset_less=True)
        ep_c0 = Signal(reset_less=True)
        ep_c1 = Signal(reset_less=True)
        ep_active = Signal(reset_less=True)
        ep_is_left = Signal(reset_less=True)
        ep_D = Signal(17, reset_less=True)
        ep_lhs = Signal((36, True))
        ep_lhs_r = Signal((36, True), reset_less=True)
        ediv_den_comb = Signal(21)
        ediv_den_m1_r = Signal(21, reset_less=True)
        self.comb += [
            ep_lhs.eq(Mux(ep_is_left, ep_a - ep_b, ep_a + ep_b)),
            ediv_den_comb.eq(ep_D << 4),
        ]
        fsm.act("SPAN_EDGE_SEL",
            NextValue(ex0, ex0_c),
            NextValue(ey0, ey0_c),
            NextValue(ex1, ex1_c),
            NextValue(ey1, ey1_c),
            NextState("SPAN_EDGE_MUX"),
        )
        ep_c0_l = Signal(reset_less=True)
        ep_c0_r = Signal(reset_less=True)
        ep_c1_l = Signal(reset_less=True)
        ep_c1_r = Signal(reset_less=True)
        edy_r = Signal((17, True), reset_less=True)
        edy_neg_r = Signal((17, True), reset_less=True)
        fsm.act("SPAN_EDGE_MUX",
            NextValue(edy_r, ey1 - ey0),
            NextValue(edy_neg_r, ey0 - ey1),
            NextValue(em_ex0, ex0_s18 - 8),
            NextValue(em_edx, edx),
            NextValue(em_ydiff, y_sample - ey0),
            NextValue(ep_c0_l, y_sample >= ey1),
            NextValue(ep_c0_r, y_sample >= ey0),
            NextValue(ep_c1_l, y_sample <= ey0),
            NextValue(ep_c1_r, y_sample <= ey1),
            NextState("SPAN_EDGE_MUX2"),
        )
        fsm.act("SPAN_EDGE_MUX2",
            NextValue(ep_is_left, edy_r[16]),
            NextValue(ep_D, Mux(edy_r[16], edy_neg_r[0:17], edy_r[0:17])),
            NextValue(em_D, Cat(Mux(edy_r[16], edy_neg_r[0:17], edy_r[0:17]), Constant(0, 1))),
            NextValue(ep_nz, edy_r != 0),
            NextValue(ep_c0, Mux(edy_r[16], ep_c0_l, ep_c0_r)),
            NextValue(ep_c1, Mux(edy_r[16], ep_c1_l, ep_c1_r)),
            NextState("SPAN_EDGE_PREP"),
        )
        fsm.act("SPAN_EDGE_PREP",
            NextValue(ep_a, em_D * em_ex0),
            NextValue(ep_b, em_edx * em_ydiff),
            NextValue(ep_active, ep_nz & ep_c0 & ep_c1),
            NextState("SPAN_EDGE_PREP2"),
        )
        fsm.act("SPAN_EDGE_PREP2",
            NextValue(ep_lhs_r, ep_lhs),
            NextValue(ediv_den_m1_r, ediv_den_comb - 1),
            NextState("SPAN_EDGE_PREP3"),
        )
        fsm.act("SPAN_EDGE_PREP3",
            If(ep_active,
                If(ep_lhs_r <= 0,
                    If(ep_is_left,
                        NextValue(edge_x_left, 0),
                        NextValue(has_left, 1),
                    ).Else(
                        NextValue(edge_x_right, -1),
                        NextValue(has_right, 1),
                    ),
                    If(edge_idx == 2,
                        NextState("SPAN_CHECK0"),
                    ).Else(
                        NextValue(edge_idx, edge_idx + 1),
                        NextState("SPAN_EDGE_SEL"),
                    )
                ).Else(
                    NextValue(ediv_num, (ep_lhs_r + ediv_den_m1_r)[0:36]),
                    NextValue(ediv_den, ediv_den_comb),
                    NextValue(ediv_rem, 0),
                    NextValue(ediv_q, 0),
                    NextValue(ediv_cnt, 15),
                    NextState("SPAN_EDGE_DIV"),
                )
            ).Else(
                If(edge_idx == 2,
                    NextState("SPAN_CHECK0"),
                ).Else(
                    NextValue(edge_idx, edge_idx + 1),
                    NextState("SPAN_EDGE_SEL"),
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
                NextState("SPAN_CHECK0"),
            ).Else(
                NextValue(edge_idx, edge_idx + 1),
                NextState("SPAN_EDGE_SEL"),
            )
        )

        xs_clamp = Mux(edge_x_left > sc_minx_s, edge_x_left, sc_minx_s)
        xe_clamp = Mux(edge_x_right < sc_maxx_s, edge_x_right, sc_maxx_s)
        span_lr = Signal(reset_less=True)
        span_ok = Signal(reset_less=True)
        xs_s17 = Signal((17, True))
        self.comb += xs_s17.eq(x_start)
        fsm.act("SPAN_CHECK0",
            NextValue(x_start, xs_clamp),
            NextValue(x_end, xe_clamp),
            NextValue(span_lr, has_left & has_right),
            NextState("SPAN_CHECK1"),
        )
        fsm.act("SPAN_CHECK1",
            NextValue(span_ok, span_lr & (x_start <= x_end)),
            NextState("SPAN_CHECK"),
        )
        fsm.act("SPAN_CHECK",
            If(span_ok,
                NextValue(x_cur, x_start[0:12]),
                NextValue(dx_q13_4, (xs_s17 << 4) + 8 - x[0]),
                NextValue(dy_q13_4, (y_cur_s17 << 4) + 8 - y[0]),
                NextValue(mac_attr, 0),
                NextValue(mac_phase, 0),
                NextState("SPAN_MAC_PRE"),
            ).Else(
                If(y_cur >= y_end,
                    NextValue(self.done, 1),
                    NextState("IDLE"),
                ).Else(
                    NextValue(y_cur, y_cur + 1),
                    NextValue(edge_idx, 0),
                    NextValue(has_left, 0),
                    NextValue(has_right, 0),
                    NextState("SPAN_EDGE_SEL"),
                )
            )
        )

        # 82-bit Row-start MAC: 4 phases per attribute, pipelined (SPAN_MAC -> SPAN_MAC_ACC)
        # phase 0: mac_base + dx * gx[0:25]
        # phase 1: mac_acc  + (dx * gx[25:50]) << 25
        # phase 2: mac_acc  + dy * gy[0:25]
        # phase 3: mac_acc  + (dy * gy[25:50]) << 25 + 8 -> >> 4
        mac_d = Signal((17, True))
        mac_g_full = Signal((50, True))
        mac_g26 = Signal((26, True))
        mac_prod_r = Signal((43, True), reset_less=True)
        mac_prod_shifted = Signal((52, True))
        mac_sum = Signal((52, True))
        mac_res = Signal((48, True))
        mac_gx_r = Signal((50, True), reset_less=True)
        mac_gy_r = Signal((50, True), reset_less=True)
        mac_base_r = Signal((49, True), reset_less=True)
        self.comb += [
            mac_d.eq(Mux(mac_phase[1] == 0, dx_q13_4, dy_q13_4)),
            mac_g_full.eq(Mux(mac_phase[1] == 0, mac_gx_r, mac_gy_r)),
            mac_g26.eq(Mux(mac_phase[0] == 0,
                           Cat(mac_g_full[0:25], Constant(0, 1)),
                           Cat(mac_g_full[25:50], mac_g_full[49]))),
            mac_prod_shifted.eq(Mux(mac_phase[0] == 0, mac_prod_r, mac_prod_r << 25)),
            mac_sum.eq(Mux(mac_phase == 0, mac_base_r, mac_acc[0:52]) + mac_prod_shifted),
            mac_res.eq((mac_acc[0:52] + 8) >> 4),
        ]
        fsm.act("SPAN_MAC_PRE",
            NextValue(mac_base_r, mac_base),
            NextState("SPAN_MAC_PRE2"),
        )
        fsm.act("SPAN_MAC_PRE2",
            NextValue(mac_gx_r, mac_gx),
            NextValue(mac_gy_r, mac_gy),
            NextState("SPAN_MAC"),
        )
        fsm.act("SPAN_MAC",
            NextValue(mac_prod_r, mac_d * mac_g26),
            NextState("SPAN_MAC_ACC"),
        )
        fsm.act("SPAN_MAC_ACC",
            NextValue(mac_acc, mac_sum),
            If(mac_phase != 3,
                NextValue(mac_phase, mac_phase + 1),
                NextState("SPAN_MAC"),
            ).Else(
                NextValue(mac_phase, 0),
                NextState("SPAN_MAC_RND"),
            )
        )
        mac_res_r = Signal((48, True), reset_less=True)
        fsm.act("SPAN_MAC_RND",
            NextValue(mac_res_r, mac_res),
            NextState("SPAN_MAC_RND2"),
        )
        self.comb += [
            self.row_y.eq(y_cur[0:12]),
            self.row_x0.eq(x_start[0:12]),
            self.row_x1.eq(x_end[0:12]),
        ]
        fsm.act("SPAN_MAC_RND2",
            Case(mac_attr, {
                0: NextValue(s_acc, mac_res_r[0:42]),
                1: NextValue(t_acc, mac_res_r[0:42]),
                2: NextValue(z_acc, mac_res_r[0:46]),
                3: NextValue(r_acc, mac_res_r[0:38]),
                4: NextValue(g_acc, mac_res_r[0:38]),
                5: NextValue(b_acc, mac_res_r[0:38]),
                6: NextValue(a_acc, mac_res_r[0:38]),
                7: NextValue(q_acc, mac_res_r[0:40]),
            }),
            If((mac_attr == 7) | ((mac_attr == 6) & ~persp_en) | ((mac_attr == 2) & ~gouraud_en & ~persp_en),
                NextState("SPAN_ROW_SYNC"),
            ).Elif((mac_attr == 2) & ~gouraud_en & persp_en,
                NextValue(mac_attr, 7),
                NextState("SPAN_MAC_PRE"),
            ).Else(
                NextValue(mac_attr, mac_attr + 1),
                NextState("SPAN_MAC_PRE"),
            )
        )
        fsm.act("SPAN_ROW_SYNC",
            self.row_sync_req.eq(1),
            If(self.row_sync_ack,
                NextState("SPAN_WALK_RND"),
            ),
        )

        # Shared 46-bit accumulator step for SPAN_STEP -> SPAN_STEP2 (reads dx_mem sequentially)
        step_cur = Signal((46, True))
        step_cur_r = Signal((46, True), reset_less=True)
        dx_rd_r = Signal((46, True), reset_less=True)
        step_sum = Signal((46, True))
        self.comb += [
            Case(walk_idx, {
                0: step_cur.eq(s_acc),
                1: step_cur.eq(t_acc),
                2: step_cur.eq(z_acc),
                3: step_cur.eq(r_acc),
                4: step_cur.eq(g_acc),
                5: step_cur.eq(b_acc),
                6: step_cur.eq(a_acc),
                7: step_cur.eq(q_acc),
                "default": step_cur.eq(0),
            }),
            step_sum.eq(step_cur_r + dx_rd_r),
        ]

        # Register rounded/clamped outputs ahead of SPAN_WALK (2-stage)
        fsm.act("SPAN_WALK_RND",
            NextValue(s_q14_16_r, (s_acc + (1 << 11)) >> 12),
            NextValue(t_q14_16_r, (t_acc + (1 << 11)) >> 12),
            NextValue(s_q14_20_r, (s_acc + 128) >> 8),
            NextValue(t_q14_20_r, (t_acc + 128) >> 8),
            NextValue(q_rnd_r, (q_acc + 128) >> 8),
            NextValue(z_rnd_r, (z_acc + (1 << 11)) >> 12),
            NextState("SPAN_WALK_PRE"),
        )
        fsm.act("SPAN_WALK_PRE",
            NextValue(z_out_r, z_clamped),
            NextValue(s_q32_16_r, s_q14_16_r),
            NextValue(t_q32_16_r, t_q14_16_r),
            NextValue(s_q12_20_r, s_sat_20),
            NextValue(t_q12_20_r, t_sat_20),
            NextValue(q_clamped_r, q_clamped_v),
            NextValue(rgba_out_r, Mux(gouraud_en, Cat(b_u8, g_u8, r_u8, a_u8), rgba[0])),
            NextState("SPAN_WALK"),
        )

        # Pixel Streamer (1 pixel per SPAN_WALK + 2-cycle/attr SPAN_STEP)
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
                        NextState("SPAN_EDGE_SEL"),
                    )
                ).Else(
                    NextValue(x_cur, x_cur + 1),
                    NextValue(walk_idx, 0),
                    NextState("SPAN_STEP"),
                )
            )
        )
        fsm.act("SPAN_STEP",
            NextValue(step_cur_r, step_cur),
            NextState("SPAN_STEP1b"),
        )
        fsm.act("SPAN_STEP1b",
            NextValue(dx_rd_r, dx_rd.dat_r[0:46]),
            NextState("SPAN_STEP2"),
        )
        fsm.act("SPAN_STEP2",
            Case(walk_idx, {
                0: NextValue(s_acc, step_sum[0:42]),
                1: NextValue(t_acc, step_sum[0:42]),
                2: NextValue(z_acc, step_sum[0:46]),
                3: NextValue(r_acc, step_sum[0:38]),
                4: NextValue(g_acc, step_sum[0:38]),
                5: NextValue(b_acc, step_sum[0:38]),
                6: NextValue(a_acc, step_sum[0:38]),
                7: NextValue(q_acc, step_sum[0:40]),
            }),
            If((walk_idx == 7) |
               ((walk_idx == 2) & ~gouraud_en & ~persp_en) |
               ((walk_idx == 6) & ~persp_en),
                NextState("SPAN_WALK_RND"),
            ).Elif((walk_idx == 2) & ~gouraud_en & persp_en,
                NextValue(walk_idx, 7),
                NextState("SPAN_STEP"),
            ).Else(
                NextValue(walk_idx, walk_idx + 1),
                NextState("SPAN_STEP"),
            )
        )
