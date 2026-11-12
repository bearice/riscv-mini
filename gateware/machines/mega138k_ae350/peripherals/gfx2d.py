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

        inst_is_wb = Signal()
        is_cmp = Signal()
        is_discard = Signal()
        self.comb += [
            inst_is_wb.eq(
                stage_active & pred_ok & (opcode != 0x38) & (opcode != 0x39)
            ),
            is_cmp.eq(
                stage_active & pred_ok & ((opcode == 0x38) | (pred == 3))
            ),
            is_discard.eq(stage_active & pred_ok & (opcode == 0x39)),
        ]
        p1_we = [
            [Signal(reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p2_we = [
            [Signal(reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p1_is_cmp = Signal(reset_less=True)
        p2_is_cmp = Signal(reset_less=True)
        p1_is_discard = Signal(reset_less=True)
        p2_is_discard = Signal(reset_less=True)

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
        p1_wr_msk = Signal(4, reset_less=True)
        p1_opcode = Signal(6, reset_less=True)
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
                self.sync += [
                    p1_r[r][c].eq(self.r_in[r][c]),
                    p1_we[r][c].eq(inst_is_wb & (dst_idx == r) & wr_msk[c]),
                ]
        self.sync += [
            p1_p.eq(self.p_in),
            p1_discard.eq(self.discard_in),
            p1_is_cmp.eq(is_cmp),
            p1_is_discard.eq(is_discard),
            p1_wr_msk.eq(wr_msk),
            p1_opcode.eq(opcode),
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
        p2_wr_msk = Signal(4, reset_less=True)
        p2_opcode = Signal(6, reset_less=True)
        p2_sat = Signal(reset_less=True)

        p2_arith = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_logic = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_prod16 = [Signal(16, reset_less=True) for _ in range(4)]
        p2_lerp_prod = [Signal((27, True), reset_less=True) for _ in range(4)]
        p2_match_ch = [Signal(reset_less=True) for _ in range(4)]
        p2_imm8 = Signal(8, reset_less=True)

        for c in range(4):
            s0 = p1_s0[c]
            s1 = p1_s1[c]
            s2 = p1_s2[c]

            add_val = Signal((17, True))
            sub_val = Signal((17, True))
            diff_val = Signal((17, True))
            min_val = Signal((17, True))
            max_val = Signal((17, True))
            clamp_val = Signal((17, True))
            prod16 = Signal(16)
            lerp_w = Signal(10)
            lerp_diff = Signal((17, True))
            lerp_prod = Signal((27, True))
            shl_val = Signal((16, True))
            shr_val = Signal((16, True))

            self.comb += [
                add_val.eq(s0 + s1),
                sub_val.eq(s0 - s1),
                diff_val.eq(Mux(s0 >= s1, s0 - s1, s1 - s0)),
                min_val.eq(Mux(s0 < s1, s0, s1)),
                max_val.eq(Mux(s0 > s1, s0, s1)),
                clamp_val.eq(Mux(s0 < s1, s1, Mux(s0 > s2, s2, s0))),
                prod16.eq(s0[0:8] * s1[0:8]),
                lerp_w.eq(s2[0:8] + (s2[0:8] >> 7)),
                lerp_diff.eq(s0 - s1),
                lerp_prod.eq(lerp_diff * lerp_w),
                shl_val.eq(s0 << p1_imm8[0:4]),
                shr_val.eq(s0 >> p1_imm8[0:4]),
            ]

            def sat_clamp(val):
                return Mux(val < 0, 0, Mux(val > 255, 255, val))

            arith_mux = Signal((16, True))
            self.comb += Case(p1_opcode[0:3], {
                0: arith_mux.eq(Mux(p1_sat, sat_clamp(s0), s0)),
                1: arith_mux.eq(Mux(p1_sat, sat_clamp(add_val), add_val)),
                2: arith_mux.eq(Mux(p1_sat, sat_clamp(sub_val), sub_val)),
                3: arith_mux.eq(Mux(p1_sat, sat_clamp(diff_val), diff_val)),
                4: arith_mux.eq(Mux(p1_sat, sat_clamp(min_val), min_val)),
                5: arith_mux.eq(Mux(p1_sat, sat_clamp(max_val), max_val)),
                6: arith_mux.eq(clamp_val),
                "default": arith_mux.eq(s0),
            })

            logic_mux = Signal((16, True))
            self.comb += Case(p1_opcode[0:3], {
                0: logic_mux.eq(s0 & s1),
                1: logic_mux.eq(s0 | s1),
                2: logic_mux.eq(s0 ^ s1),
                3: logic_mux.eq(Mux(p1_sat, sat_clamp(shl_val), shl_val)),
                4: logic_mux.eq(Mux(p1_sat, sat_clamp(shr_val), shr_val)),
                "default": logic_mux.eq(s0 & s1),
            })

            self.sync += [
                p2_s0[c].eq(s0),
                p2_s1[c].eq(s1),
                p2_s2[c].eq(s2),
                p2_src[c].eq(p1_src[c]),
                p2_dst[c].eq(p1_dst[c]),
                p2_fg[c].eq(p1_fg[c]),
                p2_bg[c].eq(p1_bg[c]),
                p2_arith[c].eq(arith_mux),
                p2_logic[c].eq(logic_mux),
                p2_prod16[c].eq(prod16),
                p2_lerp_prod[c].eq(lerp_prod),
                p2_match_ch[c].eq(
                    Mux(p1_sub_op == 0, s0 == s1,
                    Mux(p1_sub_op == 1, s0 != s1,
                    Mux(p1_sub_op == 2, s0 < s1, s0 >= s1)))
                ),
            ]
            for r in range(4):
                self.sync += [
                    p2_r[r][c].eq(p1_r[r][c]),
                    p2_we[r][c].eq(p1_we[r][c]),
                ]

        self.sync += [
            p2_p.eq(p1_p),
            p2_discard.eq(p1_discard),
            p2_is_cmp.eq(p1_is_cmp),
            p2_is_discard.eq(p1_is_discard),
            p2_wr_msk.eq(p1_wr_msk),
            p2_opcode.eq(p1_opcode),
            p2_sat.eq(p1_sat),
            p2_imm8.eq(p1_imm8),
        ]

        # Pipeline Sub-stage 3 (Post-multiply Math)
        p3_prod_unorm = [Signal(16, reset_less=True) for _ in range(4)]
        p3_mul_scaled = [Signal(16, reset_less=True) for _ in range(4)]
        p3_lerp_res = [Signal(16, reset_less=True) for _ in range(4)]
        if stage_idx == 1:
            p3_dot3_res = Signal(16, reset_less=True)

        p3_arith = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_logic = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_match_ch = [Signal(reset_less=True) for _ in range(4)]
        p3_s0 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_s2 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_r = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p3_we = [
            [Signal(reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p3_src = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_dst = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_fg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_bg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_p = Signal(reset_less=True)
        p3_discard = Signal(reset_less=True)
        p3_is_cmp = Signal(reset_less=True)
        p3_is_discard = Signal(reset_less=True)
        p3_wr_msk = Signal(4, reset_less=True)
        p3_opcode = Signal(6, reset_less=True)
        p3_sat = Signal(reset_less=True)

        for c in range(4):
            prod_raw = Signal(17)
            prod_unorm = Signal(16)
            mul_scaled_val = Signal(16)
            lerp_res = Signal((28, True))

            self.comb += [
                prod_raw.eq(p2_prod16[c] + 128),
                prod_unorm.eq((prod_raw + (prod_raw >> 8)) >> 8),
                mul_scaled_val.eq(p2_prod16[c] >> p2_imm8[0:4]),
                lerp_res.eq((p2_lerp_prod[c] + (p2_s1[c] << 8) + 128) >> 8),
            ]
            self.sync += [
                p3_prod_unorm[c].eq(prod_unorm),
                p3_mul_scaled[c].eq(mul_scaled_val),
                p3_lerp_res[c].eq(lerp_res[0:16]),
                p3_arith[c].eq(p2_arith[c]),
                p3_logic[c].eq(p2_logic[c]),
                p3_match_ch[c].eq(p2_match_ch[c]),
                p3_s0[c].eq(p2_s0[c]),
                p3_s2[c].eq(p2_s2[c]),
                p3_src[c].eq(p2_src[c]),
                p3_dst[c].eq(p2_dst[c]),
                p3_fg[c].eq(p2_fg[c]),
                p3_bg[c].eq(p2_bg[c]),
            ]
            for r in range(4):
                self.sync += [
                    p3_r[r][c].eq(p2_r[r][c]),
                    p3_we[r][c].eq(p2_we[r][c]),
                ]

        if stage_idx == 1:
            dot3_sum = Signal(19)
            dot3_res = Signal((17, True))
            self.comb += [
                dot3_sum.eq(p2_prod16[0] + p2_prod16[1] + p2_prod16[2] + 128),
                dot3_res.eq(dot3_sum >> 8),
            ]
            self.sync += p3_dot3_res.eq(dot3_res[0:16])

        self.sync += [
            p3_p.eq(p2_p),
            p3_discard.eq(p2_discard),
            p3_is_cmp.eq(p2_is_cmp),
            p3_is_discard.eq(p2_is_discard),
            p3_wr_msk.eq(p2_wr_msk),
            p3_opcode.eq(p2_opcode),
            p3_sat.eq(p2_sat),
        ]

        # Pipeline Sub-stage 4 (Result Muxing & Saturation)
        p4_res_ch = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_r = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p4_we = [
            [Signal(reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p4_src = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_dst = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_fg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_bg = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_p = Signal(reset_less=True)
        p4_discard = Signal(reset_less=True)
        p4_is_cmp = Signal(reset_less=True)
        p4_is_discard = Signal(reset_less=True)
        p4_all_match = Signal(reset_less=True)

        for c in range(4):
            muladd_res = Signal((17, True))
            self.comb += muladd_res.eq(p3_s2[c] + p3_prod_unorm[c])

            mult_mux = Signal((16, True))
            mult_cases = {
                0: mult_mux.eq(p3_prod_unorm[c]),
                1: mult_mux.eq(Mux(p3_sat & (p3_mul_scaled[c][8:16] != 0),
                                   255, p3_mul_scaled[c][0:16])),
                2: mult_mux.eq(Mux(p3_sat & (muladd_res > 255),
                                   255, muladd_res)),
                3: mult_mux.eq(p3_lerp_res[c]),
            }
            if stage_idx == 1:
                mult_cases[4] = mult_mux.eq(Mux(p3_sat & (p3_dot3_res > 255),
                                                 255, p3_dot3_res))
            mult_cases["default"] = mult_mux.eq(p3_prod_unorm[c])
            self.comb += Case(p3_opcode[0:3], mult_cases)

            res_ch = Signal((16, True))
            self.comb += Case(p3_opcode[4:6], {
                0: res_ch.eq(p3_arith[c]),
                1: res_ch.eq(mult_mux),
                2: res_ch.eq(p3_logic[c]),
                "default": res_ch.eq(p3_s0[c]),
            })
            self.sync += p4_res_ch[c].eq(res_ch)

        all_match = Signal()
        self.comb += all_match.eq(
            (~p3_wr_msk[0] | p3_match_ch[0]) &
            (~p3_wr_msk[1] | p3_match_ch[1]) &
            (~p3_wr_msk[2] | p3_match_ch[2]) &
            (~p3_wr_msk[3] | p3_match_ch[3])
        )

        for r in range(4):
            for c in range(4):
                self.sync += [
                    p4_r[r][c].eq(p3_r[r][c]),
                    p4_we[r][c].eq(p3_we[r][c]),
                ]

        self.sync += [
            p4_p.eq(p3_p),
            p4_discard.eq(p3_discard),
            p4_is_cmp.eq(p3_is_cmp),
            p4_is_discard.eq(p3_is_discard),
            p4_all_match.eq(all_match),
            [p4_src[c].eq(p3_src[c]) for c in range(4)],
            [p4_dst[c].eq(p3_dst[c]) for c in range(4)],
            [p4_fg[c].eq(p3_fg[c]) for c in range(4)],
            [p4_bg[c].eq(p3_bg[c]) for c in range(4)],
        ]

        # Pipeline Sub-stage 5 (Writeback & Pipeline Outputs)
        for r in range(4):
            for c in range(4):
                self.sync += [
                    If(p4_we[r][c],
                        self.r_out[r][c].eq(p4_res_ch[c])
                    ).Else(
                        self.r_out[r][c].eq(p4_r[r][c])
                    )
                ]

        self.sync += [
            If(p4_is_cmp,
                self.p_out.eq(p4_all_match)
            ).Else(
                self.p_out.eq(p4_p)
            ),
            If(p4_is_discard & p4_all_match,
                self.discard_out.eq(1)
            ).Else(
                self.discard_out.eq(p4_discard)
            ),
            [self.src_out[c].eq(p4_src[c]) for c in range(4)],
            [self.dst_out[c].eq(p4_dst[c]) for c in range(4)],
            [self.fg_out[c].eq(p4_fg[c]) for c in range(4)],
            [self.bg_out[c].eq(p4_bg[c]) for c in range(4)],
        ]


class Gfx2DEngine(LiteXModule):
    def __init__(self, ddr_size=1 << 30, response_timeout=1 << 18,
                 pal_port=None, lut_port=None):
        self.ui = DDR3UI()
        self.req = Signal()
        self.ack = Signal()
        self.done_err = Signal(32)
        self.desc = desc = [Signal(32) for _ in DESC_NAMES]
        self.busy = Signal(reset_less=True)

        if pal_port is not None:
            self.comb += pal_port.adr.eq(0)
        if lut_port is not None:
            self.comb += lut_port.adr.eq(0)

        local_desc = [Signal(32, reset_less=True) for _ in DESC_NAMES]
        op = local_desc[0][0:4]
        src_addr, dst_addr, src_stride, dst_stride = local_desc[1:5]
        width, height, fg, bg, blit_wb = local_desc[5:10]
        fmt = local_desc[10][0:4]
        shader_cfg = local_desc[11][0:16]
        src_w, src_h = local_desc[12], local_desc[13]
        uv_start_u, uv_start_v, uv_du_dx, uv_dv_dy = local_desc[14:18]
        inst0_lo, inst0_hi = local_desc[18], local_desc[19]
        inst1_lo, inst1_hi = local_desc[20], local_desc[21]
        inst2_lo, inst2_hi = local_desc[22], local_desc[23]
        inst3_lo, inst3_hi = local_desc[24], local_desc[25]
        uniforms_raw = local_desc[26:34]
        lut_addr_reg = local_desc[34]

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
            src_format.eq(Mux((op >= OP_SHADER) & src_fmt_override,
                              shader_cfg[12:16], fmt[0:4])),
        ]

        def get_bpp_shift(f):
            return Mux((f == FMT_XRGB8888) | (f == FMT_ARGB8888), 2,
                   Mux(f == FMT_INDEX8, 0, 1))

        dst_bpp_shift = Signal(2, reset_less=True)
        src_bpp_shift = Signal(2, reset_less=True)
        self.sync += [
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
        neg_src_stride = Signal(32, reset_less=True)
        neg_dst_stride = Signal(32, reset_less=True)
        nxt_row_start_reg = Signal(32, reset_less=True)
        nxt_src_row_start_reg = Signal(32, reset_less=True)
        beat_byte = Signal(32, reset_less=True)
        nxt_beat_byte = Signal(32)
        self.comb += nxt_beat_byte.eq(Cat(Constant(0, 5), beat_byte[5:32] + 1))
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
        beats_w_reg = Signal(16, reset_less=True)
        first_mask_reg = Signal(32, reset_less=True)
        last_mask_reg = Signal(32, reset_less=True)
        first_end = Signal(6)
        self.comb += first_end.eq(Mux((rb_hi5 != 0) | (off5 + rb_lo5 >= 32), 32,
                                     off5 + rb_lo5))
        first_bits = [(off5 <= i) & (i < first_end) for i in range(32)]
        last_count = Signal(6)
        self.comb += last_count.eq((off5 + rb_lo5 - 1)[0:5] + 1)
        last_bits = [i < last_count for i in range(32)]
        beats_left = Signal(16, reset_less=True)
        is_last_beat = Signal(reset_less=True)
        first_beat = Signal(reset_less=True)
        beat_mask = Signal(32)
        self.comb += beat_mask.eq(Mux(first_beat, first_mask,
                                     Mux(is_last_beat, last_mask, 0xffffffff)))
        src_beat_base = Signal(32, reset_less=True)
        b_beat_buf = Signal(256, reset_less=True)
        b_byte_idx = Signal(5, reset_less=True)
        src_beat_loaded = Signal(reset_less=True)
        src_byte_off = Signal(32, reset_less=True)
        bytes_in_beat = Signal(8, reset_less=True)
        bytes_in_beat_zero = Signal(reset_less=True)
        src_bytes_left = Signal(16, reset_less=True)
        src_bytes_left_zero = Signal(reset_less=True)
        bit_buf = Signal(24, reset_less=True)
        bit_cnt = Signal(5, reset_less=True)
        copy_data = Signal(256, reset_less=True)

        pat32_w0 = fg[:32]
        pat32_w1 = Cat(fg[24:32], fg[0:24])
        pat32_w2 = Cat(fg[16:32], fg[0:16])
        pat32_w3 = Cat(fg[8:32], fg[0:8])
        pat32_word = Signal(32)
        self.comb += pat32_word.eq(
            Mux(off5[0:2] == 0, pat32_w0,
            Mux(off5[0:2] == 1, pat32_w1,
            Mux(off5[0:2] == 2, pat32_w2, pat32_w3)))
        )
        pat16_even = Cat(fg[:16], fg[:16])
        pat16_odd = Cat(Cat(fg[8:16], fg[0:8]), Cat(fg[8:16], fg[0:8]))
        pat16_word = Signal(32)
        self.comb += pat16_word.eq(Mux(off5[0], pat16_odd, pat16_even))
        fill_word_comb = Signal(32)
        self.comb += fill_word_comb.eq(
            Mux(fmt == FMT_XRGB8888, pat32_word, pat16_word)
        )
        fill_word = Signal(32, reset_less=True)
        fill_pattern = Signal(256)
        self.comb += fill_pattern.eq(Cat(*[fill_word] * 8))

        blit_pix_per_beat = Signal(5, reset_less=True)
        blit_expanded = Signal(256)
        blit_pix_16 = [Mux(bit_buf[23 - j], fg[:16], bg[:16]) for j in range(16)]
        blit_pix_32 = [Mux(bit_buf[23 - j], fg[:32], bg[:32]) for j in range(8)]
        self.comb += blit_expanded.eq(
            Mux(fmt == FMT_XRGB8888, Cat(*blit_pix_32), Cat(*blit_pix_16)))
        blit_exp_reg = Signal(256, reset_less=True)

        # Output DDR registers
        req_blk = Signal(26, reset_less=True)
        out_data = Signal(256, reset_less=True)
        out_mask = Signal(32, reset_less=True)
        is_rd_state = Signal()
        src_byte = Signal(8)
        self.comb += src_byte.eq(
            Array([b_beat_buf[i*8:(i+1)*8] for i in range(32)])[b_byte_idx]
        )
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
        rem_w = Signal(16, reset_less=True)
        tile_w = Signal(11, reset_less=True)
        lb_rem = Signal(11, reset_less=True)
        comp_rem = Signal(11, reset_less=True)
        comp_out_rem = Signal(11, reset_less=True)
        wb_rem = Signal(11, reset_less=True)
        tile_wb_done = Signal(reset_less=True)
        t_src_blk = Signal(26, reset_less=True)
        t_src_off = Signal(5, reset_less=True)
        t_dst_base_blk = Signal(26, reset_less=True)
        t_dst_blk = Signal(26, reset_less=True)
        t_dst_off = Signal(5, reset_less=True)
        t_beat_buf = Signal(256, reset_less=True)
        t_src_is_first = Signal(reset_less=True)
        t_dst_is_first = Signal(reset_less=True)
        sub_idx = Signal(6, reset_less=True)
        lb_wr_ptr = Signal(11, reset_less=True)
        lb_is_last_pix = Signal(reset_less=True)
        wb_is_last_pix = Signal(reset_less=True)

        # Compute pipeline signals
        comp_idx = Signal(11, reset_less=True)
        comp_out_idx = Signal(11, reset_less=True)
        comp_pipe_vld = Signal(12, reset_less=True)
        fetch_x = Signal(16, reset_less=True)
        fetch_odd = Signal(reset_less=True)
        p0_src_pix = [Signal((16, True), reset_less=True) for _ in range(4)]
        p0_dst_pix = [Signal((16, True), reset_less=True) for _ in range(4)]
        p0_fetch_x = Signal(16, reset_less=True)

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

        # Stage 0 inputs (with P0 Line Buffer pipeline register)
        src_lb_dat = Mux(fetch_odd, self.src_lb_o_r.dat_r, self.src_lb_e_r.dat_r)
        dst_lb_dat = self.dst_lb_r.dat_r
        src_lb_ch = [
            Cat(src_lb_dat[0:8], Constant(0, 8)),
            Cat(src_lb_dat[8:16], Constant(0, 8)),
            Cat(src_lb_dat[16:24], Constant(0, 8)),
            Cat(src_lb_dat[24:32], Constant(0, 8)),
        ]
        dst_lb_ch = [
            Cat(dst_lb_dat[0:8], Constant(0, 8)),
            Cat(dst_lb_dat[8:16], Constant(0, 8)),
            Cat(dst_lb_dat[16:24], Constant(0, 8)),
            Cat(dst_lb_dat[24:32], Constant(0, 8)),
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
        self.sync += [
            p0_fetch_x.eq(fetch_x),
            [p0_src_pix[c].eq(Mux(uses_src, src_lb_ch[c], fg_ch[c]))
             for c in range(4)],
            [p0_dst_pix[c].eq(Mux(uses_dst, dst_lb_ch[c], bg_ch[c]))
             for c in range(4)],
        ]
        for c in range(4):
            self.comb += [
                stage0.src_pix[c].eq(p0_src_pix[c]),
                stage0.dst_pix[c].eq(p0_dst_pix[c]),
                stage0.fg_pix[c].eq(fg_ch[c]),
                stage0.bg_pix[c].eq(bg_ch[c]),
                stage0.r_in[0][c].eq(p0_src_pix[c]),
                stage0.r_in[1][c].eq(p0_dst_pix[c]),
            ]
        self.comb += [
            stage0.r_in[2][0].eq(p0_fetch_x[0:16]),
            stage0.r_in[2][1].eq(row[0:16]),
            stage0.r_in[2][2].eq(0),
            stage0.r_in[2][3].eq(0),
            stage0.r_in[3][0].eq(p0_fetch_x[0:16]),
            stage0.r_in[3][1].eq(row[0:16]),
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
        cur_wb_blk = Signal(26, reset_less=True)
        nxt_cur_wb_blk = Signal(26, reset_less=True)
        byte_slot = Signal(6, reset_less=True)
        byte_full = Signal()
        self.comb += byte_full.eq(
            Mux(dst_bpp_shift == 2, byte_slot == 28, byte_slot == 30)
        )

        rd_vld_fsm = Signal(name_override="gfx2d_rd_vld_fsm")
        rd_vld_fsm.attr.add(("syn_keep", 1))
        rd_vld_buf = Signal(name_override="gfx2d_rd_vld_buf")
        rd_vld_buf.attr.add(("syn_keep", 1))
        self.comb += [
            rd_vld_fsm.eq(self.ui.rd_data_valid),
            rd_vld_buf.eq(self.ui.rd_data_valid),
        ]

        self.fsm = fsm = FSM(reset_state='IDLE')
        self.sync += [local_desc[i].eq(desc[i]) for i in range(len(DESC_NAMES))]
        self.sync += [
            If(fsm.ongoing('C_RWAIT') & rd_vld_buf,
                copy_data.eq(self.ui.rd_data)),
            If(fsm.ongoing('B_RD_WAIT') & rd_vld_buf,
                b_beat_buf.eq(self.ui.rd_data)),
            If((fsm.ongoing('T_SRC_RWAIT') | fsm.ongoing('T_DST_RWAIT')) & rd_vld_buf,
                t_beat_buf.eq(self.ui.rd_data)),
        ]
        self.comb += [
            is_rd_state.eq(fsm.ongoing('C_RSEND') | fsm.ongoing('B_RD_SEND') |
                           fsm.ongoing('T_SRC_RREQ') | fsm.ongoing('T_DST_RREQ')),
            self.ui.cmd.eq(Mux(is_rd_state, 1, 0)),
            self.ui.burst.eq(0),
            self.ui.addr.eq(Cat(Constant(0, 3), req_blk)),
            self.ui.wr_data.eq(out_data),
            self.ui.wr_data_mask.eq(~out_mask),
            self.ui.wr_data_end.eq(0),
        ]
        rd_base = Signal(32)
        self.comb += rd_base.eq(Mux(fsm.ongoing('B_RD_PREP'), src_beat_base,
                                    src_beat_base + (beat << 5)))
        height_m1 = Signal(32, reset_less=True)
        fsm.act('IDLE',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(self.busy, 0),
                If(start, NextValue(err, 0), NextValue(elapsed, 0),
                   NextValue(self.busy, 1),
                   NextValue(row, 0), NextValue(rows_left, height),
                   NextValue(last_row, height == 1), NextValue(beat, 0),
                   NextValue(row_start, dst_addr),
                   NextValue(src_row_start, src_addr),
                   NextValue(height_m1, height - 1),
                   NextState('INIT')))

        src_span = Signal(32, reset_less=True)
        dst_span = Signal(32, reset_less=True)
        src_step_total = Signal(32, reset_less=True)
        dst_step_total = Signal(32, reset_less=True)
        row_start_down = Signal(32, reset_less=True)
        src_row_start_down = Signal(32, reset_less=True)
        width_bytes = Signal(32, reset_less=True)
        fsm.act('INIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(limit, ddr_size - row_bytes),
                NextValue(src_limit, Mux(op == OP_IMAGE_BLIT,
                                         ddr_size - blit_wb, ddr_size - src_row_bytes)),
                NextValue(src_step_total, height_m1 * src_stride),
                NextValue(dst_step_total, height_m1 * dst_stride),
                NextValue(blit_pix_per_beat, Mux(fmt == FMT_XRGB8888, 8, 16)),
                NextValue(width_bytes, (width + 7) >> 3),
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
                NextValue(src_span, src_step_total + src_row_bytes),
                NextValue(dst_span, dst_step_total + row_bytes),
                NextValue(row_start_down, dst_addr + dst_step_total),
                NextValue(src_row_start_down, src_addr + src_step_total),
                NextValue(cmp_dst_stride, dst_stride < row_bytes),
                NextValue(cmp_src_stride, src_stride < src_row_bytes),
                NextValue(cmp_blit_wb, blit_wb < width_bytes),
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
                NextState('INIT2B'))

        fsm.act('INIT2B',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(src_end, src_addr + src_span),
                NextValue(dst_end, dst_addr + dst_span),
                NextValue(rb_hi5, row_bytes >> 5),
                NextValue(rb_lo5, row_bytes[0:5]),
                NextState('INIT2B2'))

        fsm.act('INIT2B2',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(overlap_src_lt_dst, src_addr < dst_end),
                NextValue(overlap_dst_lt_src, dst_addr < src_end),
                NextState('INIT2C'))

        bad_blit_dst = Mux(fmt == FMT_XRGB8888,
                           (dst_addr[0:2] != 0) | (dst_stride[0:2] != 0),
                           (dst_addr[0] != 0) | (dst_stride[0] != 0))
        fmt_hi_bad = local_desc[10][4:32] != 0
        op_hi_bad = local_desc[0][4:32] != 0
        legacy_fmt_bad = (op < OP_SHADER) & ((fmt == FMT_RGB888) | (fmt > FMT_XRGB1555))
        dst_fmt_bad = fmt_hi_bad | (dst_format == FMT_RGB888) | \
                      (dst_format >= FMT_INDEX8) | legacy_fmt_bad
        src_fmt_bad = (src_format == FMT_RGB888) | (src_format >= FMT_INDEX8)
        cfg_bad = (op == OP_SCALE_BLIT) | scale_en | bilinear_en | (last_stage > 1)
        dim_bad = (width == 0) | (height == 0) | \
                  ((op >= OP_SHADER) & ((width > 16384) | (height > 16384)))

        cur_unequal_stride = (~cmp_stride_equal) & cur_mem_overlap
        cur_bad_shader = uses_src & cur_mem_overlap & (~overlap_safe)

        static_bad = (
            (op > OP_SCALE_BLIT) |
            op_hi_bad |
            dim_bad |
            cmp_dst_stride |
            dst_fmt_bad |
            ((op == OP_COPY_AREA) & (
                cmp_src_stride |
                (src_addr[0:5] != dst_addr[0:5]) |
                (src_stride[0:5] != dst_stride[0:5]) |
                cmp_right_overlap |
                cur_unequal_stride
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
                cur_bad_shader
            ))
        )
        static_bad_reg = Signal(reset_less=True)

        fsm.act('INIT2C',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(mem_overlap, cur_mem_overlap),
                NextValue(unequal_stride_overlap, cur_unequal_stride),
                NextValue(bad_shader_overlap, cur_bad_shader),
                NextValue(dir_down, dir_down_step),
                NextValue(neg_src_stride, -src_stride),
                NextValue(neg_dst_stride, -dst_stride),
                NextValue(static_bad_reg, static_bad),
                NextState('INIT3'))

        fsm.act('INIT3',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(delta_dst, Mux(dir_down, neg_dst_stride, dst_stride)),
                NextValue(delta_src, Mux(op == OP_IMAGE_BLIT, blit_wb,
                                         Mux(dir_down, neg_src_stride, src_stride))),
                If(static_bad_reg,
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

        pascal = [NextValue(beats, beats_w_reg),
                  NextValue(beats_left, beats_w_reg),
                  NextValue(is_last_beat, beats_w_reg == 1),
                  NextValue(first_beat, 1),
                  NextValue(beat_base, row_start & ~Constant(31, 32)),
                  NextValue(beat_byte, row_start & ~Constant(31, 32)),
                  NextValue(first_mask, first_mask_reg),
                  NextValue(last_mask, last_mask_reg)]

        bad_row_addr_reg = Signal(reset_less=True)
        fsm.act('ROW_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(bad_row_addr_reg,
                          (row_start > limit) |
                          ((op != OP_FILL_RECT) & (src_row_start > src_limit))),
                NextValue(beats_w_reg, beats_w),
                NextValue(first_mask_reg, Cat(*first_bits)),
                NextValue(last_mask_reg, Cat(*last_bits)),
                NextValue(fill_word, fill_word_comb),
                NextValue(nxt_row_start_reg, row_start + delta_dst),
                NextValue(nxt_src_row_start_reg, src_row_start + delta_src),
                NextState('ROW_CHECK'))
        fsm.act('ROW_CHECK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(bad_row_addr_reg,
                    NextValue(err, ERR_ADDR), NextState('FINISH')
                ).Else(
                    NextState('ROW_PASCAL')
                ))
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
                                    row_start[2:5], row_start[1:5])),
                      NextState('B_FETCH'))
                .Elif(op == OP_SHADER,
                      NextValue(tile_x, 0),
                      NextValue(rem_w, width),
                      NextState('T_SETUP'))
                .Else(NextState('PREP')))
        down = dir_down

        fsm.act('ROW_INC',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(row, Mux(down, row - 1, row + 1)),
                NextValue(rows_left, rows_left - 1),
                NextValue(last_row, rows_left == 2),
                NextValue(beat, 0),
                NextValue(row_start, nxt_row_start_reg),
                NextValue(src_row_start, nxt_src_row_start_reg),
                NextState('ROW_SETUP'))

        # Existing OP fast paths
        fsm.act('PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(req_blk, beat_byte[5:31]),
                NextValue(out_data, fill_pattern),
                NextValue(out_mask, beat_mask),
                NextState('SEND'))
        fsm.act('SEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1),
                self.ui.wr_data_end.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Elif(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    If(is_last_beat,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1),
                             NextValue(beats_left, beats_left - 1),
                             NextValue(is_last_beat, beats_left == 2),
                             NextValue(first_beat, 0),
                             NextValue(beat_byte, nxt_beat_byte),
                             NextState('PREP')),
                ).Else(NextValue(elapsed, elapsed + 1)))

        fsm.act('C_RPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(req_blk, rd_base[5:31]),
                NextState('C_RSEND'))
        fsm.act('C_RSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(0),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Elif(self.ui.cmd_ready,
                    NextValue(elapsed, 0), NextState('C_RWAIT'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('C_RWAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(elapsed, elapsed + 1),
                If(rd_vld_fsm,
                    NextState('C_WPREP'),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ))
        fsm.act('C_WPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(req_blk, beat_byte[5:31]),
                NextValue(out_data, copy_data),
                NextValue(out_mask, beat_mask),
                NextState('C_WSEND'))
        fsm.act('C_WSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1),
                self.ui.wr_data_end.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Elif(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    If(is_last_beat,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1),
                             NextValue(beats_left, beats_left - 1),
                             NextValue(is_last_beat, beats_left == 2),
                             NextValue(beat_byte, nxt_beat_byte),
                             NextValue(first_beat, 0),
                             NextState('C_RPREP')),
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
                      NextValue(b_byte_idx, b_byte_idx + 1),
                      NextValue(src_bytes_left, src_bytes_left - 1),
                      NextValue(src_bytes_left_zero, src_bytes_left == 1),
                      NextValue(src_byte_pending, 0),
                      NextState('B_FETCH')))
        fsm.act('B_RD_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(req_blk, rd_base[5:31]),
                NextState('B_RD_SEND'))
        fsm.act('B_RD_SEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(0),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Elif(self.ui.cmd_ready,
                    NextValue(elapsed, 0), NextState('B_RD_WAIT'),
                ).Else(NextValue(elapsed, elapsed + 1)))
        fsm.act('B_RD_WAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(elapsed, elapsed + 1),
                If(rd_vld_fsm,
                    NextValue(b_byte_idx, src_byte_off[0:5]),
                    NextValue(src_beat_loaded, 1), NextState('B_FETCH'),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ))
        fsm.act('B_EXP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(blit_exp_reg, blit_expanded), NextState('B_PREP'))
        fsm.act('B_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(req_blk, beat_byte[5:31]),
                NextValue(out_data, blit_exp_reg),
                NextValue(out_mask, beat_mask),
                NextState('B_WSEND'))
        fsm.act('B_WSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1),
                self.ui.wr_data_end.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Elif(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    NextValue(elapsed, 0),
                    NextValue(bit_buf,
                              Mux(fmt == FMT_XRGB8888, bit_buf << 8, bit_buf << 16)),
                    NextValue(bit_cnt,
                              Mux(bit_cnt > blit_pix_per_beat,
                                  bit_cnt - blit_pix_per_beat, 0)),
                    If(is_last_beat,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextValue(beat, beat + 1),
                             NextValue(beats_left, beats_left - 1),
                             NextValue(is_last_beat, beats_left == 2),
                             NextValue(first_beat, 0),
                             NextValue(beat_byte, nxt_beat_byte),
                             NextState('B_FETCH')),
                ).Else(NextValue(elapsed, elapsed + 1)))

        # OP_SHADER Tile FSM
        tile_is_last = Signal(reset_less=True)
        calc_t_w = Signal(16)
        self.comb += [
            calc_t_w.eq(Mux(rem_w > 1024, 1024, rem_w)),
        ]
        cur_src_byte = Signal(32)
        cur_dst_byte = Signal(32)
        self.comb += [
            cur_src_byte.eq(src_row_start + (tile_x << src_bpp_shift)),
            cur_dst_byte.eq(row_start + (tile_x << dst_bpp_shift)),
        ]
        t_src_addr_reg = Signal(32, reset_less=True)
        t_dst_addr_reg = Signal(32, reset_less=True)

        fsm.act('T_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(tile_w, calc_t_w[0:11]),
                NextValue(tile_is_last, rem_w <= 1024),
                NextValue(t_src_addr_reg, cur_src_byte),
                NextValue(t_dst_addr_reg, cur_dst_byte),
                NextState('T_SETUP2'))

        fsm.act('T_SETUP2',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(t_src_blk, t_src_addr_reg[5:31]),
                NextValue(t_src_off, t_src_addr_reg[0:5]),
                NextValue(t_dst_base_blk, t_dst_addr_reg[5:31]),
                NextValue(t_dst_blk, t_dst_addr_reg[5:31]),
                NextValue(t_dst_off, t_dst_addr_reg[0:5]),
                NextValue(t_src_is_first, 1),
                NextValue(t_dst_is_first, 1),
                NextValue(req_blk,
                          Mux(uses_src,
                              t_src_addr_reg[5:31], t_dst_addr_reg[5:31])),
                NextValue(lb_wr_ptr, 0),
                NextValue(lb_rem, tile_w),
                NextValue(lb_is_last_pix, tile_w == 1),
                If(uses_src,
                    NextState('T_SRC_RREQ')
                ).Elif(uses_dst,
                    NextState('T_DST_RREQ')
                ).Else(
                    NextState('T_COMP_START')
                ))

        fsm.act('T_SRC_RREQ',
                self.ui.cmd.eq(1), self.ui.cmd_en.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Elif(self.ui.cmd_ready,
                    NextValue(elapsed, 0),
                    NextValue(t_src_blk, t_src_blk + 1),
                    NextValue(sub_idx,
                              Mux(t_src_is_first, t_src_off >> src_bpp_shift, 0)),
                    NextValue(t_src_is_first, 0),
                    NextState('T_SRC_RWAIT')
                ).Else(NextValue(elapsed, elapsed + 1)))

        fsm.act('T_SRC_RWAIT',
                self.ui.cmd.eq(0), self.ui.cmd_en.eq(0),
                NextValue(elapsed, elapsed + 1),
                If(rd_vld_fsm,
                    NextState('T_SRC_UNPACK')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ))

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
                If(lb_is_last_pix,
                    NextValue(lb_wr_ptr, 0),
                    NextValue(lb_rem, tile_w),
                    NextValue(lb_is_last_pix, tile_w == 1),
                    If(uses_dst,
                        NextValue(req_blk, t_dst_blk),
                        NextState('T_DST_RREQ')
                    ).Else(NextState('T_COMP_START'))
                ).Else(
                    NextValue(lb_wr_ptr, lb_wr_ptr + 1),
                    NextValue(lb_rem, lb_rem - 1),
                    NextValue(sub_idx, sub_idx + 1),
                    NextValue(lb_is_last_pix, lb_rem == 2),
                    If(sub_idx == src_max_sub - 1,
                        NextValue(req_blk, t_src_blk),
                        NextState('T_SRC_RREQ')
                    )
                ))

        # DST prefetch
        fsm.act('T_DST_RREQ',
                self.ui.cmd.eq(1), self.ui.cmd_en.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Elif(self.ui.cmd_ready,
                    NextValue(elapsed, 0),
                    NextValue(t_dst_blk, t_dst_blk + 1),
                    NextValue(sub_idx,
                              Mux(t_dst_is_first, t_dst_off >> dst_bpp_shift, 0)),
                    NextValue(t_dst_is_first, 0),
                    NextState('T_DST_RWAIT')
                ).Else(NextValue(elapsed, elapsed + 1)))

        fsm.act('T_DST_RWAIT',
                self.ui.cmd.eq(0), self.ui.cmd_en.eq(0),
                NextValue(elapsed, elapsed + 1),
                If(rd_vld_fsm,
                    NextState('T_DST_UNPACK')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ))

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
                If(lb_is_last_pix,
                    NextValue(lb_wr_ptr, 0),
                    NextState('T_COMP_START')
                ).Else(
                    NextValue(lb_wr_ptr, lb_wr_ptr + 1),
                    NextValue(lb_rem, lb_rem - 1),
                    NextValue(sub_idx, sub_idx + 1),
                    NextValue(lb_is_last_pix, lb_rem == 2),
                    If(sub_idx == dst_max_sub - 1,
                        NextValue(req_blk, t_dst_blk),
                        NextState('T_DST_RREQ')
                    )
                ))

        # Streaming compute
        comp_pipe_done = Signal()
        self.comb += [
            self.src_lb_e_r.adr.eq(comp_idx >> 1),
            self.src_lb_o_r.adr.eq(comp_idx >> 1),
            self.dst_lb_r.adr.eq(comp_idx),
        ]

        comp_active = Signal(reset_less=True)
        comp_out_last = Signal(reset_less=True)
        fsm.act('T_COMP_START',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(comp_idx, 0),
                NextValue(comp_out_idx, 0),
                NextValue(comp_pipe_vld, 0),
                NextValue(comp_active, 1),
                NextValue(comp_rem, tile_w),
                NextValue(comp_out_rem, tile_w),
                NextValue(comp_out_last, tile_w == 1),
                NextState('T_COMP_STREAM'))

        fsm.act('T_COMP_STREAM',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(comp_active,
                    NextValue(comp_idx, comp_idx + 1),
                    NextValue(comp_rem, comp_rem - 1),
                    NextValue(fetch_x, tile_x + comp_idx),
                    NextValue(fetch_odd, comp_idx[0]),
                    NextValue(comp_pipe_vld,
                              Cat(Constant(1, 1), comp_pipe_vld[0:11])),
                    If(comp_rem == 1,
                        NextValue(comp_active, 0)
                    )
                ).Else(
                    NextValue(comp_pipe_vld,
                              Cat(Constant(0, 1), comp_pipe_vld[0:11])),
                ),

                If(comp_pipe_vld[11],
                    self.out_lb_w.we.eq(1),
                    self.out_lb_w.adr.eq(comp_out_idx),
                    self.out_lb_w.dat_w.eq(Cat(
                        stage1.r_out[0][0][0:8], stage1.r_out[0][1][0:8],
                        stage1.r_out[0][2][0:8], stage1.r_out[0][3][0:8],
                        stage1.discard_out
                    )),
                    NextValue(comp_out_idx, comp_out_idx + 1),
                    NextValue(comp_out_rem, comp_out_rem - 1),
                    NextValue(comp_out_last, comp_out_rem == 2),
                    If(comp_out_last,
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
                NextValue(wb_rem, tile_w),
                NextValue(tile_wb_done, 0),
                NextValue(out_mask, 0),
                NextValue(byte_slot, t_dst_off),
                NextValue(cur_wb_blk, t_dst_base_blk),
                NextValue(nxt_cur_wb_blk, t_dst_base_blk + 1),
                NextValue(req_blk, t_dst_base_blk),
                NextValue(wb_is_last_pix, tile_w == 1),
                NextState('T_WB_READ'))

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
        dith_g_off = Signal(4)
        self.comb += [
            dith_g_off.eq(Mux(dst_format == FMT_RGB565,
                              wb_thresh >> 2, wb_thresh >> 1)),
            If(dither_en,
                dith_r.eq(Mux(out_r + (wb_thresh >> 1) > 255,
                          255, out_r + (wb_thresh >> 1))),
                dith_g.eq(Mux(out_g + dith_g_off > 255,
                          255, out_g + dith_g_off)),
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
                packed_pix.eq(
                    Cat(dith_b[3:8], dith_g[3:8], dith_r[3:8], out_a >= 128)),
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

        fsm.act('T_WB_READ',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextState('T_WB_PACK'))

        wb_cases = {}
        for s in range(0, 32, 2):
            if s % 4 == 0:
                wb_cases[s] = [
                    If(dst_bpp_shift == 2,
                        NextValue(out_data[s*8:(s+4)*8], packed_pix[0:32]),
                        If(~out_disc, NextValue(out_mask[s:s+4], 0xF))
                    ).Else(
                        NextValue(out_data[s*8:(s+2)*8], packed_pix[0:16]),
                        If(~out_disc, NextValue(out_mask[s:s+2], 0x3))
                    )
                ]
            else:
                wb_cases[s] = [
                    NextValue(out_data[s*8:(s+2)*8], packed_pix[0:16]),
                    If(~out_disc, NextValue(out_mask[s:s+2], 0x3))
                ]

        fsm.act('T_WB_PACK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                Case(byte_slot, wb_cases),
                NextValue(byte_slot, byte_slot + pix_bytes),
                NextValue(wb_idx, wb_idx + 1),
                NextValue(wb_rem, wb_rem - 1),
                NextValue(wb_is_last_pix, wb_rem == 2),
                NextValue(tile_wb_done, wb_is_last_pix),
                If(byte_full | wb_is_last_pix,
                    NextState('T_WB_PREP')
                ).Else(
                    NextState('T_WB_READ')
                ))

        fsm.act('T_WB_PREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(elapsed, 0),
                NextValue(req_blk, cur_wb_blk),
                NextValue(nxt_cur_wb_blk, cur_wb_blk + 1),
                NextState('T_WB_SEND'))

        fsm.act('T_WB_SEND',
                If(out_mask == 0,
                    NextValue(cur_wb_blk, nxt_cur_wb_blk),
                    NextValue(out_mask, 0),
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
                    If(elapsed_timeout,
                        NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                    ).Elif(self.ui.cmd_ready & self.ui.wr_data_rdy,
                        NextValue(elapsed, 0),
                        NextValue(cur_wb_blk, nxt_cur_wb_blk),
                        NextValue(out_mask, 0),
                        NextValue(byte_slot, 0),
                        If(tile_wb_done,
                            NextState('T_TILE_NEXT')
                        ).Else(
                            NextState('T_WB_READ')
                        )
                    ).Else(NextValue(elapsed, elapsed + 1))
                ))

        fsm.act('T_TILE_NEXT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(tile_x, tile_x + 1024),
                NextValue(rem_w, rem_w - 1024),
                If(tile_is_last,
                    If(last_row, NextState('FINISH'))
                    .Else(NextState('ROW_INC'))
                ).Else(
                    NextState('T_SETUP')
                ))

        fsm.act('FINISH',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(self.busy, 0),
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

        rdata_cases = {i: self.rdata.eq(words[i])
                       for i in range(len(words))
                       if not (i == 3 or i == 20 or i == 46)}
        rdata_cases["default"] = self.rdata.eq(0)

        read_ok = Signal()
        is_write_reg = Signal()
        write_ok = Signal()

        self.comb += [
            widx.eq(self.off[2:8]),
            Case(widx, rdata_cases),
            read_ok.eq((self.off[0:2] == 0) & (self.off[8:12] == 0) &
                       (widx <= 46) & (widx != 3)),
            is_write_reg.eq((self.off[0:2] == 0) & (self.off[8:12] == 0) &
                            (widx >= 3) & (widx <= 46) &
                            (widx != 4) & (widx != 7) &
                            (widx != 18) & (widx != 19)),
            write_ok.eq(is_write_reg & ((widx != 3) | self.wdata[0])),
            self.ok.eq(self.selected & Mux(self.write, write_ok, read_ok)),
        ]

        go = self.commit & self.selected & self.write & self.ok

        self.sync += self.irq.eq(self.irq_pending & self.irq_enable)

        self.sync += If(complete,
                        ack_seen.eq(ack_sync[1]), self.busy.eq(0), self.done.eq(1),
                        self.error.eq(engine.done_err != 0),
                        self.last_error.eq(engine.done_err),
                        self.irq_pending.eq(1))

        busy_write_err = Signal()
        self.comb += busy_write_err.eq(
            go & self.busy &
            (self.off != reg['IRQ_ENABLE']) &
            (self.off != reg['IRQ_STATUS']) &
            (self.off != reg['CLEAR_ERRORS'])
        )
        self.sync += [
            If(busy_write_err,
                self.errors.eq(self.errors + 1),
                self.last_error.eq(ERR_BUSY),
                self.error.eq(1),
            ).Elif(complete & (engine.done_err != 0),
                self.errors.eq(self.errors + 1),
            )
        ]

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
            reg[name]: [If(~self.busy, sig.eq(self.wdata))]
            for name, sig in desc.items()
        }
        self.sync += If(go, Case(self.off, {
            reg['CONTROL']: [
                If(~self.busy,
                   self.commands.eq(self.commands + 1), self.busy.eq(1),
                   self.done.eq(0), self.error.eq(0), self.last_error.eq(ERR_NONE),
                   self.req.eq(~self.req))],
            reg['IRQ_ENABLE']: self.irq_enable.eq(self.wdata[0]),
            reg['IRQ_STATUS']: If(self.wdata[0], self.irq_pending.eq(0)),
            reg['CLEAR_ERRORS']: [
                If(self.wdata[0], self.done.eq(0), self.error.eq(0),
                   self.last_error.eq(ERR_NONE))],
            reg['LUT_DATA']: [
                If(~self.busy, desc['LUT_ADDR'][0:8].eq(desc['LUT_ADDR'][0:8] + 1))],
            **desc_cases,
        }))
