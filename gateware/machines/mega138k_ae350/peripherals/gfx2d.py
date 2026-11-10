"""GFX2D 2D pixel engine with Phase 2 programmable shader pipeline (Step 6a)."""
from migen import (Array, Case, Cat, ClockDomainsRenamer, Constant, FSM,
                   If, Memory, Mux, NextState, NextValue, Signal)
from litex.gen import LiteXModule
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI

ERR_NONE = 0
ERR_UNSUPPORTED = 1
ERR_BUSY = 2
ERR_PARAM = 3
ERR_ADDR = 4
ERR_TIMEOUT = 5

OP_FILL_RECT = 0
OP_COPY_AREA = 1
OP_IMAGE_BLIT = 2
OP_SHADER = 3
OP_SCALE_BLIT = 4

FMT_RGB565 = 0
FMT_RGB888 = 1
FMT_XRGB8888 = 2
FMT_XRGB1555 = 3
FMT_ARGB8888 = 4
FMT_ARGB1555 = 5
FMT_INDEX8 = 6
FMT_MONO1 = 7

DESC_NAMES = (
    'OP', 'SRC_ADDR', 'DST_ADDR', 'SRC_STRIDE', 'DST_STRIDE',
    'WIDTH', 'HEIGHT', 'FG_COLOR', 'BG_COLOR', 'BLIT_SRC_WB',
    'FORMAT', 'SHADER_CFG', 'SRC_WIDTH', 'SRC_HEIGHT',
    'UV_START_U', 'UV_START_V', 'UV_DU_DX', 'UV_DV_DY',
    'INST0_LO', 'INST0_HI', 'INST1_LO', 'INST1_HI',
    'INST2_LO', 'INST2_HI', 'INST3_LO', 'INST3_HI',
    'UNIFORM0', 'UNIFORM1', 'UNIFORM2', 'UNIFORM3',
    'UNIFORM4', 'UNIFORM5', 'UNIFORM6', 'UNIFORM7',
    'LUT_ADDR',
)


class ShaderALU(LiteXModule):
    def __init__(self, stage_idx):
        self.inst = Signal(64)
        self.last_stage = Signal(2)
        self.r_in = [[Signal((16, True)) for _ in range(4)] for _ in range(4)]
        self.src_pix = [Signal((16, True)) for _ in range(4)]
        self.dst_pix = [Signal((16, True)) for _ in range(4)]
        self.fg_pix = [Signal((16, True)) for _ in range(4)]
        self.bg_pix = [Signal((16, True)) for _ in range(4)]
        self.uniforms = [[Signal((16, True)) for _ in range(4)] for _ in range(8)]
        self.p_in = Signal()
        self.discard_in = Signal()

        self.r_out = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        self.src_out = [Signal((16, True), reset_less=True) for _ in range(4)]
        self.dst_out = [Signal((16, True), reset_less=True) for _ in range(4)]
        self.fg_out = [Signal((16, True), reset_less=True) for _ in range(4)]
        self.bg_out = [Signal((16, True), reset_less=True) for _ in range(4)]
        self.p_out = Signal()
        self.discard_out = Signal()

        opcode = Signal(6)
        pred = Signal(2)
        dst_idx = Signal(4)
        wr_msk = Signal(4)
        src0_idx = Signal(4)
        src1_idx = Signal(4)
        src2_idx = Signal(4)
        swz0 = Signal(8)
        swz1 = Signal(8)
        swz2 = Signal(8)
        sat = Signal()
        use_imm = Signal()
        sub_op = Signal(2)
        imm8 = Signal(8)

        self.comb += [
            opcode.eq(self.inst[58:64]),
            pred.eq(self.inst[56:58]),
            dst_idx.eq(self.inst[52:56]),
            wr_msk.eq(self.inst[48:52]),
            src0_idx.eq(self.inst[44:48]),
            src1_idx.eq(self.inst[40:44]),
            src2_idx.eq(self.inst[36:40]),
            swz0.eq(self.inst[28:36]),
            swz1.eq(self.inst[20:28]),
            swz2.eq(self.inst[12:20]),
            sat.eq(self.inst[11]),
            use_imm.eq(self.inst[10]),
            sub_op.eq(self.inst[8:10]),
            imm8.eq(self.inst[0:8]),
        ]

        stage_active = Signal()
        self.comb += stage_active.eq(stage_idx <= self.last_stage)

        pred_ok = Signal()
        self.comb += [
            If(pred == 0, pred_ok.eq(1))
            .Elif(pred == 1, pred_ok.eq(self.p_in == 1))
            .Elif(pred == 2, pred_ok.eq(self.p_in == 0))
            .Else(pred_ok.eq(1))
        ]

        def select_src(idx):
            res = [Signal((16, True)) for _ in range(4)]
            cases = {}
            for r in range(4):
                cases[r] = [res[c].eq(self.r_in[r][c]) for c in range(4)]
            cases[4] = [res[c].eq(self.src_pix[c]) for c in range(4)]
            cases[5] = [res[c].eq(self.dst_pix[c]) for c in range(4)]
            cases[6] = [res[c].eq(self.fg_pix[c]) for c in range(4)]
            cases[7] = [res[c].eq(self.bg_pix[c]) for c in range(4)]
            for u in range(8):
                cases[8 + u] = [res[c].eq(self.uniforms[u][c]) for c in range(4)]
            self.comb += Case(idx, cases)
            return res

        s0_raw = select_src(src0_idx)
        s1_raw = select_src(src1_idx)
        s2_raw = select_src(src2_idx)

        def apply_swz(raw, swz):
            out = [Signal((16, True)) for _ in range(4)]
            for c in range(4):
                self.comb += out[c].eq(Array(raw)[swz[2*c : 2*c+2]])
            return out

        s0_vec = apply_swz(s0_raw, swz0)
        s1_swz = apply_swz(s1_raw, swz1)
        s1_vec = [Signal((16, True)) for _ in range(4)]
        self.comb += [
            If(use_imm, [s1_vec[c].eq(imm8) for c in range(4)])
            .Else([s1_vec[c].eq(s1_swz[c]) for c in range(4)])
        ]
        s2_vec = apply_swz(s2_raw, swz2)

        # Pipeline Sub-stage 1 registers (P1)
        p1_s0 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p1_s1 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p1_s2 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p1_r = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p1_src = [Signal((16, True), reset_less=True) for _ in range(4)]
        p1_dst = [Signal((16, True), reset_less=True) for _ in range(4)]
        p1_fg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p1_bg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p1_p = Signal()
        p1_discard = Signal()
        p1_stage_active = Signal()
        p1_pred_ok = Signal()
        p1_dst_idx = Signal(4, reset_less=True)
        p1_wr_msk = Signal(4, reset_less=True)
        p1_opcode = Signal(6, reset_less=True)
        p1_pred = Signal(2, reset_less=True)
        p1_sat = Signal(reset_less=True)
        p1_sub_op = Signal(2, reset_less=True)
        p1_imm8 = Signal(8, reset_less=True)

        for c in range(4):
            self.sync += [
                p1_s0[c].eq(s0_vec[c]),
                p1_s1[c].eq(s1_vec[c]),
                p1_s2[c].eq(s2_vec[c]),
                p1_src[c].eq(self.src_pix[c]),
                p1_dst[c].eq(self.dst_pix[c]),
                p1_fg[c].eq(self.fg_pix[c]),
                p1_bg[c].eq(self.bg_pix[c]),
            ]
            for r in range(4):
                self.sync += p1_r[r][c].eq(self.r_in[r][c])
        self.sync += [
            p1_p.eq(self.p_in),
            p1_discard.eq(self.discard_in),
            p1_stage_active.eq(stage_active),
            p1_pred_ok.eq(pred_ok),
            p1_dst_idx.eq(dst_idx),
            p1_wr_msk.eq(wr_msk),
            p1_opcode.eq(opcode),
            p1_pred.eq(pred),
            p1_sat.eq(sat),
            p1_sub_op.eq(sub_op),
            p1_imm8.eq(imm8),
        ]

        # Pipeline Sub-stage 2 registers (P2)
        p2_s0 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_s1 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_s2 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_r = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p2_src = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_dst = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_fg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_bg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_p = Signal()
        p2_discard = Signal()
        p2_stage_active = Signal()
        p2_pred_ok = Signal()
        p2_dst_idx = Signal(4, reset_less=True)
        p2_wr_msk = Signal(4, reset_less=True)
        p2_opcode = Signal(6, reset_less=True)
        p2_pred = Signal(2, reset_less=True)
        p2_sat = Signal(reset_less=True)

        p2_add = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_sub = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_diff = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_min = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_max = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_clamp = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_and = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_or = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_xor = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_shl = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_shr = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_mul_unorm = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_mul_scaled = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_muladd = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_lerp = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_match_ch = [Signal(reset_less=True) for _ in range(4)]

        def sat_clamp(val):
            return Mux(p1_sat,
                       Mux(val < 0, 0, Mux(val > 255, 255, val)),
                       val)

        for c in range(4):
            s0 = p1_s0[c]
            s1 = p1_s1[c]
            s2 = p1_s2[c]
            lerp_w = Signal(10)
            lerp_diff = Signal((10, True))
            lerp_prod = Signal((20, True))
            lerp_res = Signal((17, True))
            prod_raw = Signal(17)
            prod_unorm = Signal(16)
            muladd_res = Signal((17, True))
            add_val = Signal((17, True))
            sub_val = Signal((17, True))
            diff_val = Signal((17, True))
            min_val = Signal((17, True))
            max_val = Signal((17, True))
            clamp_val = Signal((17, True))
            shl_val = Signal((16, True))
            shr_val = Signal((16, True))
            mul_scaled_val = Signal((16, True))

            self.comb += [
                lerp_w.eq(s2[0:8] + (s2[0:8] >> 7)),
                lerp_diff.eq(s0[0:9] - s1[0:9]),
                lerp_prod.eq(lerp_diff * lerp_w),
                lerp_res.eq((lerp_prod + (s1[0:8] << 8) + 128) >> 8),
                prod_raw.eq(s0[0:8] * s1[0:8] + 128),
                prod_unorm.eq((prod_raw + (prod_raw >> 8)) >> 8),
                muladd_res.eq(s2 + prod_unorm),
                add_val.eq(s0 + s1),
                sub_val.eq(s0 - s1),
                diff_val.eq(Mux(s0 >= s1, s0 - s1, s1 - s0)),
                min_val.eq(Mux(s0 < s1, s0, s1)),
                max_val.eq(Mux(s0 > s1, s0, s1)),
                clamp_val.eq(Mux(s0 < s1, s1, Mux(s0 > s2, s2, s0))),
                shl_val.eq(s0 << p1_imm8[0:4]),
                shr_val.eq(s0 >> p1_imm8[0:4]),
                mul_scaled_val.eq((s0 * s1) >> p1_imm8[0:4]),
            ]
            self.sync += [
                p2_s0[c].eq(sat_clamp(s0)),
                p2_s1[c].eq(s1),
                p2_s2[c].eq(s2),
                p2_src[c].eq(p1_src[c]),
                p2_dst[c].eq(p1_dst[c]),
                p2_fg[c].eq(p1_fg[c]),
                p2_bg[c].eq(p1_bg[c]),
                p2_mul_unorm[c].eq(sat_clamp(prod_unorm)),
                p2_muladd[c].eq(sat_clamp(muladd_res)),
                p2_lerp[c].eq(sat_clamp(lerp_res)),
                p2_add[c].eq(sat_clamp(add_val)),
                p2_sub[c].eq(sat_clamp(sub_val)),
                p2_diff[c].eq(sat_clamp(diff_val)),
                p2_min[c].eq(sat_clamp(min_val)),
                p2_max[c].eq(sat_clamp(max_val)),
                p2_clamp[c].eq(sat_clamp(clamp_val)),
                p2_and[c].eq(sat_clamp(s0 & s1)),
                p2_or[c].eq(sat_clamp(s0 | s1)),
                p2_xor[c].eq(sat_clamp(s0 ^ s1)),
                p2_shl[c].eq(sat_clamp(shl_val)),
                p2_shr[c].eq(sat_clamp(shr_val)),
                p2_mul_scaled[c].eq(sat_clamp(mul_scaled_val)),
                p2_match_ch[c].eq(
                    Mux(p1_sub_op == 0, s0 == s1,
                    Mux(p1_sub_op == 1, s0 != s1,
                    Mux(p1_sub_op == 2, s0 < s1, s0 >= s1)))
                ),
            ]
            for r in range(4):
                self.sync += p2_r[r][c].eq(p1_r[r][c])

        if stage_idx == 1:
            dot3_p0 = Signal(17)
            dot3_p1 = Signal(17)
            dot3_p2 = Signal(17)
            dot3_sum = Signal(19)
            dot3_res = Signal((17, True))
            p2_dot3 = Signal((16, True), reset_less=True)
            self.comb += [
                dot3_p0.eq(p1_s0[0][0:8] * p1_s1[0][0:8]),
                dot3_p1.eq(p1_s0[1][0:8] * p1_s1[1][0:8]),
                dot3_p2.eq(p1_s0[2][0:8] * p1_s1[2][0:8]),
                dot3_sum.eq(dot3_p0 + dot3_p1 + dot3_p2 + 128),
                dot3_res.eq(dot3_sum >> 8),
            ]
            self.sync += p2_dot3.eq(sat_clamp(dot3_res))

        self.sync += [
            p2_p.eq(p1_p),
            p2_discard.eq(p1_discard),
            p2_stage_active.eq(p1_stage_active),
            p2_pred_ok.eq(p1_pred_ok),
            p2_dst_idx.eq(p1_dst_idx),
            p2_wr_msk.eq(p1_wr_msk),
            p2_opcode.eq(p1_opcode),
            p2_pred.eq(p1_pred),
            p2_sat.eq(p1_sat),
        ]

        # Pipeline Sub-stage 3 (Outputs r_out, p_out, discard_out)
        res_ch = [Signal((16, True)) for _ in range(4)]

        for c in range(4):
            cases = {
                0x00: res_ch[c].eq(p2_s0[c]),
                0x01: res_ch[c].eq(p2_add[c]),
                0x02: res_ch[c].eq(p2_sub[c]),
                0x03: res_ch[c].eq(p2_diff[c]),
                0x04: res_ch[c].eq(p2_min[c]),
                0x05: res_ch[c].eq(p2_max[c]),
                0x06: res_ch[c].eq(p2_clamp[c]),
                0x10: res_ch[c].eq(p2_mul_unorm[c]),
                0x11: res_ch[c].eq(p2_mul_scaled[c]),
                0x12: res_ch[c].eq(p2_muladd[c]),
                0x13: res_ch[c].eq(p2_lerp[c]),
                0x20: res_ch[c].eq(p2_and[c]),
                0x21: res_ch[c].eq(p2_or[c]),
                0x22: res_ch[c].eq(p2_xor[c]),
                0x23: res_ch[c].eq(p2_shl[c]),
                0x24: res_ch[c].eq(p2_shr[c]),
            }
            if stage_idx == 1:
                cases[0x14] = res_ch[c].eq(p2_dot3)
            cases["default"] = res_ch[c].eq(p2_s0[c])
            self.comb += Case(p2_opcode, cases)

        all_match = Signal()
        self.comb += all_match.eq(
            (~p2_wr_msk[0] | p2_match_ch[0]) &
            (~p2_wr_msk[1] | p2_match_ch[1]) &
            (~p2_wr_msk[2] | p2_match_ch[2]) &
            (~p2_wr_msk[3] | p2_match_ch[3])
        )

        for r in range(4):
            for c in range(4):
                self.sync += [
                    If(p2_stage_active & p2_pred_ok & (p2_dst_idx == r) &
                       p2_wr_msk[c] & (p2_opcode != 0x38) & (p2_opcode != 0x39),
                        self.r_out[r][c].eq(res_ch[c])
                    ).Else(
                        self.r_out[r][c].eq(p2_r[r][c])
                    )
                ]

        self.sync += [
            If(p2_stage_active & p2_pred_ok & ((p2_opcode == 0x38) | (p2_pred == 3)),
                self.p_out.eq(all_match)
            ).Else(
                self.p_out.eq(p2_p)
            ),
            If(p2_stage_active & p2_pred_ok & (p2_opcode == 0x39) & all_match,
                self.discard_out.eq(1)
            ).Else(
                self.discard_out.eq(p2_discard)
            ),
            [self.src_out[c].eq(p2_src[c]) for c in range(4)],
            [self.dst_out[c].eq(p2_dst[c]) for c in range(4)],
            [self.fg_out[c].eq(p2_fg[c]) for c in range(4)],
            [self.bg_out[c].eq(p2_bg[c]) for c in range(4)],
        ]


class Gfx2DEngine(LiteXModule):
    def __init__(self, ddr_size=1 << 30, response_timeout=1 << 18,
                 pal_port=None, lut_port=None):
        self.ui = DDR3UI()
        self.req = Signal()
        self.ack = Signal()
        self.done_err = Signal(32)
        self.desc = desc = [Signal(32) for _ in DESC_NAMES]
        self.busy = Signal()

        op, src_addr, dst_addr, src_stride, dst_stride = desc[0:5]
        width, height, fg, bg, blit_wb, fmt = desc[5:11]
        shader_cfg = desc[11]
        src_w, src_h = desc[12], desc[13]
        uv_start_u, uv_start_v, uv_du_dx, uv_dv_dy = desc[14:18]
        inst0_lo, inst0_hi = desc[18], desc[19]
        inst1_lo, inst1_hi = desc[20], desc[21]
        inst2_lo, inst2_hi = desc[22], desc[23]
        inst3_lo, inst3_hi = desc[24], desc[25]
        uniforms_raw = desc[26:34]
        lut_addr_reg = desc[34]

        inst0 = Cat(inst0_lo, inst0_hi)
        inst1 = Cat(inst1_lo, inst1_hi)
        inst2 = Cat(inst2_lo, inst2_hi)
        inst3 = Cat(inst3_lo, inst3_hi)

        last_stage = shader_cfg[0:2]
        uses_src = shader_cfg[4]
        uses_dst = shader_cfg[5]
        bilinear_en = shader_cfg[6]
        dither_en = shader_cfg[7]
        scale_en = shader_cfg[8]
        src_fmt_override = shader_cfg[11]
        src_format = Signal(4)
        dst_format = Signal(4)
        self.comb += [
            dst_format.eq(fmt[0:4]),
            src_format.eq(Mux(src_fmt_override, shader_cfg[12:16], fmt[0:4])),
        ]

        def get_bpp_shift(f):
            return Mux((f == FMT_XRGB8888) | (f == FMT_ARGB8888), 2,
                   Mux(f == FMT_INDEX8, 0, 1))

        dst_bpp_shift = Signal(2)
        src_bpp_shift = Signal(2)
        self.comb += [
            dst_bpp_shift.eq(get_bpp_shift(dst_format)),
            src_bpp_shift.eq(get_bpp_shift(src_format)),
        ]

        req_sync = [Signal() for _ in range(2)]
        self.sync += [req_sync[0].eq(self.req), req_sync[1].eq(req_sync[0])]
        start = req_sync[1] != self.ack

        row_bytes = Signal(32, reset_less=True)
        src_row_bytes = Signal(32, reset_less=True)
        row = Signal(32, reset_less=True)
        rows_left = Signal(32, reset_less=True)
        last_row = Signal(reset_less=True)
        beat = Signal(32, reset_less=True)
        beats = Signal(16, reset_less=True)
        elapsed = Signal(max=response_timeout + 1)
        elapsed_timeout = Signal()
        self.sync += elapsed_timeout.eq(elapsed >= response_timeout - 1)
        err = Signal(32)

        beat_base = Signal(32, reset_less=True)
        offset = Signal(32, reset_less=True)
        row_start = Signal(32, reset_less=True)
        src_row_start = Signal(32, reset_less=True)
        limit = Signal(32, reset_less=True)
        src_limit = Signal(32, reset_less=True)
        dir_down = Signal(reset_less=True)
        self.sync += [
            row_bytes.eq(width << dst_bpp_shift),
            src_row_bytes.eq(width << src_bpp_shift),
        ]

        delta_dst = Signal(32, reset_less=True)
        delta_src = Signal(32, reset_less=True)
        nxt_row_start = Signal(32)
        nxt_src_row_start = Signal(32)
        self.comb += [
            nxt_row_start.eq(row_start + delta_dst),
            nxt_src_row_start.eq(src_row_start + delta_src),
        ]
        beat_byte = Signal(32, reset_less=True)
        rb_hi5 = Signal(16, reset_less=True)
        rb_lo5 = Signal(5, reset_less=True)
        off5 = Signal(5)
        self.comb += off5.eq(row_start[0:5])
        first_mask = Signal(32, reset_less=True)
        last_mask = Signal(32, reset_less=True)
        beats_w = Signal(16)
        geo_sum = Signal(7)
        self.comb += geo_sum.eq(off5 + rb_lo5 + 31)
        self.comb += beats_w.eq(rb_hi5 + geo_sum[5:7])
        first_end = Signal(6)
        self.comb += first_end.eq(Mux((rb_hi5 != 0) | (off5 + rb_lo5 >= 32), 32,
                                     off5 + rb_lo5))
        first_bits = [(off5 <= i) & (i < first_end) for i in range(32)]
        last_count = Signal(6)
        self.comb += last_count.eq((off5 + rb_lo5 - 1)[0:5] + 1)
        last_bits = [i < last_count for i in range(32)]
        beats_left = Signal(16, reset_less=True)
        first_beat = Signal(reset_less=True)
        beat_mask = Signal(32)
        self.comb += beat_mask.eq(Mux(first_beat, first_mask,
                                     Mux(beats_left == 1, last_mask, 0xffffffff)))
        src_beat_base = Signal(32, reset_less=True)
        beat_shift = Signal(256, reset_less=True)
        src_beat_loaded = Signal(reset_less=True)
        src_byte_off = Signal(32, reset_less=True)
        bytes_in_beat = Signal(8, reset_less=True)
        bytes_in_beat_zero = Signal(reset_less=True)
        src_bytes_left = Signal(16, reset_less=True)
        src_bytes_left_zero = Signal(reset_less=True)
        bit_buf = Signal(24, reset_less=True)
        bit_cnt = Signal(5, reset_less=True)
        copy_data = Signal(256, reset_less=True)

        wr_pattern = Signal(256)
        pat_even = Cat(*[fg[:16]] * 16)
        pat_odd = Cat(*[Cat(fg[8:16], fg[0:8])] * 16)
        pat_16 = Signal(256)
        self.comb += pat_16.eq(Mux(offset[0], pat_odd, pat_even))

        pat_32_0 = Cat(*[fg[:32]] * 8)
        pat_32_1 = Cat(*[Cat(fg[24:32], fg[0:24])] * 8)
        pat_32_2 = Cat(*[Cat(fg[16:32], fg[0:16])] * 8)
        pat_32_3 = Cat(*[Cat(fg[8:32], fg[0:8])] * 8)
        pat_32 = Signal(256)
        self.comb += pat_32.eq(Mux(offset[0:2] == 0, pat_32_0,
                               Mux(offset[0:2] == 1, pat_32_1,
                               Mux(offset[0:2] == 2, pat_32_2, pat_32_3))))

        self.comb += wr_pattern.eq(Mux(fmt == FMT_XRGB8888, pat_32, pat_16))

        blit_pix_per_beat = Signal(5)
        self.comb += blit_pix_per_beat.eq(Mux(fmt == FMT_XRGB8888, 8, 16))
        blit_expanded = Signal(256)
        blit_pix_16 = [Mux(bit_buf[23 - j], fg[:16], bg[:16]) for j in range(16)]
        blit_pix_32 = [Mux(bit_buf[23 - j], fg[:32], bg[:32]) for j in range(8)]
        self.comb += blit_expanded.eq(
            Mux(fmt == FMT_XRGB8888, Cat(*blit_pix_32), Cat(*blit_pix_16)))
        blit_exp_reg = Signal(256, reset_less=True)
        blit_shifted = Signal(256, reset_less=True)
        self.comb += blit_shifted.eq(blit_exp_reg)

        # Output DDR registers
        out_addr = Signal(29, reset_less=True)
        out_data = Signal(256, reset_less=True)
        out_mask = Signal(32, reset_less=True)
        prep_data = Signal(256)
        load_out = Signal()
        is_rd_state = Signal()
        rd_addr = Signal(29, reset_less=True)
        src_byte = Signal(8)
        self.comb += src_byte.eq(beat_shift[0:8])
        src_byte_q = Signal(8)
        src_byte_pending = Signal()

        # Line Buffers
        self.src_lb_e = Memory(32, 512, name="src_lb_e")
        self.src_lb_o = Memory(32, 512, name="src_lb_o")
        self.dst_lb = Memory(32, 1024, name="dst_lb")
        self.out_lb = Memory(33, 1024, name="out_lb")

        self.src_lb_e_w = self.src_lb_e.get_port(write_capable=True, clock_domain="ddr")
        self.src_lb_o_w = self.src_lb_o.get_port(write_capable=True, clock_domain="ddr")
        self.src_lb_e_r = self.src_lb_e.get_port(clock_domain="ddr")
        self.src_lb_o_r = self.src_lb_o.get_port(clock_domain="ddr")

        self.dst_lb_w = self.dst_lb.get_port(write_capable=True, clock_domain="ddr")
        self.dst_lb_r = self.dst_lb.get_port(clock_domain="ddr")

        self.out_lb_w = self.out_lb.get_port(write_capable=True, clock_domain="ddr")
        self.out_lb_r = self.out_lb.get_port(clock_domain="ddr")

        self.specials += [
            self.src_lb_e, self.src_lb_e_w, self.src_lb_e_r,
            self.src_lb_o, self.src_lb_o_w, self.src_lb_o_r,
            self.dst_lb, self.dst_lb_w, self.dst_lb_r,
            self.out_lb, self.out_lb_w, self.out_lb_r,
        ]

        # Tile FSM signals
        tile_x = Signal(16, reset_less=True)
        tile_w = Signal(11, reset_less=True)
        tile_last = Signal(11, reset_less=True)
        tile_is_last_in_row = Signal(reset_less=True)
        tile_wb_done = Signal(reset_less=True)
        t_src_cur_blk = Signal(29, reset_less=True)
        t_src_beats_left = Signal(16, reset_less=True)
        t_src_off = Signal(5, reset_less=True)
        t_dst_base_blk = Signal(29, reset_less=True)
        t_dst_cur_blk = Signal(29, reset_less=True)
        t_dst_beats_left = Signal(16, reset_less=True)
        t_dst_off = Signal(5, reset_less=True)
        t_beat_buf = Signal(256, reset_less=True)
        t_src_is_first = Signal(reset_less=True)
        t_dst_is_first = Signal(reset_less=True)
        sub_idx = Signal(6, reset_less=True)
        lb_wr_ptr = Signal(11, reset_less=True)

        # Compute pipeline signals
        comp_idx = Signal(11, reset_less=True)
        comp_out_idx = Signal(11, reset_less=True)
        comp_pipe_vld = Signal(7)
        fetch_x = Signal(16, reset_less=True)
        fetch_odd = Signal(reset_less=True)

        # Shader ALUs
        self.stage0 = stage0 = ShaderALU(0)
        self.stage1 = stage1 = ShaderALU(1)
        self.comb += [
            stage0.inst.eq(inst0),
            stage0.last_stage.eq(last_stage),
            stage1.inst.eq(inst1),
            stage1.last_stage.eq(last_stage),
        ]
        # Unpack uniforms to 4 channels each: 0=.b, 1=.g, 2=.r, 3=.a
        for u in range(8):
            u_raw = uniforms_raw[u]
            u_ch = [Cat(u_raw[0:8], Constant(0, 8)),
                    Cat(u_raw[8:16], Constant(0, 8)),
                    Cat(u_raw[16:24], Constant(0, 8)),
                    Cat(u_raw[24:32], Constant(0, 8))]
            for c in range(4):
                self.comb += [
                    stage0.uniforms[u][c].eq(u_ch[c]),
                    stage1.uniforms[u][c].eq(u_ch[c]),
                ]

        # Stage 0 inputs
        src_raw_lb = Mux(fetch_odd, self.src_lb_o_r.dat_r, self.src_lb_e_r.dat_r)
        src_pix_ch = [
            Cat(src_raw_lb[0:8], Constant(0, 8)),
            Cat(src_raw_lb[8:16], Constant(0, 8)),
            Cat(src_raw_lb[16:24], Constant(0, 8)),
            Cat(src_raw_lb[24:32], Constant(0, 8)),
        ]
        dst_raw_lb = self.dst_lb_r.dat_r
        dst_pix_ch = [
            Cat(dst_raw_lb[0:8], Constant(0, 8)),
            Cat(dst_raw_lb[8:16], Constant(0, 8)),
            Cat(dst_raw_lb[16:24], Constant(0, 8)),
            Cat(dst_raw_lb[24:32], Constant(0, 8)),
        ]
        fg_ch = [
            Cat(fg[0:8], Constant(0, 8)),
            Cat(fg[8:16], Constant(0, 8)),
            Cat(fg[16:24], Constant(0, 8)),
            Cat(fg[24:32], Constant(0, 8)),
        ]
        bg_ch = [
            Cat(bg[0:8], Constant(0, 8)),
            Cat(bg[8:16], Constant(0, 8)),
            Cat(bg[16:24], Constant(0, 8)),
            Cat(bg[24:32], Constant(0, 8)),
        ]
        for c in range(4):
            self.comb += [
                stage0.src_pix[c].eq(Mux(uses_src, src_pix_ch[c], fg_ch[c])),
                stage0.dst_pix[c].eq(Mux(uses_dst, dst_pix_ch[c], bg_ch[c])),
                stage0.fg_pix[c].eq(fg_ch[c]),
                stage0.bg_pix[c].eq(bg_ch[c]),
                stage0.r_in[0][c].eq(stage0.src_pix[c]),
                stage0.r_in[1][c].eq(stage0.dst_pix[c]),
            ]
        self.comb += [
            stage0.r_in[2][0].eq(fetch_x[0:16]),
            stage0.r_in[2][1].eq(row[0:16]),
            stage0.r_in[2][2].eq(0),
            stage0.r_in[2][3].eq(0),
            stage0.r_in[3][0].eq(0),
            stage0.r_in[3][1].eq(0),
            stage0.r_in[3][2].eq(0),
            stage0.r_in[3][3].eq(0),
            stage0.p_in.eq(0),
            stage0.discard_in.eq(0),
        ]

        # Stage 0 to Stage 1 direct pipeline connection
        for c in range(4):
            for r in range(4):
                self.comb += stage1.r_in[r][c].eq(stage0.r_out[r][c])
            self.comb += [
                stage1.src_pix[c].eq(stage0.src_out[c]),
                stage1.dst_pix[c].eq(stage0.dst_out[c]),
                stage1.fg_pix[c].eq(stage0.fg_out[c]),
                stage1.bg_pix[c].eq(stage0.bg_out[c]),
            ]
        self.comb += [
            stage1.p_in.eq(stage0.p_out),
            stage1.discard_in.eq(stage0.discard_out),
        ]

        # Writeback packer signals
        wb_idx = Signal(11)
        wbuf = Signal(256, reset_less=True)
        wmask = Signal(32, reset_less=True)
        cur_wb_blk = Signal(29, reset_less=True)
        byte_slot = Signal(6, reset_less=True)
        byte_full = Signal()
        self.comb += byte_full.eq(
            Mux(dst_bpp_shift == 2, byte_slot == 28, byte_slot == 30)
        )

        self.fsm = fsm = FSM(reset_state='IDLE')
        self.comb += [
            load_out.eq(fsm.ongoing('PREP') | fsm.ongoing('C_WPREP') |
                        fsm.ongoing('B_PREP') | fsm.ongoing('T_WB_PREP')),
            prep_data.eq(Mux(fsm.ongoing('PREP'), wr_pattern,
                         Mux(fsm.ongoing('C_WPREP'), copy_data,
                         Mux(fsm.ongoing('T_WB_PREP'), wbuf, blit_shifted)))),
            is_rd_state.eq(fsm.ongoing('C_RSEND') | fsm.ongoing('B_RD_SEND') |
                           fsm.ongoing('T_SRC_RREQ') | fsm.ongoing('T_DST_RREQ')),
            self.ui.cmd.eq(Mux(is_rd_state, 1, 0)),
            self.ui.burst.eq(0),
            self.ui.addr.eq(
                Mux(fsm.ongoing('C_RSEND') | fsm.ongoing('B_RD_SEND'), rd_addr,
                Mux(fsm.ongoing('T_SRC_RREQ'), t_src_cur_blk,
                Mux(fsm.ongoing('T_DST_RREQ'), t_dst_cur_blk, out_addr)))),
            self.ui.wr_data.eq(out_data),
            self.ui.wr_data_mask.eq(~out_mask),
            self.ui.wr_data_end.eq(0),
        ]
        self.sync += If(load_out,
            If(fsm.ongoing('T_WB_PREP'),
                out_addr.eq(cur_wb_blk),
                out_data.eq(wbuf),
                out_mask.eq(wmask),
            ).Else(
                out_addr.eq(beat_byte[2:31] & ~Constant(7, 29)),
                out_data.eq(prep_data),
                out_mask.eq(beat_mask),
            )
        )
        rd_base = Signal(32)
        self.comb += rd_base.eq(Mux(fsm.ongoing('B_RD_PREP'), src_beat_base,
                                    src_beat_base + (beat << 5)))
        self.sync += If(fsm.ongoing('C_RPREP') | fsm.ongoing('B_RD_PREP'),
                        rd_addr.eq(rd_base[2:31] & ~Constant(7, 29)))
        self.comb += self.busy.eq(~fsm.ongoing('IDLE'))
        fsm.act('IDLE',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(start, NextValue(err, 0), NextValue(elapsed, 0),
                   NextValue(row, 0), NextValue(rows_left, height),
                   NextValue(last_row, height == 1), NextValue(beat, 0),
                   NextValue(row_start, dst_addr),
                   NextValue(src_row_start, src_addr),
                   NextState('INIT')))

        src_span = Signal(32, reset_less=True)
        dst_span = Signal(32, reset_less=True)
        src_step_total = Signal(32, reset_less=True)
        dst_step_total = Signal(32, reset_less=True)
        row_start_down = Signal(32, reset_less=True)
        src_row_start_down = Signal(32, reset_less=True)
        fsm.act('INIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(limit, ddr_size - row_bytes),
                NextValue(src_limit, Mux(op == OP_IMAGE_BLIT,
                                         ddr_size - blit_wb, ddr_size - src_row_bytes)),
                NextValue(src_step_total, (height - 1) * src_stride),
                NextValue(dst_step_total, (height - 1) * dst_stride),
                NextValue(src_span, (height - 1) * src_stride + src_row_bytes),
                NextValue(dst_span, (height - 1) * dst_stride + row_bytes),
                NextState('INIT2'))

        cmp_dst_stride = Signal(reset_less=True)
        cmp_src_stride = Signal(reset_less=True)
        cmp_blit_wb = Signal(reset_less=True)
        cmp_right_overlap = Signal(reset_less=True)
        cmp_stride_equal = Signal(reset_less=True)
        cmp_bpp_equal = Signal(reset_less=True)
        cmp_width_le_1024 = Signal(reset_less=True)
        cmp_addr_lt = Signal(reset_less=True)
        overlap_src_lt_dst = Signal(reset_less=True)
        overlap_dst_lt_src = Signal(reset_less=True)
        unequal_stride_overlap = Signal(reset_less=True)
        bad_shader_overlap = Signal(reset_less=True)
        mem_overlap = Signal(reset_less=True)
        dst_align_bad = Signal(reset_less=True)
        src_align_bad = Signal(reset_less=True)
        src_end = Signal(33, reset_less=True)
        dst_end = Signal(33, reset_less=True)
        cur_mem_overlap = Signal()
        dir_down_step = Signal()
        overlap_safe = Signal()
        self.comb += [
            cur_mem_overlap.eq(overlap_src_lt_dst & overlap_dst_lt_src),
            overlap_safe.eq((~scale_en) & (~bilinear_en) &
                            cmp_bpp_equal & cmp_stride_equal & cmp_width_le_1024),
            dir_down_step.eq(Mux(op == OP_COPY_AREA,
                                 cmp_stride_equal & cmp_addr_lt,
                             Mux(op == OP_SHADER,
                                 uses_src & cur_mem_overlap &
                                     overlap_safe & cmp_addr_lt,
                                 0))),
        ]
        fsm.act('INIT2',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(src_end, src_addr + src_span),
                NextValue(dst_end, dst_addr + dst_span),
                NextValue(cmp_dst_stride, dst_stride < row_bytes),
                NextValue(cmp_src_stride, src_stride < src_row_bytes),
                NextValue(cmp_blit_wb, blit_wb < ((width + 7) >> 3)),
                NextValue(cmp_right_overlap,
                          (src_addr < dst_addr) & ((dst_addr - src_addr) < row_bytes)),
                NextValue(cmp_stride_equal, src_stride == dst_stride),
                NextValue(cmp_bpp_equal, src_bpp_shift == dst_bpp_shift),
                NextValue(cmp_width_le_1024, width <= 1024),
                NextValue(cmp_addr_lt, src_addr < dst_addr),
                NextValue(dst_align_bad,
                          Mux(dst_bpp_shift == 2,
                              (dst_addr[0:2] != 0) | (dst_stride[0:2] != 0),
                              Mux(dst_bpp_shift == 1,
                                  (dst_addr[0] != 0) | (dst_stride[0] != 0), 0))),
                NextValue(src_align_bad,
                          Mux(src_bpp_shift == 2,
                              (src_addr[0:2] != 0) | (src_stride[0:2] != 0),
                              Mux(src_bpp_shift == 1,
                                  (src_addr[0] != 0) | (src_stride[0] != 0), 0))),
                NextValue(row_start_down, dst_addr + dst_step_total),
                NextValue(src_row_start_down, src_addr + src_step_total),
                NextValue(rb_hi5, row_bytes >> 5),
                NextValue(rb_lo5, row_bytes[0:5]),
                NextState('INIT2B'))

        fsm.act('INIT2B',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(overlap_src_lt_dst, src_addr < dst_end),
                NextValue(overlap_dst_lt_src, dst_addr < src_end),
                NextState('INIT2C'))

        fsm.act('INIT2C',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(mem_overlap, cur_mem_overlap),
                NextValue(unequal_stride_overlap,
                          (~cmp_stride_equal) & cur_mem_overlap),
                NextValue(bad_shader_overlap,
                          uses_src & cur_mem_overlap & (~overlap_safe)),
                NextValue(dir_down, dir_down_step),
                NextState('INIT3'))

        bad_blit_dst = Mux(fmt == FMT_XRGB8888,
                           (dst_addr[0:2] != 0) | (dst_stride[0:2] != 0),
                           (dst_addr[0] != 0) | (dst_stride[0] != 0))
        legacy_fmt_bad = (op < OP_SHADER) & ((fmt == FMT_RGB888) | (fmt > FMT_XRGB1555))
        dst_fmt_bad = (dst_format == FMT_RGB888) | (dst_format >= FMT_INDEX8) | \
                      legacy_fmt_bad
        src_fmt_bad = (src_format == FMT_RGB888) | (src_format >= FMT_INDEX8)
        cfg_bad = (op == OP_SCALE_BLIT) | scale_en | bilinear_en | (last_stage > 1)
        dim_bad = (width == 0) | (height == 0) | \
                  ((op >= OP_SHADER) & ((width > 16384) | (height > 16384)))

        static_bad = (
            (op > OP_SCALE_BLIT) |
            dim_bad |
            cmp_dst_stride |
            dst_fmt_bad |
            ((op == OP_COPY_AREA) & (
                cmp_src_stride |
                (src_addr[0:5] != dst_addr[0:5]) |
                (src_stride[0:5] != dst_stride[0:5]) |
                cmp_right_overlap |
                unequal_stride_overlap
            )) |
            ((op == OP_IMAGE_BLIT) & (
                (fmt == FMT_RGB888) |
                cmp_blit_wb | (blit_wb > 65535) |
                bad_blit_dst
            )) |
            ((op == OP_SHADER) & (
                dst_align_bad |
                cfg_bad |
                (uses_src & (src_fmt_bad | cmp_src_stride | src_align_bad)) |
                bad_shader_overlap
            ))
        )
        fsm.act('INIT3',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(delta_dst, Mux(dir_down, -dst_stride, dst_stride)),
                NextValue(delta_src, Mux(op == OP_COPY_AREA,
                                         Mux(dir_down, -src_stride, src_stride),
                                     Mux(op == OP_IMAGE_BLIT, blit_wb,
                                         Mux(dir_down, -src_stride, src_stride)))),
                If(static_bad,
                   NextValue(err, Mux(op > OP_SCALE_BLIT, ERR_UNSUPPORTED, ERR_PARAM)),
                   NextState('FINISH')
                ).Elif(dir_down,
                   NextState('C_DOWN_INIT')
                ).Else(
                   NextState('ROW_SETUP')))
        fsm.act('C_DOWN_INIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(row_start, row_start_down),
                NextValue(src_row_start, src_row_start_down),
                NextValue(row, height - 1),
                NextState('ROW_SETUP'))

        pascal = [NextValue(beats, beats_w),
                  NextValue(beats_left, beats_w),
                  NextValue(first_beat, 1),
                  NextValue(beat_base, row_start & ~Constant(31, 32)),
                  NextValue(beat_byte, row_start & ~Constant(31, 32)),
                  NextValue(offset, row_start[0:5]),
                  NextValue(first_mask, Cat(*first_bits)),
                  NextValue(last_mask, Cat(*last_bits))]

        bad_row_addr = Signal()
        bad_row_addr = Signal()
        self.comb += bad_row_addr.eq(
            (row_start > limit) | ((op != OP_FILL_RECT) & (src_row_start > src_limit)))
        fsm.act('ROW_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(bad_row_addr, NextValue(err, ERR_ADDR), NextState('FINISH'))
                .Else(NextState('ROW_PASCAL')))
        fsm.act('ROW_PASCAL',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                *pascal,
                If(op == OP_COPY_AREA,
                   NextValue(src_beat_base, src_row_start & ~Constant(31, 32)),
                   NextState('C_RPREP'))
                .Elif(op == OP_IMAGE_BLIT,
                      NextValue(src_beat_base, src_row_start & ~Constant(31, 32)),
                      NextValue(src_byte_off, src_row_start[0:5]),
                      NextValue(bytes_in_beat, 32 - src_row_start[0:5]),
                      NextValue(bytes_in_beat_zero, 0),
                      NextValue(src_beat_loaded, 0),
                      NextValue(src_byte_pending, 0),
                      NextValue(src_bytes_left, blit_wb),
                      NextValue(src_bytes_left_zero, blit_wb == 0),
                      NextValue(bit_buf, 0),
                      NextValue(bit_cnt,
                                Mux(fmt == FMT_XRGB8888,
                                    row_start[0:5] >> 2, row_start[0:5] >> 1)),
                      NextState('B_FETCH'))
                .Elif(op == OP_SHADER,
                      NextValue(tile_x, 0),
                      NextState('T_SETUP'))
                .Else(NextState('PREP')))
        down = dir_down

        fsm.act('ROW_INC',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(row, Mux(down, row - 1, row + 1)),
                NextValue(rows_left, rows_left - 1),
                NextValue(last_row, rows_left == 2),
                NextValue(beat, 0),
                NextValue(row_start, nxt_row_start),
                NextValue(src_row_start, nxt_src_row_start), NextState('ROW_SETUP'))

        # Existing OP fast paths
        fsm.act('PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('SEND'))
        fsm.act('SEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1),
                self.ui.wr_data_end.eq(1),
                If(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    If(beats_left == 1,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1),
                             NextValue(beats_left, beats_left - 1),
                             NextValue(first_beat, 0),
                             NextValue(beat_byte, beat_byte + 32), NextState('PREP')),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))

        fsm.act('C_RPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('C_RSEND'))
        fsm.act('C_RSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(0),
                If(self.ui.cmd_ready,
                    NextValue(elapsed, 0), NextState('C_RWAIT'),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('C_RWAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(self.ui.rd_data_valid,
                    NextValue(copy_data, self.ui.rd_data), NextState('C_WPREP'),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('C_WPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('C_WSEND'))
        fsm.act('C_WSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1),
                self.ui.wr_data_end.eq(1),
                If(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    If(beats_left == 1,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1),
                             NextValue(beats_left, beats_left - 1),
                             NextValue(beat_byte, beat_byte + 32),
                             NextValue(first_beat, 0),
                             NextState('C_RPREP')),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))

        fsm.act('B_FETCH',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(bit_cnt >= blit_pix_per_beat, NextState('B_EXP'))
                .Elif(src_bytes_left_zero, NextState('B_EXP'))
                .Elif(bytes_in_beat_zero,
                      NextValue(src_beat_base, src_beat_base + 32),
                      NextValue(src_byte_off, 0), NextValue(bytes_in_beat, 32),
                      NextValue(bytes_in_beat_zero, 0),
                      NextValue(src_beat_loaded, 0),
                      NextState('B_FETCH'))
                .Elif(~src_beat_loaded, NextState('B_RD_PREP'))
                .Elif(~src_byte_pending,
                      NextValue(src_byte_q, src_byte),
                      NextValue(src_byte_pending, 1), NextState('B_FETCH'))
                .Else(NextValue(bit_buf,
                                bit_buf |
                                    (Cat(Constant(0, 16), src_byte_q) >> bit_cnt)),
                      NextValue(bit_cnt, bit_cnt + 8),
                      NextValue(src_byte_off, src_byte_off + 1),
                      NextValue(bytes_in_beat, bytes_in_beat - 1),
                      NextValue(bytes_in_beat_zero, bytes_in_beat == 1),
                      NextValue(beat_shift, beat_shift >> 8),
                      NextValue(src_bytes_left, src_bytes_left - 1),
                      NextValue(src_bytes_left_zero, src_bytes_left == 1),
                      NextValue(src_byte_pending, 0),
                      NextState('B_FETCH')))
        fsm.act('B_RD_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('B_RD_SEND'))
        fsm.act('B_RD_SEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(0),
                If(self.ui.cmd_ready,
                    NextValue(elapsed, 0), NextState('B_RD_WAIT'),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('B_RD_WAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(self.ui.rd_data_valid,
                    NextValue(beat_shift, self.ui.rd_data >>
                              Cat(Constant(0, 3), src_byte_off[0:5])),
                    NextValue(src_beat_loaded, 1), NextState('B_FETCH'),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('B_EXP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(blit_exp_reg, blit_expanded), NextState('B_PREP'))
        fsm.act('B_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0), NextState('B_WSEND'))
        fsm.act('B_WSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1),
                self.ui.wr_data_end.eq(1),
                If(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    NextValue(bit_buf,
                              Mux(fmt == FMT_XRGB8888, bit_buf << 8, bit_buf << 16)),
                    NextValue(bit_cnt,
                              Mux(bit_cnt > blit_pix_per_beat,
                                  bit_cnt - blit_pix_per_beat, 0)),
                    If(beats_left == 1,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1),
                             NextValue(beats_left, beats_left - 1),
                             NextValue(first_beat, 0),
                             NextValue(beat_byte, beat_byte + 32),
                             NextState('B_FETCH')),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Else(NextValue(elapsed, elapsed + 1)))

        # OP_SHADER Tile FSM
        cur_w_rem = Signal(16)
        calc_t_w = Signal(16)
        self.comb += [
            cur_w_rem.eq(width - tile_x),
            calc_t_w.eq(Mux(cur_w_rem > 1024, 1024, cur_w_rem)),
        ]
        cur_src_byte = Signal(32)
        cur_dst_byte = Signal(32)
        cur_src_end = Signal(32)
        cur_dst_end = Signal(32)
        self.comb += [
            cur_src_byte.eq(src_row_start + (tile_x << src_bpp_shift)),
            cur_dst_byte.eq(row_start + (tile_x << dst_bpp_shift)),
            cur_src_end.eq(cur_src_byte + (calc_t_w << src_bpp_shift) - 1),
            cur_dst_end.eq(cur_dst_byte + (calc_t_w << dst_bpp_shift) - 1),
        ]

        fsm.act('T_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(tile_w, calc_t_w[0:11]),
                NextValue(tile_last, calc_t_w[0:11] - 1),
                NextValue(tile_is_last_in_row, (tile_x + calc_t_w >= width)),
                NextValue(t_src_cur_blk, cur_src_byte[2:31] & ~Constant(7, 29)),
                NextValue(t_src_beats_left,
                          (((cur_src_end[2:31] & ~Constant(7, 29)) -
                            (cur_src_byte[2:31] & ~Constant(7, 29))) >> 3) + 1),
                NextValue(t_src_off, cur_src_byte[0:5]),
                NextValue(t_dst_base_blk, cur_dst_byte[2:31] & ~Constant(7, 29)),
                NextValue(t_dst_cur_blk, cur_dst_byte[2:31] & ~Constant(7, 29)),
                NextValue(t_dst_beats_left,
                          (((cur_dst_end[2:31] & ~Constant(7, 29)) -
                            (cur_dst_byte[2:31] & ~Constant(7, 29))) >> 3) + 1),
                NextValue(t_dst_off, cur_dst_byte[0:5]),
                NextValue(t_src_is_first, 1),
                NextValue(t_dst_is_first, 1),
                NextValue(lb_wr_ptr, 0),
                If(uses_src,
                    NextState('T_SRC_RREQ')
                ).Elif(uses_dst,
                    NextState('T_DST_RREQ')
                ).Else(
                    NextState('T_COMP_START')
                ))

        fsm.act('T_SRC_RREQ',
                self.ui.cmd.eq(1), self.ui.cmd_en.eq(1), self.ui.addr.eq(t_src_cur_blk),
                If(self.ui.cmd_ready,
                    NextValue(elapsed, 0),
                    NextValue(t_src_cur_blk, t_src_cur_blk + 8),
                    NextValue(t_src_beats_left, t_src_beats_left - 1),
                    NextState('T_SRC_RWAIT')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Else(NextValue(elapsed, elapsed + 1)))

        fsm.act('T_SRC_RWAIT',
                self.ui.cmd.eq(0), self.ui.cmd_en.eq(0),
                If(self.ui.rd_data_valid,
                    NextValue(elapsed, 0),
                    NextValue(t_beat_buf, self.ui.rd_data),
                    NextValue(sub_idx,
                              Mux(t_src_is_first, t_src_off >> src_bpp_shift, 0)),
                    NextValue(t_src_is_first, 0),
                    NextState('T_SRC_UNPACK')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Else(NextValue(elapsed, elapsed + 1)))

        # Unpack logic for SRC
        src_p32 = Signal(32)
        src_p16 = Signal(16)
        src_argb = Signal(32)
        src_max_sub = Signal(6)
        self.comb += [
            src_p32.eq(Array([t_beat_buf[i*32:(i+1)*32]
                           for i in range(8)])[sub_idx[0:3]]),
            src_p16.eq(Array([t_beat_buf[i*16:(i+1)*16]
                           for i in range(16)])[sub_idx[0:4]]),
            src_max_sub.eq(Mux(src_bpp_shift == 2, 8, Mux(src_bpp_shift == 1, 16, 32))),
            If((src_format == FMT_XRGB8888) | (src_format == FMT_ARGB8888),
                src_argb.eq(Cat(src_p32[0:24],
                                Mux(src_format == FMT_XRGB8888,
                                    Constant(0xFF, 8), src_p32[24:32])))
            ).Elif(src_format == FMT_RGB565,
                src_argb.eq(Cat(
                    Cat(src_p16[2:5], src_p16[0:5]),
                    Cat(src_p16[9:11], src_p16[5:11]),
                    Cat(src_p16[13:16], src_p16[11:16]),
                    Constant(0xFF, 8)
                ))
            ).Elif(src_format == FMT_XRGB1555,
                src_argb.eq(Cat(
                    Cat(src_p16[2:5], src_p16[0:5]),
                    Cat(src_p16[7:10], src_p16[5:10]),
                    Cat(src_p16[12:15], src_p16[10:15]),
                    Constant(0xFF, 8)
                ))
            ).Elif(src_format == FMT_ARGB1555,
                src_argb.eq(Cat(
                    Cat(src_p16[2:5], src_p16[0:5]),
                    Cat(src_p16[7:10], src_p16[5:10]),
                    Cat(src_p16[12:15], src_p16[10:15]),
                    Mux(src_p16[15], Constant(0xFF, 8), Constant(0, 8))
                ))
            ).Else(
                src_argb.eq(Cat(src_p32[0:24], Constant(0xFF, 8)))
            )
        ]

        fsm.act('T_SRC_UNPACK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(~lb_wr_ptr[0],
                    self.src_lb_e_w.we.eq(1),
                    self.src_lb_e_w.adr.eq(lb_wr_ptr >> 1),
                    self.src_lb_e_w.dat_w.eq(src_argb),
                ).Else(
                    self.src_lb_o_w.we.eq(1),
                    self.src_lb_o_w.adr.eq(lb_wr_ptr >> 1),
                    self.src_lb_o_w.dat_w.eq(src_argb),
                ),
                NextValue(lb_wr_ptr, lb_wr_ptr + 1),
                NextValue(sub_idx, sub_idx + 1),
                If(lb_wr_ptr == tile_last,
                    NextValue(lb_wr_ptr, 0),
                    If(uses_dst, NextState('T_DST_RREQ'))
                    .Else(NextState('T_COMP_START'))
                ).Elif(sub_idx == src_max_sub - 1,
                    NextState('T_SRC_RREQ')
                ))

        # DST prefetch
        fsm.act('T_DST_RREQ',
                self.ui.cmd.eq(1), self.ui.cmd_en.eq(1), self.ui.addr.eq(t_dst_cur_blk),
                If(self.ui.cmd_ready,
                    NextValue(elapsed, 0),
                    NextValue(t_dst_cur_blk, t_dst_cur_blk + 8),
                    NextValue(t_dst_beats_left, t_dst_beats_left - 1),
                    NextState('T_DST_RWAIT')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Else(NextValue(elapsed, elapsed + 1)))

        fsm.act('T_DST_RWAIT',
                self.ui.cmd.eq(0), self.ui.cmd_en.eq(0),
                If(self.ui.rd_data_valid,
                    NextValue(elapsed, 0),
                    NextValue(t_beat_buf, self.ui.rd_data),
                    NextValue(sub_idx,
                              Mux(t_dst_is_first, t_dst_off >> dst_bpp_shift, 0)),
                    NextValue(t_dst_is_first, 0),
                    NextState('T_DST_UNPACK')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Else(NextValue(elapsed, elapsed + 1)))

        dst_p32 = Signal(32)
        dst_p16 = Signal(16)
        dst_argb = Signal(32)
        dst_max_sub = Signal(6)
        self.comb += [
            dst_p32.eq(Array([t_beat_buf[i*32:(i+1)*32]
                           for i in range(8)])[sub_idx[0:3]]),
            dst_p16.eq(Array([t_beat_buf[i*16:(i+1)*16]
                           for i in range(16)])[sub_idx[0:4]]),
            dst_max_sub.eq(Mux(dst_bpp_shift == 2, 8, Mux(dst_bpp_shift == 1, 16, 32))),
            If((dst_format == FMT_XRGB8888) | (dst_format == FMT_ARGB8888),
                dst_argb.eq(Cat(dst_p32[0:24],
                                Mux(dst_format == FMT_XRGB8888,
                                    Constant(0xFF, 8), dst_p32[24:32])))
            ).Elif(dst_format == FMT_RGB565,
                dst_argb.eq(Cat(
                    Cat(dst_p16[2:5], dst_p16[0:5]),
                    Cat(dst_p16[9:11], dst_p16[5:11]),
                    Cat(dst_p16[13:16], dst_p16[11:16]),
                    Constant(0xFF, 8)
                ))
            ).Elif(dst_format == FMT_XRGB1555,
                dst_argb.eq(Cat(
                    Cat(dst_p16[2:5], dst_p16[0:5]),
                    Cat(dst_p16[7:10], dst_p16[5:10]),
                    Cat(dst_p16[12:15], dst_p16[10:15]),
                    Constant(0xFF, 8)
                ))
            ).Elif(dst_format == FMT_ARGB1555,
                dst_argb.eq(Cat(
                    Cat(dst_p16[2:5], dst_p16[0:5]),
                    Cat(dst_p16[7:10], dst_p16[5:10]),
                    Cat(dst_p16[12:15], dst_p16[10:15]),
                    Mux(dst_p16[15], Constant(0xFF, 8), Constant(0, 8))
                ))
            ).Else(
                dst_argb.eq(Cat(dst_p32[0:24], Constant(0xFF, 8)))
            )
        ]

        fsm.act('T_DST_UNPACK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                self.dst_lb_w.we.eq(1),
                self.dst_lb_w.adr.eq(lb_wr_ptr),
                self.dst_lb_w.dat_w.eq(dst_argb),
                NextValue(lb_wr_ptr, lb_wr_ptr + 1),
                NextValue(sub_idx, sub_idx + 1),
                If(lb_wr_ptr == tile_last,
                    NextValue(lb_wr_ptr, 0),
                    NextState('T_COMP_START')
                ).Elif(sub_idx == dst_max_sub - 1,
                    NextState('T_DST_RREQ')
                ))

        # Streaming compute
        comp_pipe_done = Signal()
        self.comb += [
            self.src_lb_e_r.adr.eq(comp_idx >> 1),
            self.src_lb_o_r.adr.eq(comp_idx >> 1),
            self.dst_lb_r.adr.eq(comp_idx),
        ]

        fsm.act('T_COMP_START',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(comp_idx, 0),
                NextValue(comp_out_idx, 0),
                NextValue(comp_pipe_vld, 0),
                NextState('T_COMP_STREAM'))

        fsm.act('T_COMP_STREAM',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(comp_idx < tile_w,
                    NextValue(comp_idx, comp_idx + 1),
                    NextValue(fetch_x, tile_x + comp_idx),
                    NextValue(fetch_odd, comp_idx[0]),
                    NextValue(comp_pipe_vld,
                              Cat(Constant(1, 1), comp_pipe_vld[0:6])),
                ).Else(
                    NextValue(comp_pipe_vld,
                              Cat(Constant(0, 1), comp_pipe_vld[0:6])),
                ),

                If(comp_pipe_vld[6],
                    self.out_lb_w.we.eq(1),
                    self.out_lb_w.adr.eq(comp_out_idx),
                    self.out_lb_w.dat_w.eq(Cat(
                        stage1.r_out[0][0][0:8], stage1.r_out[0][1][0:8],
                        stage1.r_out[0][2][0:8], stage1.r_out[0][3][0:8],
                        stage1.discard_out
                    )),
                    NextValue(comp_out_idx, comp_out_idx + 1),
                    If(comp_out_idx == tile_last,
                        NextState('T_WB_SETUP')
                    )
                ))

        # Writeback phase
        bayer_matrix = [
            0, 8, 2, 10,
            12, 4, 14, 6,
            3, 11, 1, 9,
            15, 7, 13, 5,
        ]
        wb_x = Signal(16)
        wb_thresh = Signal(4)
        self.comb += [
            wb_x.eq(tile_x + wb_idx),
            wb_thresh.eq(
                Array([Constant(v, 4) for v in bayer_matrix])[
                    Cat(wb_x[0:2], row[0:2])]),
            self.out_lb_r.adr.eq(wb_idx),
        ]

        fsm.act('T_WB_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(wb_idx, 0),
                NextValue(tile_wb_done, 0),
                NextValue(wmask, 0),
                NextValue(byte_slot, t_dst_off),
                NextValue(cur_wb_blk, t_dst_base_blk),
                NextState('T_WB_READ'))

        fsm.act('T_WB_READ',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextState('T_WB_PACK'))

        # Pixel dither and pack
        out_b = Signal(8)
        out_g = Signal(8)
        out_r = Signal(8)
        out_a = Signal(8)
        out_disc = Signal()
        self.comb += [
            out_b.eq(self.out_lb_r.dat_r[0:8]),
            out_g.eq(self.out_lb_r.dat_r[8:16]),
            out_r.eq(self.out_lb_r.dat_r[16:24]),
            out_a.eq(self.out_lb_r.dat_r[24:32]),
            out_disc.eq(self.out_lb_r.dat_r[32]),
        ]

        dith_r = Signal(8)
        dith_g = Signal(8)
        dith_b = Signal(8)
        self.comb += [
            If(dither_en,
                dith_r.eq(Mux(out_r + (wb_thresh >> 1) > 255,
                          255, out_r + (wb_thresh >> 1))),
                dith_g.eq(Mux(out_g + (wb_thresh >> 2) > 255,
                          255, out_g + (wb_thresh >> 2))),
                dith_b.eq(Mux(out_b + (wb_thresh >> 1) > 255,
                          255, out_b + (wb_thresh >> 1))),
            ).Else(
                dith_r.eq(out_r),
                dith_g.eq(out_g),
                dith_b.eq(out_b),
            )
        ]

        packed_pix = Signal(32)
        pix_bytes = Signal(4)
        self.comb += [
            If(dst_format == FMT_RGB565,
                packed_pix.eq(Cat(dith_b[3:8], dith_g[2:8], dith_r[3:8])),
                pix_bytes.eq(2)
            ).Elif(dst_format == FMT_XRGB1555,
                packed_pix.eq(
                    Cat(dith_b[3:8], dith_g[3:8], dith_r[3:8], Constant(1, 1))),
                pix_bytes.eq(2)
            ).Elif(dst_format == FMT_ARGB1555,
                packed_pix.eq(Cat(dith_b[3:8], dith_g[3:8], dith_r[3:8], out_a >= 128)),
                pix_bytes.eq(2)
            ).Elif(dst_format == FMT_XRGB8888,
                packed_pix.eq(Cat(out_b, out_g, out_r, Constant(0xFF, 8))),
                pix_bytes.eq(4)
            ).Elif(dst_format == FMT_ARGB8888,
                packed_pix.eq(Cat(out_b, out_g, out_r, out_a)),
                pix_bytes.eq(4)
            ).Else(
                packed_pix.eq(Cat(out_b, out_g, out_r, Constant(0xFF, 8))),
                pix_bytes.eq(4)
            )
        ]

        wb_cases = {}
        for s in range(0, 32, 2):
            if s % 4 == 0:
                wb_cases[s] = [
                    If(dst_bpp_shift == 2,
                        NextValue(wbuf[s*8:(s+4)*8], packed_pix[0:32]),
                        If(~out_disc, NextValue(wmask[s:s+4], 0xF))
                    ).Else(
                        NextValue(wbuf[s*8:(s+2)*8], packed_pix[0:16]),
                        If(~out_disc, NextValue(wmask[s:s+2], 0x3))
                    )
                ]
            else:
                wb_cases[s] = [
                    NextValue(wbuf[s*8:(s+2)*8], packed_pix[0:16]),
                    If(~out_disc, NextValue(wmask[s:s+2], 0x3))
                ]

        fsm.act('T_WB_PACK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                Case(byte_slot, wb_cases),
                NextValue(byte_slot, byte_slot + pix_bytes),
                NextValue(wb_idx, wb_idx + 1),
                NextValue(tile_wb_done, wb_idx == tile_last),
                If(byte_full | (wb_idx == tile_last),
                    NextState('T_WB_PREP')
                ).Else(
                    NextState('T_WB_READ')
                ))

        fsm.act('T_WB_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextState('T_WB_SEND'))

        fsm.act('T_WB_SEND',
                If(wmask == 0,
                    NextValue(cur_wb_blk, cur_wb_blk + 8),
                    NextValue(wmask, 0),
                    NextValue(byte_slot, 0),
                    If(tile_wb_done,
                        NextState('T_TILE_NEXT')
                    ).Else(
                        NextState('T_WB_READ')
                    )
                ).Else(
                    self.ui.cmd_en.eq(1),
                    self.ui.wr_data_en.eq(1),
                    self.ui.wr_data_end.eq(1),
                    If(self.ui.cmd_ready & self.ui.wr_data_rdy,
                        NextValue(elapsed, 0),
                        NextValue(cur_wb_blk, cur_wb_blk + 8),
                        NextValue(wmask, 0),
                        NextValue(byte_slot, 0),
                        If(tile_wb_done,
                            NextState('T_TILE_NEXT')
                        ).Else(
                            NextState('T_WB_READ')
                        )
                    ).Elif(elapsed_timeout,
                        NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                    ).Else(NextValue(elapsed, elapsed + 1))
                ))

        fsm.act('T_TILE_NEXT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(tile_x, tile_x + tile_w),
                If(~tile_is_last_in_row,
                    NextState('T_SETUP')
                ).Else(
                    If(last_row, NextState('FINISH'))
                    .Else(NextState('ROW_INC'))
                ))

        fsm.act('FINISH',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(self.done_err, err), NextValue(self.ack, ~self.ack),
                NextState('IDLE'))


class Gfx2D(LiteXModule):
    def __init__(self, ddr_size=1 << 30):
        self.off = Signal(12)
        self.write = Signal()
        self.wdata = Signal(32)
        self.commit = Signal()
        self.selected = Signal()
        self.rdata = Signal(32)
        self.ok = Signal()
        self.irq = Signal()
        self.ui = DDR3UI()

        reg = amap.GFX2D_REGS
        desc = {}
        for name in DESC_NAMES:
            desc[name] = Signal(32)
            setattr(self, name.lower(), desc[name])
        self.busy = Signal()
        self.done = Signal()
        self.error = Signal()
        self.last_error = Signal(32)
        self.commands = Signal(32)
        self.errors = Signal(32)
        self.irq_pending = Signal()
        self.irq_enable = Signal()

        # Pal and LUT RAMs
        self.pal_ram = pal_ram = Memory(32, 256, name="gfx2d_pal_ram")
        self.lut_ram = lut_ram = Memory(32, 256, name="gfx2d_lut_ram")
        self.pal_w = pal_w = pal_ram.get_port(write_capable=True, clock_domain="sys")
        self.lut_w = lut_w = lut_ram.get_port(write_capable=True, clock_domain="sys")
        self.pal_r = pal_r = pal_ram.get_port(clock_domain="ddr")
        self.lut_r = lut_r = lut_ram.get_port(clock_domain="ddr")
        self.specials += pal_ram, pal_w, pal_r, lut_ram, lut_w, lut_r

        self.engine = engine = ClockDomainsRenamer('ddr')(
            Gfx2DEngine(ddr_size=ddr_size, pal_port=pal_r, lut_port=lut_r)
        )
        for i, name in enumerate(DESC_NAMES):
            self.comb += engine.desc[i].eq(desc[name])
        self.comb += [
            engine.ui.cmd_ready.eq(self.ui.cmd_ready),
            engine.ui.wr_data_rdy.eq(self.ui.wr_data_rdy),
            engine.ui.rd_data.eq(self.ui.rd_data),
            engine.ui.rd_data_valid.eq(self.ui.rd_data_valid),
            self.ui.cmd.eq(engine.ui.cmd), self.ui.cmd_en.eq(engine.ui.cmd_en),
            self.ui.addr.eq(engine.ui.addr), self.ui.burst.eq(engine.ui.burst),
            self.ui.wr_data.eq(engine.ui.wr_data),
            self.ui.wr_data_en.eq(engine.ui.wr_data_en),
            self.ui.wr_data_end.eq(engine.ui.wr_data_end),
            self.ui.wr_data_mask.eq(engine.ui.wr_data_mask),
        ]
        self.req = Signal()
        self.comb += engine.req.eq(self.req)
        ack_sync = [Signal() for _ in range(2)]
        self.sync += [ack_sync[0].eq(engine.ack), ack_sync[1].eq(ack_sync[0])]
        ack_seen = Signal()
        complete = ack_sync[1] != ack_seen

        widx = Signal(6)
        words = [Constant(0, 32) for _ in range(48)]
        words[0] = Constant(amap.GFX2D_ID, 32)
        words[1] = Constant(amap.ABI_VERSION, 32)
        words[2] = Constant(amap.GFX2D_CAP, 32)
        words[3] = Constant(0, 32)
        words[4] = self.busy | (self.done << 1) | (self.error << 2)
        words[5] = self.irq_pending
        words[6] = self.irq_enable
        words[7] = self.last_error
        for name, sig in desc.items():
            words[reg[name] >> 2] = sig
        words[18] = self.commands
        words[19] = self.errors
        words[20] = Constant(0, 32)
        words[46] = Constant(0, 32)

        grp0 = Array(words[0:16])
        grp1 = Array(words[16:32])
        grp2 = Array(words[32:48])

        rdata_grp0 = Signal(32)
        rdata_grp1 = Signal(32)
        rdata_grp2 = Signal(32)
        read_ok = Signal()
        is_write_reg = Signal()
        write_ok = Signal()
        from migen import Case as _Case

        self.comb += [
            widx.eq(self.off[2:8]),
            rdata_grp0.eq(grp0[widx[0:4]]),
            rdata_grp1.eq(grp1[widx[0:4]]),
            rdata_grp2.eq(grp2[widx[0:4]]),
            If(widx[5],
                self.rdata.eq(rdata_grp2)
            ).Elif(widx[4],
                self.rdata.eq(rdata_grp1)
            ).Else(
                self.rdata.eq(rdata_grp0)
            ),
            read_ok.eq((self.off[0:2] == 0) & (self.off[8:12] == 0) &
                       (widx <= 46) & (widx != 3)),
            is_write_reg.eq((self.off[0:2] == 0) & (self.off[8:12] == 0) &
                            (widx >= 3) & (widx <= 46) &
                            (widx != 4) & (widx != 7) &
                            (widx != 18) & (widx != 19)),
            write_ok.eq(is_write_reg & ((widx != 3) | (self.wdata == 1))),
            self.ok.eq(self.selected & Mux(self.write, write_ok, read_ok)),
        ]

        go = self.commit & self.selected & self.write & self.ok

        self.sync += self.irq.eq(self.irq_pending & self.irq_enable)

        self.sync += If(complete,
                        ack_seen.eq(ack_sync[1]), self.busy.eq(0), self.done.eq(1),
                        self.error.eq(engine.done_err != 0),
                        self.last_error.eq(engine.done_err),
                        If(engine.done_err != 0, self.errors.eq(self.errors + 1)),
                        self.irq_pending.eq(1))

        lut_wr_act = go & (self.off == reg['LUT_DATA']) & (~self.busy)
        self.comb += [
            pal_w.we.eq(lut_wr_act & desc['LUT_ADDR'][8]),
            pal_w.adr.eq(desc['LUT_ADDR'][0:8]),
            pal_w.dat_w.eq(self.wdata),
            lut_w.we.eq(lut_wr_act & (~desc['LUT_ADDR'][8])),
            lut_w.adr.eq(desc['LUT_ADDR'][0:8]),
            lut_w.dat_w.eq(self.wdata),
        ]

        desc_cases = {
            reg[name]: [If(self.busy, self.errors.eq(self.errors + 1),
                           self.last_error.eq(ERR_BUSY), self.error.eq(1))
                        .Else(sig.eq(self.wdata))]
            for name, sig in desc.items()
        }
        self.sync += If(go, _Case(self.off, {
            reg['CONTROL']: [
                If(self.busy, self.errors.eq(self.errors + 1),
                   self.last_error.eq(ERR_BUSY), self.error.eq(1))
                .Else(self.commands.eq(self.commands + 1), self.busy.eq(1),
                      self.done.eq(0), self.error.eq(0), self.last_error.eq(ERR_NONE),
                      self.req.eq(~self.req))],
            reg['IRQ_ENABLE']: self.irq_enable.eq(self.wdata[0]),
            reg['IRQ_STATUS']: If(self.wdata[0], self.irq_pending.eq(0)),
            reg['CLEAR_ERRORS']: [
                If(self.wdata[0], self.done.eq(0), self.error.eq(0),
                   self.last_error.eq(ERR_NONE))],
            reg['LUT_DATA']: [
                If(self.busy, self.errors.eq(self.errors + 1),
                   self.last_error.eq(ERR_BUSY), self.error.eq(1))
                .Else(desc['LUT_ADDR'][0:8].eq(desc['LUT_ADDR'][0:8] + 1))],
            **desc_cases,
        }))
