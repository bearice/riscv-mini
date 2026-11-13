"""GFX2D 2D pixel engine with Phase 2 programmable shader pipeline (Step 6c)."""
from functools import reduce
from operator import or_

from migen import (Array, Case, Cat, ClockDomainsRenamer, Constant,
                   If, Memory, Mux, NextState, NextValue, Signal)
from litex.gen import LiteXModule
from gateware.machines.mega138k_ae350 import address_map as amap
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI
from gateware.machines.mega138k_ae350.onehot_fsm import OneHotFSM

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
    def __init__(self, stage_idx, lut_ports=None):
        self.inst = Signal(64)
        self.last_stage = Signal(2)
        self.r_in = [[Signal((16, True)) for _ in range(4)] for _ in range(4)]
        self.src_pix = [Signal((16, True)) for _ in range(4)]
        self.dst_pix = [Signal((16, True)) for _ in range(4)]
        # FG/BG/uniforms are unorm8 channels held static for a whole command.
        self.fg_pix = [Signal(8) for _ in range(4)]
        self.bg_pix = [Signal(8) for _ in range(4)]
        self.uniforms = [[Signal(8) for _ in range(4)] for _ in range(8)]
        self.p_in = Signal()
        self.discard_in = Signal()

        self.r_out = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        self.src_out = [Signal((16, True), reset_less=True) for _ in range(4)]
        self.dst_out = [Signal((16, True), reset_less=True) for _ in range(4)]
        self.p_out = Signal()
        self.discard_out = Signal()

        # The instruction is static for a whole command; a local copy keeps
        # the shared descriptor registers off every stage's operand paths.
        inst = Signal(64, reset_less=True)
        inst.attr.add(("syn_maxfan", 16))
        last_stage = Signal(2, reset_less=True)
        self.sync += [inst.eq(self.inst), last_stage.eq(self.last_stage)]

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
            opcode.eq(inst[58:64]),
            pred.eq(inst[56:58]),
            dst_idx.eq(inst[52:56]),
            wr_msk.eq(inst[48:52]),
            src0_idx.eq(inst[44:48]),
            src1_idx.eq(inst[40:44]),
            src2_idx.eq(inst[36:40]),
            swz0.eq(inst[28:36]),
            swz1.eq(inst[20:28]),
            swz2.eq(inst[12:20]),
            sat.eq(inst[11]),
            use_imm.eq(inst[10]),
            sub_op.eq(inst[8:10]),
            imm8.eq(inst[0:8]),
        ]

        stage_active = Signal()
        self.comb += stage_active.eq(stage_idx <= last_stage)

        pred_ok = Signal()
        self.comb += [
            If(pred == 0, pred_ok.eq(1))
            .Elif(pred == 1, pred_ok.eq(self.p_in == 1))
            .Elif(pred == 2, pred_ok.eq(self.p_in == 0))
            .Else(pred_ok.eq(1))
        ]

        def select_src(idx):
            # FG/BG/U0..U7 operands are static, so resolve them into a local
            # register ahead of the pixel stream.
            # idx 6/7 = FG/BG and 8..15 = U0..U7; other values are masked by
            # sel below. An explicit 8:1 + 2:1 tree keeps the mux shallow.
            static = [Signal(8, reset_less=True) for _ in range(4)]
            for c in range(4):
                uni = Signal(8, reset_less=True)
                self.sync += uni.eq(
                    Array([self.uniforms[u][c] for u in range(8)])[idx[0:3]])
                fgbg = Mux(idx[0], self.bg_pix[c], self.fg_pix[c])
                self.sync += static[c].eq(Mux(idx[3], uni, fgbg))
            # A second stage lets the descriptor-side mux sit near the shared
            # descriptor and this copy near the stage's operand logic.
            static_q = [Signal(8, reset_less=True) for _ in range(4)]
            self.sync += [static_q[c].eq(static[c]) for c in range(4)]

            # The index is static too, so its decode is registered with the
            # static operand and the pixel path is a single AND-OR level.
            sel = [Signal(reset_less=True) for _ in range(7)]
            for s in sel:
                s.attr.add(("syn_maxfan", 16))
            self.sync += [sel[k].eq(idx == k) for k in range(6)]
            self.sync += sel[6].eq(idx >= 6)
            cands = [*self.r_in, self.src_pix, self.dst_pix, static_q]
            res = [Signal((16, True)) for _ in range(4)]
            for c in range(4):
                self.comb += res[c].eq(reduce(or_, [
                    Mux(s, cand[c], 0) for s, cand in zip(sel, cands)]))
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
        p1_p = Signal()
        p1_discard = Signal()
        p1_wr_msk = Signal(4, reset_less=True)
        p1_opcode = Signal(6, reset_less=True)
        p1_sat = Signal(reset_less=True)
        p1_sub_op = Signal(2, reset_less=True)
        p1_imm8 = Signal(8, reset_less=True)
        # Bits of S0[14:0] that reach bit 8 or above after SHL / SHR by
        # IMM8[3:0], so SAT overflow does not wait for the barrel shifter.
        p1_shl_ovf = Signal(15, reset_less=True)
        p1_shr_ovf = Signal(15, reset_less=True)

        for c in range(4):
            self.sync += [
                p1_s0[c].eq(s0_vec[c]),
                p1_s1[c].eq(s1_vec[c]),
                p1_s2[c].eq(s2_vec[c]),
                p1_src[c].eq(self.src_pix[c]),
                p1_dst[c].eq(self.dst_pix[c]),
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
            p1_shl_ovf.eq(Cat(*[1 if i >= 8 else imm8[0:4] >= 8 - i
                                for i in range(15)])),
            p1_shr_ovf.eq(Cat(*[imm8[0:4] <= i - 8 if i >= 8 else 0
                                for i in range(15)])),
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
        p2_p = Signal()
        p2_discard = Signal()
        p2_wr_msk = Signal(4, reset_less=True)
        p2_opcode = Signal(6, reset_less=True)
        p2_sat = Signal(reset_less=True)

        # Saturation is applied in P3: the clamp would otherwise land on the
        # flip-flop reset pin behind the 17-bit subtract and flag decode.
        p2_arith = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_logic = [Signal((16, True), reset_less=True) for _ in range(4)]
        p2_arith_sat = [Signal(2, reset_less=True) for _ in range(4)]
        p2_logic_sat = [Signal(2, reset_less=True) for _ in range(4)]
        # Multiplier operands are registered here without logic after them:
        # Gowin folds these registers into the DSP inputs, so the operand
        # select network must not feed the DSPs directly.
        p2_lerp_diff = [Signal((17, True), reset_less=True) for _ in range(4)]
        p2_lerp_w = [Signal(10, reset_less=True) for _ in range(4)]
        p2_match_ch = [Signal(reset_less=True) for _ in range(4)]
        p2_imm8 = Signal(8, reset_less=True)

        for c in range(4):
            s0 = p1_s0[c]
            s1 = p1_s1[c]
            s2 = p1_s2[c]

            add_val = Signal((17, True))
            sub_val = Signal((17, True))
            rsub_val = Signal((17, True))
            s0_lt_s1 = Signal()
            s0_eq_s1 = Signal()
            diff_val = Signal((17, True))
            min_val = Signal((17, True))
            max_val = Signal((17, True))
            clamp_val = Signal((17, True))
            shl_val = Signal((16, True))
            shr_val = Signal((16, True))

            self.comb += [
                add_val.eq(s0 + s1),
                sub_val.eq(s0 - s1),
                rsub_val.eq(s1 - s0),
                s0_lt_s1.eq(sub_val[16]),
                s0_eq_s1.eq(s0 == s1),
                diff_val.eq(Mux(s0_lt_s1, rsub_val, sub_val)),
                min_val.eq(Mux(s0_lt_s1, s0, s1)),
                max_val.eq(Mux(s0_lt_s1, s1, s0)),
                clamp_val.eq(Mux(s0_lt_s1, s1, Mux(s0 > s2, s2, s0))),
                shl_val.eq(s0 << p1_imm8[0:4]),
                shr_val.eq(s0 >> p1_imm8[0:4]),
            ]

            def sat_flags(val, sign):
                return Cat(val[sign], ~val[sign] & (val[8:sign] != 0))

            # Saturation selects two range flags alongside the raw result
            # instead of muxing six separately clamped values. DIFF/MIN/MAX
            # flags come from their candidates, in parallel with the compare.
            s0_fl = sat_flags(s0, 15)
            s1_fl = sat_flags(s1, 15)
            sub_fl = sat_flags(sub_val, 16)
            rsub_fl = sat_flags(rsub_val, 16)
            arith_raw = Signal((16, True))
            arith_flags = Signal(2)
            self.comb += Case(p1_opcode[0:3], {
                0: [arith_raw.eq(s0), arith_flags.eq(s0_fl)],
                1: [arith_raw.eq(add_val), arith_flags.eq(sat_flags(add_val, 16))],
                2: [arith_raw.eq(sub_val), arith_flags.eq(sub_fl)],
                3: [arith_raw.eq(diff_val),
                    arith_flags.eq(Mux(s0_lt_s1, rsub_fl, sub_fl))],
                4: [arith_raw.eq(min_val),
                    arith_flags.eq(Mux(s0_lt_s1, s0_fl, s1_fl))],
                5: [arith_raw.eq(max_val),
                    arith_flags.eq(Mux(s0_lt_s1, s1_fl, s0_fl))],
                6: [arith_raw.eq(clamp_val), arith_flags.eq(0)],
                "default": [arith_raw.eq(s0), arith_flags.eq(0)],
            })

            s0_pos = ~s0[15]
            logic_raw = Signal((16, True))
            logic_flags = Signal(2)
            self.comb += Case(p1_opcode[0:3], {
                0: [logic_raw.eq(s0 & s1), logic_flags.eq(0)],
                1: [logic_raw.eq(s0 | s1), logic_flags.eq(0)],
                2: [logic_raw.eq(s0 ^ s1), logic_flags.eq(0)],
                3: [logic_raw.eq(shl_val), logic_flags.eq(Cat(
                    s0[15], s0_pos & ((s0[0:15] & p1_shl_ovf) != 0)))],
                4: [logic_raw.eq(shr_val), logic_flags.eq(Cat(
                    s0[15], s0_pos & ((s0[0:15] & p1_shr_ovf) != 0)))],
                "default": [logic_raw.eq(s0 & s1), logic_flags.eq(0)],
            })

            self.sync += [
                p2_s0[c].eq(s0),
                p2_s1[c].eq(s1),
                p2_s2[c].eq(s2),
                p2_src[c].eq(p1_src[c]),
                p2_dst[c].eq(p1_dst[c]),
                p2_arith[c].eq(arith_raw),
                p2_logic[c].eq(logic_raw),
                p2_arith_sat[c].eq(Mux(p1_sat, arith_flags, 0)),
                p2_logic_sat[c].eq(Mux(p1_sat, logic_flags, 0)),
                p2_lerp_diff[c].eq(sub_val),
                p2_lerp_w[c].eq(s2[0:8] + (s2[0:8] >> 7)),
                p2_match_ch[c].eq(
                    Mux(p1_sub_op == 0, s0_eq_s1,
                    Mux(p1_sub_op == 1, ~s0_eq_s1,
                    Mux(p1_sub_op == 2, s0_lt_s1, ~s0_lt_s1)))
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

        # Pipeline Sub-stage 3 (Multiply)
        p3_prod16 = [Signal(16, reset_less=True) for _ in range(4)]
        p3_lerp_prod = [Signal((27, True), reset_less=True) for _ in range(4)]
        if lut_ports is not None:
            # LUT read ports register the address in P2 and return data
            # alongside the P2 registers.
            p3_lut = [Signal(8, reset_less=True) for _ in range(4)]
            for c in range(4):
                s0 = p1_s0[c]
                self.comb += lut_ports[c].adr.eq(
                    Mux(s0 < 0, 0, Mux(s0 > 255, 255, s0[0:8])))
                self.sync += p3_lut[c].eq(lut_ports[c].dat_r)

        p3_arith = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_logic = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_match_ch = [Signal(reset_less=True) for _ in range(4)]
        p3_s0 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_s1 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_s2 = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_r = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p3_we = [
            [Signal(reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p3_src = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_dst = [Signal((16, True), reset_less=True) for _ in range(4)]
        p3_p = Signal(reset_less=True)
        p3_discard = Signal(reset_less=True)
        p3_is_cmp = Signal(reset_less=True)
        p3_is_discard = Signal(reset_less=True)
        p3_wr_msk = Signal(4, reset_less=True)
        p3_opcode = Signal(6, reset_less=True)
        p3_sat = Signal(reset_less=True)
        p3_imm8 = Signal(8, reset_less=True)

        for c in range(4):
            self.sync += [
                p3_prod16[c].eq(p2_s0[c][0:8] * p2_s1[c][0:8]),
                p3_lerp_prod[c].eq(p2_lerp_diff[c] * p2_lerp_w[c]),
                p3_arith[c].eq(Mux(p2_arith_sat[c][0], 0,
                               Mux(p2_arith_sat[c][1], 255, p2_arith[c]))),
                p3_logic[c].eq(Mux(p2_logic_sat[c][0], 0,
                               Mux(p2_logic_sat[c][1], 255, p2_logic[c]))),
                p3_match_ch[c].eq(p2_match_ch[c]),
                p3_s0[c].eq(p2_s0[c]),
                p3_s1[c].eq(p2_s1[c]),
                p3_s2[c].eq(p2_s2[c]),
                p3_src[c].eq(p2_src[c]),
                p3_dst[c].eq(p2_dst[c]),
            ]
            for r in range(4):
                self.sync += [
                    p3_r[r][c].eq(p2_r[r][c]),
                    p3_we[r][c].eq(p2_we[r][c]),
                ]

        self.sync += [
            p3_p.eq(p2_p),
            p3_discard.eq(p2_discard),
            p3_is_cmp.eq(p2_is_cmp),
            p3_is_discard.eq(p2_is_discard),
            p3_wr_msk.eq(p2_wr_msk),
            p3_opcode.eq(p2_opcode),
            p3_sat.eq(p2_sat),
            p3_imm8.eq(p2_imm8),
        ]

        # Pipeline Sub-stage 4 (Post-multiply Math)
        p4_mult = [Signal((17, True), reset_less=True) for _ in range(4)]
        p4_mult_sat = Signal(reset_less=True)
        p4_arith = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_logic = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_alt = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_opcode = Signal(6, reset_less=True)
        p4_r = [
            [Signal((16, True), reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p4_we = [
            [Signal(reset_less=True) for _ in range(4)] for _ in range(4)
        ]
        p4_src = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_dst = [Signal((16, True), reset_less=True) for _ in range(4)]
        p4_p = Signal(reset_less=True)
        p4_discard = Signal(reset_less=True)
        p4_is_cmp = Signal(reset_less=True)
        p4_is_discard = Signal(reset_less=True)
        p4_all_match = Signal(reset_less=True)

        if stage_idx == 1:
            dot3_res = Signal(16)
            self.comb += dot3_res.eq(
                (p3_prod16[0] + p3_prod16[1] + p3_prod16[2] + 128) >> 8)

        for c in range(4):
            prod_raw = Signal(17)
            prod_unorm = Signal(16)
            muladd_res = Signal((17, True))
            mul_scaled_val = Signal(16)
            lerp_res = Signal((28, True))
            self.comb += [
                prod_raw.eq(p3_prod16[c] + 128),
                prod_unorm.eq((prod_raw + (prod_raw >> 8)) >> 8),
                muladd_res.eq(p3_s2[c] + prod_unorm),
                mul_scaled_val.eq(p3_prod16[c] >> p3_imm8[0:4]),
                lerp_res.eq((p3_lerp_prod[c] + (p3_s1[c] << 8) + 128) >> 8),
            ]

            # SAT on the multiply group only clamps upward, and the clamp is
            # decided from the stored 17-bit result in the writeback
            # sub-stage, so no compare follows the post-multiply adders.
            mult_raw = Signal((17, True))
            mult_cases = {
                0: mult_raw.eq(prod_unorm),
                1: mult_raw.eq(mul_scaled_val),
                2: mult_raw.eq(muladd_res),
                3: mult_raw.eq(lerp_res[0:16]),
                "default": mult_raw.eq(prod_unorm),
            }
            if stage_idx == 1:
                mult_cases[4] = mult_raw.eq(dot3_res)
            self.comb += Case(p3_opcode[0:3], mult_cases)

            self.sync += [
                p4_mult[c].eq(mult_raw),
                p4_arith[c].eq(p3_arith[c]),
                p4_logic[c].eq(p3_logic[c]),
                p4_alt[c].eq(p3_lut[c] if lut_ports is not None else p3_s0[c]),
            ]

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

        clamp_ops = (1, 2, 4) if stage_idx == 1 else (1, 2)
        self.sync += [
            p4_mult_sat.eq(p3_sat & reduce(or_, [p3_opcode[0:3] == k for k in clamp_ops])),
            p4_p.eq(p3_p),
            p4_discard.eq(p3_discard),
            p4_is_cmp.eq(p3_is_cmp),
            p4_is_discard.eq(p3_is_discard),
            p4_all_match.eq(all_match),
            p4_opcode.eq(p3_opcode),
            [p4_src[c].eq(p3_src[c]) for c in range(4)],
            [p4_dst[c].eq(p3_dst[c]) for c in range(4)],
        ]

        # Pipeline Sub-stage 5 (Result Muxing, Writeback & Outputs)
        for c in range(4):
            res_ch = Signal((16, True))
            self.comb += Case(p4_opcode[4:6], {
                0: res_ch.eq(p4_arith[c]),
                1: res_ch.eq(Mux(p4_mult_sat & (p4_mult[c] > 255), 255, p4_mult[c][0:16])),
                2: res_ch.eq(p4_logic[c]),
                3: res_ch.eq(p4_alt[c]),
            })
            for r in range(4):
                self.sync += [
                    If(p4_we[r][c],
                        self.r_out[r][c].eq(res_ch)
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
        ]


class BilinearFilter(LiteXModule):
    """2x2 texture filter built from the LERP primitive of the shader ISA.

    top = LERP(P10, P00, wu), bot = LERP(P11, P01, wu) and
    out = LERP(bot, top, wv), with LERP(S, D, w) = ((S - D) * w + (D << 8)
    + 128) >> 8 per channel and w the expanded 0..256 weight. Zero weights
    return P00 exactly. out follows the inputs by LATENCY cycles.
    """
    LATENCY = 6

    def __init__(self):
        self.p00 = Signal(32)
        self.p10 = Signal(32)
        self.p01 = Signal(32)
        self.p11 = Signal(32)
        self.wu = Signal(9)
        self.wv = Signal(9)
        self.out = Signal(32, reset_less=True)

        wu1 = Signal(9, reset_less=True)
        wv_d = [Signal(9, reset_less=True) for _ in range(4)]
        self.sync += [wu1.eq(self.wu), wv_d[0].eq(self.wv)]
        self.sync += [wv_d[i].eq(wv_d[i - 1]) for i in range(1, 4)]

        for c in range(4):
            sl = slice(8 * c, 8 * c + 8)
            # Differences and pass-through endpoints are registered with no
            # logic after them so they can sit in the DSP input registers.
            dt = Signal((9, True), reset_less=True)
            db = Signal((9, True), reset_less=True)
            e00 = Signal(8, reset_less=True)
            e01 = Signal(8, reset_less=True)
            self.sync += [
                dt.eq(self.p10[sl] - self.p00[sl]),
                db.eq(self.p11[sl] - self.p01[sl]),
                e00.eq(self.p00[sl]),
                e01.eq(self.p01[sl]),
            ]
            pt = Signal((18, True), reset_less=True)
            pb = Signal((18, True), reset_less=True)
            e00_q = Signal(8, reset_less=True)
            e01_q = Signal(8, reset_less=True)
            self.sync += [
                pt.eq(dt * wu1),
                pb.eq(db * wu1),
                e00_q.eq(e00),
                e01_q.eq(e01),
            ]
            # D << 8 is spelled as an explicit 16-bit Cat: Migen wraps the
            # unsigned term in a signed concatenation, where a Verilog shift
            # keeps its operand's 8-bit width and would drop D entirely.
            top_full = Signal((19, True))
            bot_full = Signal((19, True))
            top = Signal(8, reset_less=True)
            bot = Signal(8, reset_less=True)
            self.comb += [
                top_full.eq((pt + Cat(Constant(0, 8), e00_q) + 128) >> 8),
                bot_full.eq((pb + Cat(Constant(0, 8), e01_q) + 128) >> 8),
            ]
            self.sync += [top.eq(top_full[0:8]), bot.eq(bot_full[0:8])]

            dv = Signal((9, True), reset_less=True)
            top_q = Signal(8, reset_less=True)
            self.sync += [dv.eq(bot - top), top_q.eq(top)]
            pv = Signal((18, True), reset_less=True)
            top_qq = Signal(8, reset_less=True)
            self.sync += [pv.eq(dv * wv_d[3]), top_qq.eq(top_q)]
            out_full = Signal((19, True))
            self.comb += out_full.eq((pv + Cat(Constant(0, 8), top_qq) + 128) >> 8)
            self.sync += self.out[sl].eq(out_full[0:8])


class Gfx2DEngine(LiteXModule):
    def __init__(self, pal_port, lut_ports, ddr_size=1 << 30,
                 response_timeout=1 << 18):
        self.ui = DDR3UI()
        self.req = Signal()
        self.ack = Signal()
        self.done_err = Signal(32)
        self.desc = desc = [Signal(32) for _ in DESC_NAMES]
        self.busy = Signal(reset_less=True)

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
        scaled_comb = (op >= OP_SHADER) & scale_en
        scaled = Signal(reset_less=True)
        bil = Signal(reset_less=True)
        src_w_m1 = Signal(32, reset_less=True)
        src_h_m1 = Signal(32, reset_less=True)
        self.sync += [
            scaled.eq(scaled_comb),
            bil.eq(scaled_comb & bilinear_en),
            src_w_m1.eq(src_w - 1),
            src_h_m1.eq(src_h - 1),
        ]

        # Scaled tile width: the largest candidate W whose worst-case source
        # span ((W-1)*dU + 0xFFFF)[47:16] + BILINEAR + 1 fits the 1024-pixel
        # line buffer, i.e. (W-1)*dU <= (1023 - BILINEAR) << 16, which for
        # integers is dU <= floor(((1023 - BILINEAR) << 16) / (W-1)). W=1
        # always fits. Free-running from the descriptor like the other decodes.
        du = uv_du_dx
        w_cands = (1024, 1023, 1022, 512, 256, 128, 64, 32, 16)
        w_pass = []
        for w in w_cands:
            p = Signal(reset_less=True)
            self.sync += p.eq(Mux(bil, du <= ((1022 << 16) // (w - 1)),
                                  du <= ((1023 << 16) // (w - 1))))
            w_pass.append(p)
        w_pick = Constant(1, 11)
        for w, p in reversed(list(zip(w_cands, w_pass))):
            w_pick = Mux(p, w, w_pick)
        w_step = Signal(11, reset_less=True)
        self.sync += w_step.eq(Mux(scaled, w_pick, 1024))

        src_format = Signal(4)
        dst_format = Signal(4)
        # The descriptor is stable before start (see height_m1_q), so the
        # widely used 32-bit format decode is registered.
        fmt32 = Signal(reset_less=True)
        fmt32.attr.add(("syn_maxfan", 64))
        self.sync += fmt32.eq(fmt == FMT_XRGB8888)
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
            src_row_bytes.eq(Mux(scaled, src_w, width) << src_bpp_shift),
        ]
        # Row address limits are descriptor-derived and first read in
        # ROW_SETUP, several INIT states after start, so they free-run.
        src_row_limit = Signal(32, reset_less=True)
        src_blit_limit = Signal(32, reset_less=True)
        self.sync += [
            limit.eq(ddr_size - row_bytes),
            src_row_limit.eq(ddr_size - src_row_bytes),
            src_blit_limit.eq(ddr_size - blit_wb),
            src_limit.eq(Mux(op == OP_IMAGE_BLIT, src_blit_limit, src_row_limit)),
        ]

        delta_dst = Signal(32, reset_less=True)
        delta_src = Signal(32, reset_less=True)
        neg_src_stride = Signal(32, reset_less=True)
        neg_dst_stride = Signal(32, reset_less=True)
        nxt_row_start_reg = Signal(32, reset_less=True)
        nxt_src_row_start_reg = Signal(32, reset_less=True)
        beat_byte = Signal(32, reset_less=True)
        # Every write-send state leaves through a PREP state, so beat_byte
        # never advances on consecutive cycles and its increment is registered.
        nxt_beat_byte = Signal(32, reset_less=True)
        self.sync += nxt_beat_byte.eq(Cat(Constant(0, 5), beat_byte[5:32] + 1))
        rb_hi5 = Signal(16, reset_less=True)
        rb_lo5 = Signal(5, reset_less=True)
        # row_start changes only on the cycle before ROW_SETUP, so off5 is
        # valid from ROW_CHECK: fill_word is captured there and the row
        # geometry (one more register stage) is read in ROW_PASCAL.
        off5 = Signal(5, reset_less=True)
        self.sync += off5.eq(row_start[0:5])
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
        self.sync += [beats_w_reg.eq(beats_w),
                      first_mask_reg.eq(Cat(*first_bits)),
                      last_mask_reg.eq(Cat(*last_bits))]
        beats_left = Signal(16, reset_less=True)
        is_last_beat = Signal(reset_less=True)
        first_beat = Signal(reset_less=True)
        beat_mask = Signal(32)
        self.comb += beat_mask.eq(Mux(first_beat, first_mask,
                                     Mux(is_last_beat, last_mask, 0xffffffff)))
        src_beat_base = Signal(32, reset_less=True)
        # COPY, BLIT and tiled ops are exclusive, so one beat register
        # serves all of them.
        beat_buf = Signal(256, reset_less=True)
        b_byte_idx = Signal(5, reset_less=True)
        src_beat_loaded = Signal(reset_less=True)
        src_byte_off = Signal(32, reset_less=True)
        bytes_in_beat = Signal(8, reset_less=True)
        bytes_in_beat_zero = Signal(reset_less=True)
        src_bytes_left = Signal(16, reset_less=True)
        src_bytes_left_zero = Signal(reset_less=True)
        bit_buf = Signal(24, reset_less=True)
        bit_cnt = Signal(5, reset_less=True)

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
            Mux(fmt32, pat32_word, pat16_word)
        )
        fill_word = Signal(32, reset_less=True)
        fill_pattern = Signal(256)
        self.comb += fill_pattern.eq(Cat(*[fill_word] * 8))

        blit_pix_per_beat = Signal(5, reset_less=True)
        blit_expanded = Signal(256)
        blit_pix_16 = [Mux(bit_buf[23 - j], fg[:16], bg[:16]) for j in range(16)]
        blit_pix_32 = [Mux(bit_buf[23 - j], fg[:32], bg[:32]) for j in range(8)]
        self.comb += blit_expanded.eq(
            Mux(fmt32, Cat(*blit_pix_32), Cat(*blit_pix_16)))
        blit_exp_reg = Signal(256, reset_less=True)

        # Output DDR registers
        req_blk = Signal(26, reset_less=True)
        out_data = Signal(256, reset_less=True)
        out_mask = Signal(32, reset_less=True)
        is_rd_state = Signal()
        src_byte = Signal(8)
        self.comb += src_byte.eq(
            Array([beat_buf[i*8:(i+1)*8] for i in range(32)])[b_byte_idx]
        )
        src_byte_q = Signal(8)
        src_byte_pending = Signal()

        # Line Buffers. SRC_LB holds two source-row slots (slot = row[0]),
        # each split into even/odd column banks so a bilinear 2x2 fetch
        # reads both horizontal neighbours of a slot in one cycle.
        src_lb_w = [[None, None], [None, None]]
        src_lb_r = [[None, None], [None, None]]
        for k in range(2):
            for b, bank in enumerate("eo"):
                mem = Memory(32, 512, name="src_lb%d_%s" % (k, bank))
                src_lb_w[k][b] = mem.get_port(write_capable=True, clock_domain="ddr")
                src_lb_r[k][b] = mem.get_port(clock_domain="ddr")
                self.specials += mem, src_lb_w[k][b], src_lb_r[k][b]
        self.dst_lb = Memory(32, 1024, name="dst_lb")
        self.out_lb = Memory(33, 1024, name="out_lb")

        self.dst_lb_w = self.dst_lb.get_port(write_capable=True, clock_domain="ddr")
        self.dst_lb_r = self.dst_lb.get_port(clock_domain="ddr")

        self.out_lb_w = self.out_lb.get_port(write_capable=True, clock_domain="ddr")
        self.out_lb_r = self.out_lb.get_port(clock_domain="ddr")

        self.specials += [
            self.dst_lb, self.dst_lb_w, self.dst_lb_r,
            self.out_lb, self.out_lb_w, self.out_lb_r,
        ]

        # Tile FSM signals
        tile_x = Signal(16, reset_less=True)
        rem_w = Signal(16, reset_less=True)
        self.tile_w = tile_w = Signal(11, reset_less=True)
        lb_rem = Signal(11, reset_less=True)
        comp_rem = Signal(11, reset_less=True)
        comp_out_rem = Signal(11, reset_less=True)
        wb_rem = Signal(11, reset_less=True)
        tile_wb_done = Signal(reset_less=True)
        t_src_blk = Signal(26, reset_less=True)
        t_src_off = Signal(5, reset_less=True)
        t_dst_base_blk = Signal(26, reset_less=True)
        t_dst_blk = Signal(26, reset_less=True)
        # T_DST_RREQ is always entered at least one cycle after t_dst_blk
        # last changed, so its increment can be registered.
        t_dst_blk_p1 = Signal(26, reset_less=True)
        self.sync += t_dst_blk_p1.eq(t_dst_blk + 1)
        t_dst_off = Signal(5, reset_less=True)
        t_src_is_first = Signal(reset_less=True)
        t_dst_is_first = Signal(reset_less=True)
        sub_idx = Signal(6, reset_less=True)
        lb_wr_ptr = Signal(11, reset_less=True)
        lb_is_last_pix = Signal(reset_less=True)
        wb_is_last_pix = Signal(reset_less=True)

        def delayed(sig, n):
            for _ in range(n):
                q = Signal(len(sig), reset_less=True)
                self.sync += q.eq(sig)
                sig = q
            return sig

        # UV state of scaled ops: U(x) = U0 + x*dU and V(y) = V0 + y*dV as
        # 48-bit UQ32.16, V per row and U at each tile start. The tile-width
        # bound keeps the integer offset from U_first below 1024 inside a
        # tile, so a 27-bit accumulator of frac(U_first) + k*dU covers it.
        v_acc = Signal(48, reset_less=True)
        u_first = Signal(48, reset_less=True)
        u_acc = Signal(27, reset_less=True)
        # V advances once per row, so its next value adds in two 24-bit
        # halves over two cycles.
        v_lo = Signal(25, reset_less=True)
        v_hi = Signal(24, reset_less=True)
        self.sync += [
            v_lo.eq(v_acc[0:24] + uv_dv_dy[0:24]),
            v_hi.eq(v_acc[24:48] + uv_dv_dy[24:32] + v_lo[24]),
        ]
        v_nxt = Cat(v_lo[0:24], v_hi)

        # Per-tile source geometry is a free-running pipeline from tile_w,
        # u_first and v_acc; the tile FSM waits for it in T_SETUP_W.
        tw_m1 = Signal(10, reset_less=True)
        mu_a = Signal(10, reset_less=True)
        mu_b = Signal(32, reset_less=True)
        mu_lo = Signal(26, reset_less=True)
        mu_hi = Signal(26, reset_less=True)
        u_span = Signal(42, reset_less=True)
        u_last = Signal(48, reset_less=True)
        u_next = Signal(48, reset_less=True)
        uf_ge = Signal(reset_less=True)
        u_base = Signal(14, reset_less=True)
        u_end_raw = Signal(33, reset_less=True)
        u_end = Signal(14, reset_less=True)
        src_span_px = Signal(15, reset_less=True)
        span_bad = Signal(reset_less=True)
        span_is1 = Signal(reset_less=True)
        lim_raw = Signal(14, reset_less=True)
        lim_c = Signal(11, reset_less=True)
        # The 32-bit V clamp compare is split into registered halves.
        v_hi_gt = Signal(reset_less=True)
        v_hi_eq = Signal(reset_less=True)
        v_lo_ge = Signal(reset_less=True)
        v_ge = Signal()
        self.comb += v_ge.eq(v_hi_gt | (v_hi_eq & v_lo_ge))
        y0 = Signal(14, reset_less=True)
        y1 = Signal(14, reset_less=True)
        top_slot = Signal(reset_less=True)
        bot_slot = Signal(reset_less=True)
        wv = Signal(9, reset_less=True)
        ufi = u_first[16:48]
        vi = v_acc[16:48]
        fv = v_acc[8:16]
        self.sync += [
            tw_m1.eq(tile_w - 1),
            mu_a.eq(tw_m1),
            mu_b.eq(du),
            mu_lo.eq(mu_a * mu_b[0:16]),
            mu_hi.eq(mu_a * mu_b[16:32]),
            u_span.eq(mu_lo + (mu_hi << 16)),
            u_last.eq(u_first + u_span),
            u_next.eq(u_last + du),
            uf_ge.eq(ufi >= src_w_m1),
            u_base.eq(Mux(uf_ge, src_w_m1, ufi)[0:14]),
            u_end_raw.eq(u_last[16:48] + bil),
            u_end.eq(Mux(u_end_raw >= src_w_m1, src_w_m1, u_end_raw)[0:14]),
            src_span_px.eq(u_end - u_base + 1),
            span_bad.eq(src_span_px > 1024),
            span_is1.eq(src_span_px == 1),
            # Local index limit of the right edge clamp: pixels at or past
            # it sample SRC_WIDTH-1 and reuse it as their right neighbour.
            lim_raw.eq(src_w_m1[0:14] - u_base),
            lim_c.eq(Mux(scaled,
                     Mux(uf_ge, 0, Mux(lim_raw >= 1024, 1024, lim_raw)), 1024)),
            v_hi_gt.eq(vi[16:32] > src_h_m1[16:32]),
            v_hi_eq.eq(vi[16:32] == src_h_m1[16:32]),
            v_lo_ge.eq(vi[0:16] >= src_h_m1[0:16]),
            y0.eq(Mux(v_ge, src_h_m1, vi)[0:14]),
            y1.eq(Mux(bil & ~v_ge, y0 + 1, y0)),
            top_slot.eq(scaled & y0[0]),
            bot_slot.eq(scaled & y1[0]),
            wv.eq(Mux(bil, fv + (fv >> 7), 0)),
        ]

        # Source row cache tags, one per SRC_LB slot (slot = row[0]).
        tag_valid = [Signal() for _ in range(2)]
        tag_row = [Signal(14, reset_less=True) for _ in range(2)]
        tag_ub = [Signal(14, reset_less=True) for _ in range(2)]
        tag_ue = [Signal(14, reset_less=True) for _ in range(2)]

        def tag_hit(y):
            hits = [tag_valid[k] & (tag_row[k] == y) & (tag_ub[k] == u_base) &
                    (tag_ue[k] >= u_end) for k in range(2)]
            return Mux(y[0], hits[1], hits[0])
        hit0 = Signal(reset_less=True)
        hit1 = Signal(reset_less=True)
        need_y1 = Signal(reset_less=True)
        self.sync += [
            hit0.eq(tag_hit(y0)),
            hit1.eq(tag_hit(y1)),
            need_y1.eq(y1 != y0),
        ]

        # Source row address src_addr + y * SRC_STRIDE + (u_base << bpp),
        # free-running from load_y; the FSM waits for it in T_SC_ADDR.
        load_y = Signal(14, reset_less=True)
        load_slot = Signal(reset_less=True)
        my_a = Signal(14, reset_less=True)
        my_b = Signal(32, reset_less=True)
        my_lo = Signal(30, reset_less=True)
        my_hi = Signal(16, reset_less=True)
        y_off = Signal(32, reset_less=True)
        sc_row_addr = Signal(32, reset_less=True)
        sc_src_addr = Signal(32, reset_less=True)
        self.sync += [
            my_a.eq(load_y),
            my_b.eq(src_stride),
            my_lo.eq(my_a * my_b[0:16]),
            my_hi.eq(my_a * my_b[16:32]),
            y_off.eq(my_lo + (my_hi << 16)),
            sc_row_addr.eq(src_addr + y_off),
            sc_src_addr.eq(sc_row_addr + (u_base << src_bpp_shift)),
        ]
        setup_wait = Signal(4, reset_less=True)

        # Compute pipeline signals. The sampling front end adds FE_LAT
        # cycles ahead of Stage 0.
        fe_lat = 4 + BilinearFilter.LATENCY
        comp_idx = Signal(11, reset_less=True)
        comp_out_idx = Signal(11, reset_less=True)
        comp_pipe_vld = Signal(22 + fe_lat, reset_less=True)
        p0_src_pix = [Signal((16, True), reset_less=True) for _ in range(4)]
        p0_dst_pix = [Signal((16, True), reset_less=True) for _ in range(4)]
        p0_fetch_x = Signal(16, reset_less=True)
        p0_r3 = [Signal(16, reset_less=True) for _ in range(4)]

        # Shader ALUs
        self.stage0 = stage0 = ShaderALU(0)
        self.stage1 = stage1 = ShaderALU(1)
        self.stage2 = stage2 = ShaderALU(2, lut_ports=lut_ports)
        self.stage3 = stage3 = ShaderALU(3)
        stages = [stage0, stage1, stage2, stage3]
        for stage, inst in zip(stages, (inst0, inst1, inst2, inst3)):
            self.comb += [
                stage.inst.eq(inst),
                stage.last_stage.eq(last_stage),
            ]
        # Unpack uniforms to 4 channels each: 0=.b, 1=.g, 2=.r, 3=.a
        for u in range(8):
            u_raw = uniforms_raw[u]
            for stage in stages:
                self.comb += [stage.uniforms[u][c].eq(u_raw[8*c:8*(c+1)])
                              for c in range(4)]

        # Sampling front end, one pixel per cycle from comp_idx / u_acc:
        # A) local source index and U fraction, B) right edge clamp,
        # C) bank addresses, D) bank read, E) 2x2 neighbour select, then the
        # bilinear filter. Unscaled ops sample local index comp_idx with
        # zero weights, which returns P00 unchanged.
        fa_rel = Signal(11, reset_less=True)
        fa_fu = Signal(8, reset_less=True)
        fa_x = Signal(16, reset_less=True)
        self.sync += [
            fa_rel.eq(Mux(scaled, u_acc[16:27], comp_idx)),
            fa_fu.eq(Mux(scaled, u_acc[8:16], 0)),
            fa_x.eq(tile_x + comp_idx),
        ]
        fb_i0 = Signal(10, reset_less=True)
        fb_rc = Signal(reset_less=True)
        fb_fu = Signal(8, reset_less=True)
        fb_x = Signal(16, reset_less=True)
        self.sync += [
            fb_rc.eq(fa_rel >= lim_c),
            fb_i0.eq(Mux(fa_rel >= lim_c, lim_c, fa_rel)),
            fb_fu.eq(fa_fu),
            fb_x.eq(fa_x),
        ]
        # Local index i lives in bank i[0] at address i >> 1, so the pair
        # (i, i + 1) is even[(i + 1) >> 1] and odd[i >> 1].
        fc_eadr = Signal(9, reset_less=True)
        fc_oadr = Signal(9, reset_less=True)
        fc_odd = Signal(reset_less=True)
        fc_rc = Signal(reset_less=True)
        fc_fu = Signal(8, reset_less=True)
        fc_x = Signal(16, reset_less=True)
        fc_uc = Signal(14, reset_less=True)
        self.sync += [
            fc_eadr.eq((fb_i0 + 1) >> 1),
            fc_oadr.eq(fb_i0 >> 1),
            fc_odd.eq(fb_i0[0]),
            fc_rc.eq(fb_rc),
            fc_fu.eq(fb_fu),
            fc_x.eq(fb_x),
            fc_uc.eq(u_base + fb_i0),
        ]
        for k in range(2):
            self.comb += [
                src_lb_r[k][0].adr.eq(fc_eadr),
                src_lb_r[k][1].adr.eq(fc_oadr),
            ]
        fd_odd = Signal(reset_less=True)
        fd_rc = Signal(reset_less=True)
        fd_fu = Signal(8, reset_less=True)
        fd_x = Signal(16, reset_less=True)
        fd_uc = Signal(14, reset_less=True)
        self.sync += [
            fd_odd.eq(fc_odd),
            fd_rc.eq(fc_rc),
            fd_fu.eq(fc_fu),
            fd_x.eq(fc_x),
            fd_uc.eq(fc_uc),
        ]

        def neighbours(slot):
            even = Mux(slot, src_lb_r[1][0].dat_r, src_lb_r[0][0].dat_r)
            odd = Mux(slot, src_lb_r[1][1].dat_r, src_lb_r[0][1].dat_r)
            left = Mux(fd_odd, odd, even)
            return left, Mux(fd_rc, left, Mux(fd_odd, even, odd))
        top_l, top_r = neighbours(top_slot)
        bot_l, bot_r = neighbours(bot_slot)
        fe_p = [Signal(32, reset_less=True) for _ in range(4)]
        fe_wu = Signal(9, reset_less=True)
        fe_fu = Signal(8, reset_less=True)
        fe_x = Signal(16, reset_less=True)
        fe_uc = Signal(14, reset_less=True)
        self.sync += [
            fe_p[0].eq(top_l), fe_p[1].eq(top_r),
            fe_p[2].eq(bot_l), fe_p[3].eq(bot_r),
            fe_wu.eq(Mux(bil, fd_fu + (fd_fu >> 7), 0)),
            fe_fu.eq(fd_fu),
            fe_x.eq(fd_x),
            fe_uc.eq(fd_uc),
        ]
        self.bilinear = bilinear = BilinearFilter()
        self.comb += [
            bilinear.p00.eq(fe_p[0]), bilinear.p10.eq(fe_p[1]),
            bilinear.p01.eq(fe_p[2]), bilinear.p11.eq(fe_p[3]),
            bilinear.wu.eq(fe_wu), bilinear.wv.eq(wv),
        ]
        ff_x = delayed(fe_x, BilinearFilter.LATENCY)
        ff_uc = delayed(fe_uc, BilinearFilter.LATENCY)
        ff_fu = delayed(fe_fu, BilinearFilter.LATENCY)

        # Stage 0 inputs (with P0 pipeline register). The DST read trails
        # comp_idx by FE_LAT cycles so it lands with the filtered source.
        self.comb += self.dst_lb_r.adr.eq(delayed(comp_idx, fe_lat))
        src_lb_dat = bilinear.out
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
        # R3 = {V_frac, U_frac, V_clamp, U_clamp} on scaled ops and
        # {0, 0, Y, X} otherwise.
        self.sync += [
            p0_fetch_x.eq(ff_x),
            p0_r3[0].eq(Mux(scaled, ff_uc, ff_x)),
            p0_r3[1].eq(Mux(scaled, y0, row[0:16])),
            p0_r3[2].eq(Mux(scaled, ff_fu, 0)),
            p0_r3[3].eq(Mux(scaled, fv, 0)),
            [p0_src_pix[c].eq(Mux(uses_src, src_lb_ch[c], fg_ch[c]))
             for c in range(4)],
            [p0_dst_pix[c].eq(Mux(uses_dst, dst_lb_ch[c], bg_ch[c]))
             for c in range(4)],
        ]
        # FG/BG are static for the whole command, so every stage reads them
        # from the descriptor instead of carrying them down the pipeline.
        for stage in stages:
            for c in range(4):
                self.comb += [
                    stage.fg_pix[c].eq(fg_ch[c]),
                    stage.bg_pix[c].eq(bg_ch[c]),
                ]
        for c in range(4):
            self.comb += [
                stage0.src_pix[c].eq(p0_src_pix[c]),
                stage0.dst_pix[c].eq(p0_dst_pix[c]),
                stage0.r_in[0][c].eq(p0_src_pix[c]),
                stage0.r_in[1][c].eq(p0_dst_pix[c]),
            ]
        self.comb += [
            stage0.r_in[2][0].eq(p0_fetch_x[0:16]),
            stage0.r_in[2][1].eq(row[0:16]),
            stage0.r_in[2][2].eq(0),
            stage0.r_in[2][3].eq(0),
            [stage0.r_in[3][c].eq(p0_r3[c]) for c in range(4)],
            stage0.p_in.eq(0),
            stage0.discard_in.eq(0),
        ]

        # Stage k to Stage k+1 direct pipeline connection
        for prev, nxt in zip(stages[:-1], stages[1:]):
            for c in range(4):
                for r in range(4):
                    self.comb += nxt.r_in[r][c].eq(prev.r_out[r][c])
                self.comb += [
                    nxt.src_pix[c].eq(prev.src_out[c]),
                    nxt.dst_pix[c].eq(prev.dst_out[c]),
                ]
            self.comb += [
                nxt.p_in.eq(prev.p_out),
                nxt.discard_in.eq(prev.discard_out),
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

        self.fsm = fsm = OneHotFSM(reset_state='IDLE')
        self.sync += [local_desc[i].eq(desc[i]) for i in range(len(DESC_NAMES))]
        # Responses arrive only for this engine's single outstanding read,
        # whose wait state consumes them, so no state decode is needed on
        # the 256-bit capture enable.
        self.sync += If(rd_vld_buf, beat_buf.eq(self.ui.rd_data))
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
        # The COPY_AREA read address src_beat_base + beat * 32 advances with
        # beat, and the IMAGE_BLIT source step src_beat_base + 32 is
        # free-running: src_beat_base never steps on consecutive cycles.
        c_rd_base = Signal(32, reset_less=True)
        src_beat_inc = Signal(32, reset_less=True)
        self.sync += src_beat_inc.eq(src_beat_base + 32)
        rd_base = Signal(32)
        self.comb += rd_base.eq(Mux(fsm.ongoing('B_RD_PREP'), src_beat_base, c_rd_base))
        # HEIGHT is stable for several cycles before start is seen. Two
        # stages keep the decrement out of the INIT multiplier input
        # register that Gowin absorbs into the DSP.
        height_m1 = Signal(32, reset_less=True)
        height_m1_q = Signal(32, reset_less=True)
        self.sync += [height_m1.eq(height - 1), height_m1_q.eq(height_m1)]
        # Scaled ops read the whole SRC_WIDTH x SRC_HEIGHT texture.
        src_rows_m1 = Signal(32, reset_less=True)
        src_rows_m1_q = Signal(32, reset_less=True)
        self.sync += [src_rows_m1.eq(Mux(scaled, src_h_m1, height_m1)),
                      src_rows_m1_q.eq(src_rows_m1)]
        fsm.act('IDLE',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(self.busy, 0),
                If(start, NextValue(err, 0),
                   NextValue(self.busy, 1),
                   NextState('INIT')))

        src_span = Signal(32, reset_less=True)
        dst_span = Signal(32, reset_less=True)
        src_step_total = Signal(32, reset_less=True)
        dst_step_total = Signal(32, reset_less=True)
        row_start_down = Signal(32, reset_less=True)
        src_row_start_down = Signal(32, reset_less=True)
        width_bytes = Signal(32, reset_less=True)
        # Per-command row state loads here rather than on IDLE & start, so
        # start stays out of these wide registers' enables. Nothing reads
        # them before ROW_SETUP.
        fsm.act('INIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(row, 0), NextValue(rows_left, height),
                NextValue(last_row, height == 1), NextValue(beat, 0),
                NextValue(row_start, dst_addr),
                NextValue(src_row_start, src_addr),
                [NextValue(v, 0) for v in tag_valid],
                NextValue(src_step_total, src_rows_m1_q * src_stride),
                NextValue(dst_step_total, height_m1_q * dst_stride),
                NextValue(blit_pix_per_beat, Mux(fmt32, 8, 16)),
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
        dst_src_diff = Signal(32, reset_less=True)
        self.sync += dst_src_diff.eq(dst_addr - src_addr)
        self.comb += [
            cur_mem_overlap.eq(overlap_src_lt_dst & overlap_dst_lt_src),
            overlap_safe.eq((~scale_en) & (~bilinear_en) &
                            cmp_bpp_equal & cmp_stride_equal & cmp_width_le_1024),
            dir_down_step.eq(Mux(op == OP_COPY_AREA,
                                 cmp_stride_equal & cmp_addr_lt,
                             Mux(op >= OP_SHADER,
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
                          (src_addr < dst_addr) & (dst_src_diff < row_bytes)),
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

        # Scaled ops address source rows directly, so the whole texture is
        # bounds-checked once instead of per destination row.
        # src_step_total keeps only 32 bits of (SRC_HEIGHT-1) * SRC_STRIDE.
        # With SRC_HEIGHT <= 16384, a high half (rows * STRIDE[31:16]) below
        # ceil(ddr_size / 2^16) bounds the product below 2^32, so it did not
        # wrap; at or above it the texture is past the end of DDR anyway.
        assert ddr_size <= 1 << 31
        ext_rows = Signal(14, reset_less=True)
        ext_stride_hi = Signal(16, reset_less=True)
        ext_hi = Signal(30, reset_less=True)
        ext_big = Signal(reset_less=True)
        self.sync += [
            ext_rows.eq(src_rows_m1_q[0:14]),
            ext_stride_hi.eq(src_stride[16:32]),
            ext_hi.eq(ext_rows * ext_stride_hi),
            ext_big.eq(ext_hi >= (ddr_size + 0xFFFF) >> 16),
        ]
        scaled_src_oob = Signal(reset_less=True)
        fsm.act('INIT2B2',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(overlap_src_lt_dst, src_addr < dst_end),
                NextValue(overlap_dst_lt_src, dst_addr < src_end),
                NextValue(scaled_src_oob, scaled & (ext_big | (src_end > ddr_size))),
                NextState('INIT2C'))

        bad_blit_dst = Mux(fmt32,
                           (dst_addr[0:2] != 0) | (dst_stride[0:2] != 0),
                           (dst_addr[0] != 0) | (dst_stride[0] != 0))
        fmt_hi_bad = local_desc[10][4:32] != 0
        op_hi_bad = local_desc[0][4:32] != 0
        legacy_fmt_bad = (op < OP_SHADER) & ((fmt == FMT_RGB888) | (fmt > FMT_XRGB1555))
        dst_fmt_bad = fmt_hi_bad | (dst_format == FMT_RGB888) | \
                      (dst_format >= FMT_INDEX8) | legacy_fmt_bad
        src_fmt_bad = (src_format == FMT_RGB888) | (src_format >= FMT_MONO1)
        inst_bad = Signal(reset_less=True)
        inst_bad_terms = []
        for k, inst in enumerate((inst0, inst1, inst2, inst3)):
            inst_op = inst[58:64]
            misplaced = []
            if k != 1:
                misplaced.append(inst_op == 0x14)
            if k != 2:
                misplaced.append(inst_op == 0x30)
            inst_bad_terms.append((last_stage >= k) &
                                  reduce(or_, misplaced))
        self.sync += inst_bad.eq(reduce(or_, inst_bad_terms))
        # BILINEAR_EN => SCALE_EN => USES_SRC, and OP_SCALE_BLIT requires
        # SCALE_EN, so the datapath only ever sees consistent modes.
        src_dim_bad = (src_w == 0) | (src_w > 16384) | \
                      (src_h == 0) | (src_h > 16384)
        cfg_bad = (inst_bad |
                   ((op == OP_SCALE_BLIT) & ~scale_en) |
                   (scale_en & ~uses_src) |
                   (bilinear_en & ~scale_en) |
                   (scale_en & src_dim_bad))
        dim_bad = (width == 0) | (height == 0) | \
                  ((op >= OP_SHADER) & ((width > 16384) | (height > 16384)))

        cur_unequal_stride = (~cmp_stride_equal) & cur_mem_overlap
        cur_bad_shader = uses_src & cur_mem_overlap & (~overlap_safe)

        # Checks of the descriptor alone free-run; INIT2C only ORs them with
        # the comparisons registered during INIT2..INIT2B2.
        # Three register levels: local_desc settles at least five cycles
        # before INIT2C reads desc_bad (IDLE start, INIT .. INIT2B2).
        desc_bad = Signal(reset_less=True)
        desc_bad_flags = [Signal(reset_less=True) for _ in range(8)]
        desc_bad_terms = [Signal(reset_less=True) for _ in range(3)]
        f = desc_bad_flags
        self.sync += [
            f[0].eq((op > OP_SCALE_BLIT) | op_hi_bad),
            f[1].eq(dim_bad),
            f[2].eq(dst_fmt_bad),
            f[3].eq((src_addr[0:5] != dst_addr[0:5]) |
                    (src_stride[0:5] != dst_stride[0:5])),
            f[4].eq((fmt == FMT_RGB888) | (blit_wb > 65535) | bad_blit_dst),
            f[5].eq(cfg_bad),
            f[6].eq(uses_src & src_fmt_bad),
            f[7].eq(op >= OP_SHADER),
            desc_bad_terms[0].eq(f[0] | f[1] | f[2]),
            desc_bad_terms[1].eq(((op == OP_COPY_AREA) & f[3]) |
                                 ((op == OP_IMAGE_BLIT) & f[4])),
            desc_bad_terms[2].eq(f[7] & (f[5] | f[6])),
            desc_bad.eq(reduce(or_, desc_bad_terms)),
        ]
        static_bad = (
            desc_bad |
            cmp_dst_stride |
            ((op == OP_COPY_AREA) & (
                cmp_src_stride |
                cmp_right_overlap |
                cur_unequal_stride
            )) |
            ((op == OP_IMAGE_BLIT) & cmp_blit_wb) |
            ((op >= OP_SHADER) & (
                dst_align_bad |
                (uses_src & (cmp_src_stride | src_align_bad)) |
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
                ).Elif(scaled_src_oob,
                   NextValue(err, ERR_ADDR),
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
                          ((op != OP_FILL_RECT) & ~scaled &
                           (src_row_start > src_limit))),
                NextValue(nxt_row_start_reg, row_start + delta_dst),
                NextValue(nxt_src_row_start_reg, src_row_start + delta_src),
                NextState('ROW_CHECK'))
        fsm.act('ROW_CHECK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(fill_word, fill_word_comb),
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
                   NextValue(c_rd_base, src_row_start & ~Constant(31, 32)),
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
                                Mux(fmt32,
                                    row_start[2:5], row_start[1:5])),
                      NextState('B_FETCH'))
                .Elif(op >= OP_SHADER,
                      NextValue(tile_x, 0),
                      NextValue(rem_w, width),
                      NextValue(u_first, uv_start_u),
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
        # V loads one cycle after INIT / ROW_INC from registered strobes, so
        # the 48-bit enable does not fan out from the FSM state. Nothing
        # reads V before T_SETUP, and the next ROW_INC is rows away.
        v_init_q = Signal(reset_less=True)
        v_inc_q = Signal(reset_less=True)
        self.sync += [
            v_init_q.eq(fsm.ongoing('INIT')),
            v_inc_q.eq(fsm.ongoing('ROW_INC')),
            If(v_init_q, v_acc.eq(uv_start_v))
            .Elif(v_inc_q, v_acc.eq(v_nxt)),
        ]

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
                    If(is_last_beat,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextState('PREP')),
                ))

        fsm.act('C_RPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(req_blk, rd_base[5:31]),
                NextState('C_RSEND'))
        fsm.act('C_RSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(0),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Elif(self.ui.cmd_ready,
                    NextState('C_RWAIT'),
                ))
        fsm.act('C_RWAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(rd_vld_fsm,
                    NextState('C_WPREP'),
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ))
        fsm.act('C_WPREP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(req_blk, beat_byte[5:31]),
                NextValue(out_data, beat_buf),
                NextValue(out_mask, beat_mask),
                NextState('C_WSEND'))
        fsm.act('C_WSEND',
                self.ui.cmd_en.eq(1), self.ui.wr_data_en.eq(1),
                self.ui.wr_data_end.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH'),
                ).Elif(self.ui.cmd_ready & self.ui.wr_data_rdy,
                    If(is_last_beat,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextState('C_RPREP')),
                ))

        fsm.act('B_FETCH',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(bit_cnt >= blit_pix_per_beat, NextState('B_EXP'))
                .Elif(src_bytes_left_zero, NextState('B_EXP'))
                .Elif(bytes_in_beat_zero,
                      NextValue(src_beat_base, src_beat_inc),
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
                    NextState('B_RD_WAIT'),
                ))
        fsm.act('B_RD_WAIT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
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
                    NextValue(bit_buf,
                              Mux(fmt32, bit_buf << 8, bit_buf << 16)),
                    NextValue(bit_cnt,
                              Mux(bit_cnt > blit_pix_per_beat,
                                  bit_cnt - blit_pix_per_beat, 0)),
                    If(is_last_beat,
                        If(last_row, NextState('FINISH'))
                        .Else(NextState('ROW_INC')),
                    ).Else(NextState('B_FETCH')),
                ))

        # The response watchdog counts in every send/wait state and clears
        # on a command handshake or anywhere else, so it needs no enable
        # built from the per-state transitions.
        elapsed_count = Signal()
        elapsed_clear = Signal()
        wr_send = Signal()
        for s in (elapsed_count, elapsed_clear):
            s.attr.add(("syn_keep", 1))
        self.comb += [
            wr_send.eq(fsm.ongoing('SEND') | fsm.ongoing('C_WSEND') |
                       fsm.ongoing('B_WSEND') | fsm.ongoing('T_WB_SEND')),
            elapsed_count.eq(is_rd_state | wr_send |
                fsm.ongoing('C_RWAIT') | fsm.ongoing('B_RD_WAIT') |
                fsm.ongoing('T_SRC_RWAIT') | fsm.ongoing('T_DST_RWAIT')),
            elapsed_clear.eq(~elapsed_count | (self.ui.cmd_ready &
                (is_rd_state | (wr_send & self.ui.wr_data_rdy)))),
        ]
        self.sync += If(elapsed_clear, elapsed.eq(0)).Else(elapsed.eq(elapsed + 1))

        # The three write-send states advance the beat counters identically.
        # One kept enable from registered terms stops synthesis from folding
        # it into the timeout and request-block enables.
        beat_advance = Signal()
        beat_advance.attr.add(("syn_keep", 1))
        self.comb += beat_advance.eq(
            (fsm.ongoing('SEND') | fsm.ongoing('C_WSEND') | fsm.ongoing('B_WSEND')) &
            ~elapsed_timeout & self.ui.cmd_ready & self.ui.wr_data_rdy & ~is_last_beat)
        self.sync += If(beat_advance,
            beat.eq(beat + 1),
            c_rd_base.eq(c_rd_base + 32),
            beats_left.eq(beats_left - 1),
            is_last_beat.eq(beats_left == 2),
            first_beat.eq(0),
            beat_byte.eq(nxt_beat_byte))

        # OP_SHADER Tile FSM
        tile_is_last = Signal(reset_less=True)
        calc_t_w = Signal(16)
        self.comb += [
            calc_t_w.eq(Mux(rem_w > w_step, w_step, rem_w)),
        ]
        cur_src_byte = Signal(32)
        cur_dst_byte = Signal(32)
        self.comb += [
            cur_src_byte.eq(src_row_start + (tile_x << src_bpp_shift)),
            cur_dst_byte.eq(row_start + (tile_x << dst_bpp_shift)),
        ]
        t_src_addr_reg = Signal(32, reset_less=True)
        t_dst_addr_reg = Signal(32, reset_less=True)

        # Settle time of the free-running tile geometry (tile_w -> span
        # check) and of the row address pipeline (load_y -> sc_src_addr).
        geo_wait = 11
        addr_wait = 6
        fsm.act('T_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(tile_w, calc_t_w[0:11]),
                NextValue(tile_is_last, rem_w <= w_step),
                NextValue(t_src_addr_reg, cur_src_byte),
                NextValue(t_dst_addr_reg, cur_dst_byte),
                NextValue(setup_wait, geo_wait),
                If(scaled,
                    NextState('T_SETUP_W')
                ).Else(
                    NextState('T_SETUP2')
                ))

        fsm.act('T_SETUP_W',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(setup_wait, setup_wait - 1),
                If(setup_wait == 0, NextState('T_SETUP_CHK')))

        # The step choice already bounds every tile's span; a tile whose
        # exact span still exceeds the line buffer falls back to one pixel,
        # whose span is at most 1 + BILINEAR_EN.
        fsm.act('T_SETUP_CHK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(span_bad,
                    NextValue(tile_w, 1),
                    NextValue(tile_is_last, rem_w == 1),
                    NextValue(setup_wait, geo_wait),
                    NextState('T_SETUP_W')
                ).Else(
                    NextState('T_SETUP2')
                ))

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
                If(scaled,
                    NextState('T_SC_ROW')
                ).Elif(uses_src,
                    NextState('T_SRC_RREQ')
                ).Elif(uses_dst,
                    NextState('T_DST_START')
                ).Else(
                    NextState('T_COMP_START')
                ))

        # Scaled source rows: fetch y0 and (bilinear) y1 into their slots
        # unless the slot tag already covers this tile's [u_base, u_end].
        fsm.act('T_SC_ROW',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(setup_wait, addr_wait),
                If(~hit0,
                    NextValue(load_y, y0),
                    NextValue(load_slot, y0[0]),
                    NextState('T_SC_ADDR')
                ).Elif(need_y1 & ~hit1,
                    NextValue(load_y, y1),
                    NextValue(load_slot, y1[0]),
                    NextState('T_SC_ADDR')
                ).Elif(uses_dst,
                    NextState('T_DST_START')
                ).Else(
                    NextState('T_COMP_START')
                ))

        fsm.act('T_SC_ADDR',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(setup_wait, setup_wait - 1),
                If(setup_wait == 0, NextState('T_SC_ADDR2')))

        fsm.act('T_SC_ADDR2',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(t_src_blk, sc_src_addr[5:31]),
                NextValue(t_src_off, sc_src_addr[0:5]),
                NextValue(t_src_is_first, 1),
                NextValue(req_blk, sc_src_addr[5:31]),
                NextValue(lb_wr_ptr, 0),
                NextValue(lb_rem, src_span_px[0:11]),
                NextValue(lb_is_last_pix, span_is1),
                NextState('T_SRC_RREQ'))

        # Tags change here; T_SC_TAG2 lets hit0/hit1 pick them up before
        # T_SC_ROW decides on the next row.
        fsm.act('T_SC_TAG',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                [If(load_slot == k,
                    NextValue(tag_valid[k], 1),
                    NextValue(tag_row[k], load_y),
                    NextValue(tag_ub[k], u_base),
                    NextValue(tag_ue[k], u_end))
                 for k in range(2)],
                NextState('T_SC_TAG2'))

        fsm.act('T_SC_TAG2',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextState('T_SC_ROW'))

        fsm.act('T_DST_START',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(lb_wr_ptr, 0),
                NextValue(lb_rem, tile_w),
                NextValue(lb_is_last_pix, tile_w == 1),
                NextValue(req_blk, t_dst_blk),
                NextState('T_DST_RREQ'))

        fsm.act('T_SRC_RREQ',
                self.ui.cmd.eq(1), self.ui.cmd_en.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Elif(self.ui.cmd_ready,
                    NextValue(t_src_blk, t_src_blk + 1),
                    NextValue(sub_idx,
                              Mux(t_src_is_first, t_src_off >> src_bpp_shift, 0)),
                    NextValue(t_src_is_first, 0),
                    NextState('T_SRC_RWAIT')
                ))

        fsm.act('T_SRC_RWAIT',
                self.ui.cmd.eq(0), self.ui.cmd_en.eq(0),
                If(rd_vld_fsm,
                    NextState('T_SRC_UNPACK')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ))

        # Unpack logic for SRC
        src_p32 = Signal(32)
        src_p16 = Signal(16)
        src_argb = Signal(32)
        src_last_sub = Signal(6, reset_less=True)
        self.sync += src_last_sub.eq(
            Mux(src_bpp_shift == 2, 7, Mux(src_bpp_shift == 1, 15, 31)))
        self.comb += [
            src_p32.eq(Array([beat_buf[i*32:(i+1)*32]
                           for i in range(8)])[sub_idx[0:3]]),
            src_p16.eq(Array([beat_buf[i*16:(i+1)*16]
                           for i in range(16)])[sub_idx[0:4]]),
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

        # SRC line-buffer writes trail T_SRC_UNPACK by two cycles: one for
        # the PAL_RAM read and one to register its output before the LB.
        src_wr_req = Signal()
        src_p8 = Signal(8)
        s1_vld = Signal(reset_less=True)
        s1_ptr = Signal(11, reset_less=True)
        s1_dat = Signal(32, reset_less=True)
        s1_pal = Signal(reset_less=True)
        s1_slot = Signal(reset_less=True)
        s2_vld = Signal(reset_less=True)
        s2_ptr = Signal(11, reset_less=True)
        s2_dat = Signal(32, reset_less=True)
        s2_slot = Signal(reset_less=True)
        self.comb += [
            src_p8.eq(Array([beat_buf[i*8:(i+1)*8]
                             for i in range(32)])[sub_idx[0:5]]),
            pal_port.adr.eq(src_p8),
        ]
        for k in range(2):
            for b in range(2):
                self.comb += [
                    src_lb_w[k][b].we.eq(s2_vld & (s2_slot == k) & (s2_ptr[0] == b)),
                    src_lb_w[k][b].adr.eq(s2_ptr >> 1),
                    src_lb_w[k][b].dat_w.eq(s2_dat),
                ]
        self.sync += [
            s1_vld.eq(src_wr_req),
            s1_ptr.eq(lb_wr_ptr),
            s1_dat.eq(src_argb),
            s1_pal.eq(src_format == FMT_INDEX8),
            s1_slot.eq(scaled & load_slot),
            s2_vld.eq(s1_vld),
            s2_ptr.eq(s1_ptr),
            s2_dat.eq(Mux(s1_pal, pal_port.dat_r, s1_dat)),
            s2_slot.eq(s1_slot),
        ]

        fsm.act('T_SRC_UNPACK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                src_wr_req.eq(1),
                If(lb_is_last_pix,
                    NextValue(lb_wr_ptr, 0),
                    NextValue(lb_rem, tile_w),
                    NextValue(lb_is_last_pix, tile_w == 1),
                    If(scaled,
                        NextState('T_SC_TAG')
                    ).Elif(uses_dst,
                        NextValue(req_blk, t_dst_blk),
                        NextState('T_DST_RREQ')
                    ).Else(NextState('T_SRC_DRAIN'))
                ).Else(
                    NextValue(lb_wr_ptr, lb_wr_ptr + 1),
                    NextValue(lb_rem, lb_rem - 1),
                    NextValue(sub_idx, sub_idx + 1),
                    NextValue(lb_is_last_pix, lb_rem == 2),
                    If(sub_idx == src_last_sub,
                        NextValue(req_blk, t_src_blk),
                        NextState('T_SRC_RREQ')
                    )
                ))

        # Lets the last trailing SRC LB write land before compute reads it.
        fsm.act('T_SRC_DRAIN',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextState('T_COMP_START'))

        # DST prefetch
        fsm.act('T_DST_RREQ',
                self.ui.cmd.eq(1), self.ui.cmd_en.eq(1),
                If(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ).Elif(self.ui.cmd_ready,
                    NextValue(t_dst_blk, t_dst_blk_p1),
                    NextValue(sub_idx,
                              Mux(t_dst_is_first, t_dst_off >> dst_bpp_shift, 0)),
                    NextValue(t_dst_is_first, 0),
                    NextState('T_DST_RWAIT')
                ))

        fsm.act('T_DST_RWAIT',
                self.ui.cmd.eq(0), self.ui.cmd_en.eq(0),
                If(rd_vld_fsm,
                    NextState('T_DST_UNPACK')
                ).Elif(elapsed_timeout,
                    NextValue(err, ERR_TIMEOUT), NextState('FINISH')
                ))

        dst_p32 = Signal(32)
        dst_p16 = Signal(16)
        dst_argb = Signal(32)
        dst_last_sub = Signal(6, reset_less=True)
        self.sync += dst_last_sub.eq(
            Mux(dst_bpp_shift == 2, 7, Mux(dst_bpp_shift == 1, 15, 31)))
        self.comb += [
            dst_p32.eq(Array([beat_buf[i*32:(i+1)*32]
                           for i in range(8)])[sub_idx[0:3]]),
            dst_p16.eq(Array([beat_buf[i*16:(i+1)*16]
                           for i in range(16)])[sub_idx[0:4]]),
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

        # DST line-buffer writes trail T_DST_UNPACK by one cycle; compute
        # reads DST_LB no earlier than T_COMP_STREAM, after the last write.
        dst_wr_req = Signal()
        d1_vld = Signal(reset_less=True)
        d1_ptr = Signal(11, reset_less=True)
        d1_dat = Signal(32, reset_less=True)
        self.sync += [
            d1_vld.eq(dst_wr_req),
            d1_ptr.eq(lb_wr_ptr),
            d1_dat.eq(dst_argb),
        ]
        self.comb += [
            self.dst_lb_w.we.eq(d1_vld),
            self.dst_lb_w.adr.eq(d1_ptr),
            self.dst_lb_w.dat_w.eq(d1_dat),
        ]

        fsm.act('T_DST_UNPACK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                dst_wr_req.eq(1),
                If(lb_is_last_pix,
                    NextValue(lb_wr_ptr, 0),
                    NextState('T_COMP_START')
                ).Else(
                    NextValue(lb_wr_ptr, lb_wr_ptr + 1),
                    NextValue(lb_rem, lb_rem - 1),
                    NextValue(sub_idx, sub_idx + 1),
                    NextValue(lb_is_last_pix, lb_rem == 2),
                    If(sub_idx == dst_last_sub,
                        NextValue(req_blk, t_dst_blk),
                        NextState('T_DST_RREQ')
                    )
                ))

        # Streaming compute
        comp_active = Signal(reset_less=True)
        comp_out_last = Signal(reset_less=True)
        out_r0_sat = [Signal(8) for _ in range(4)]
        for c in range(4):
            r0 = stage3.r_out[0][c]
            self.comb += out_r0_sat[c].eq(
                Mux(r0 < 0, 0, Mux(r0 > 255, 255, r0[0:8])))
        fsm.act('T_COMP_START',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(comp_idx, 0),
                NextValue(comp_out_idx, 0),
                NextValue(comp_pipe_vld, 0),
                NextValue(comp_active, 1),
                NextValue(comp_rem, tile_w),
                NextValue(comp_out_rem, tile_w),
                NextValue(comp_out_last, tile_w == 1),
                NextValue(u_acc, u_first[0:16]),
                NextState('T_COMP_STREAM'))

        pipe_top = len(comp_pipe_vld) - 1
        fsm.act('T_COMP_STREAM',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                If(comp_active,
                    NextValue(comp_idx, comp_idx + 1),
                    NextValue(comp_rem, comp_rem - 1),
                    NextValue(u_acc, u_acc + du[0:27]),
                    NextValue(comp_pipe_vld,
                              Cat(Constant(1, 1), comp_pipe_vld[0:pipe_top])),
                    If(comp_rem == 1,
                        NextValue(comp_active, 0)
                    )
                ).Else(
                    NextValue(comp_pipe_vld,
                              Cat(Constant(0, 1), comp_pipe_vld[0:pipe_top])),
                ),

                If(comp_pipe_vld[pipe_top],
                    self.out_lb_w.we.eq(1),
                    self.out_lb_w.adr.eq(comp_out_idx),
                    self.out_lb_w.dat_w.eq(Cat(
                        *out_r0_sat, stage3.discard_out
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
        # OUT_LB data must be valid in T_WB_READ so the packed pixel can be
        # registered before T_WB_PACK; present wb_idx's next value early.
        wb_idx_p1 = Signal(11, reset_less=True)
        self.comb += [
            wb_x.eq(tile_x + wb_idx),
            wb_thresh.eq(
                Array([Constant(v, 4) for v in bayer_matrix])[
                    Cat(wb_x[0:2], row[0:2])]),
            If(fsm.ongoing('T_WB_SETUP'),
                self.out_lb_r.adr.eq(0)
            ).Elif(fsm.ongoing('T_WB_PACK'),
                self.out_lb_r.adr.eq(wb_idx_p1)
            ).Else(
                self.out_lb_r.adr.eq(wb_idx)
            ),
        ]

        fsm.act('T_WB_SETUP',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(wb_idx, 0),
                NextValue(wb_idx_p1, 1),
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

        dith_g_off = Signal(4)
        # Saturate on the carry of one 9-bit sum rather than a second compare.
        sum_r = Signal(9)
        sum_g = Signal(9)
        sum_b = Signal(9)
        self.comb += [
            dith_g_off.eq(Mux(dst_format == FMT_RGB565,
                              wb_thresh >> 2, wb_thresh >> 1)),
            sum_r.eq(out_r + (wb_thresh >> 1)),
            sum_g.eq(out_g + dith_g_off),
            sum_b.eq(out_b + (wb_thresh >> 1)),
        ]

        # Only the 16-bit formats are dithered. The channels are registered
        # straight after OUT_LB in T_WB_READ; T_WB_PACK packs them under the
        # static format select.
        dith16 = Signal(reset_less=True)
        ch_r = Signal(8, reset_less=True)
        ch_g = Signal(8, reset_less=True)
        ch_b = Signal(8, reset_less=True)
        ch_a = Signal(8, reset_less=True)
        out_disc_q = Signal(reset_less=True)
        self.sync += [
            dith16.eq(dither_en & ((dst_format == FMT_RGB565) |
                                   (dst_format == FMT_XRGB1555) |
                                   (dst_format == FMT_ARGB1555))),
            ch_r.eq(Mux(dith16, Mux(sum_r[8], 255, sum_r[0:8]), out_r)),
            ch_g.eq(Mux(dith16, Mux(sum_g[8], 255, sum_g[0:8]), out_g)),
            ch_b.eq(Mux(dith16, Mux(sum_b[8], 255, sum_b[0:8]), out_b)),
            ch_a.eq(out_a),
            out_disc_q.eq(out_disc),
        ]

        packed_pix = Signal(32)
        pix_bytes = Signal(4)
        self.comb += [
            If(dst_format == FMT_RGB565,
                packed_pix.eq(Cat(ch_b[3:8], ch_g[2:8], ch_r[3:8])),
                pix_bytes.eq(2)
            ).Elif(dst_format == FMT_XRGB1555,
                packed_pix.eq(
                    Cat(ch_b[3:8], ch_g[3:8], ch_r[3:8], Constant(1, 1))),
                pix_bytes.eq(2)
            ).Elif(dst_format == FMT_ARGB1555,
                packed_pix.eq(
                    Cat(ch_b[3:8], ch_g[3:8], ch_r[3:8], ch_a[7])),
                pix_bytes.eq(2)
            ).Elif(dst_format == FMT_ARGB8888,
                packed_pix.eq(Cat(ch_b, ch_g, ch_r, ch_a)),
                pix_bytes.eq(4)
            ).Else(
                packed_pix.eq(Cat(ch_b, ch_g, ch_r, Constant(0xFF, 8))),
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
                        If(~out_disc_q, NextValue(out_mask[s:s+4], 0xF))
                    ).Else(
                        NextValue(out_data[s*8:(s+2)*8], packed_pix[0:16]),
                        If(~out_disc_q, NextValue(out_mask[s:s+2], 0x3))
                    )
                ]
            else:
                wb_cases[s] = [
                    NextValue(out_data[s*8:(s+2)*8], packed_pix[0:16]),
                    If(~out_disc_q, NextValue(out_mask[s:s+2], 0x3))
                ]

        fsm.act('T_WB_PACK',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                Case(byte_slot, wb_cases),
                NextValue(byte_slot, byte_slot + pix_bytes),
                NextValue(wb_idx, wb_idx + 1),
                NextValue(wb_idx_p1, wb_idx_p1 + 1),
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
                NextValue(req_blk, cur_wb_blk),
                NextValue(nxt_cur_wb_blk, cur_wb_blk + 1),
                NextState('T_WB_SEND'))

        # out_mask last changes in T_WB_PACK; T_WB_PREP lets this settle so
        # the arbiter request does not pass through the 32-bit compare.
        out_mask_zero = Signal(reset_less=True)
        self.sync += out_mask_zero.eq(out_mask == 0)
        fsm.act('T_WB_SEND',
                If(out_mask_zero,
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
                        NextValue(cur_wb_blk, nxt_cur_wb_blk),
                        NextValue(out_mask, 0),
                        NextValue(byte_slot, 0),
                        If(tile_wb_done,
                            NextState('T_TILE_NEXT')
                        ).Else(
                            NextState('T_WB_READ')
                        )
                    )
                ))

        fsm.act('T_TILE_NEXT',
                self.ui.cmd_en.eq(0), self.ui.wr_data_en.eq(0),
                NextValue(tile_x, tile_x + tile_w),
                NextValue(rem_w, rem_w - tile_w),
                NextValue(u_first, u_next),
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

        # PAL_RAM expands INDEX8 sources; LUT_RAM is one 256x8 table per
        # channel (0=.b .. 3=.a) so Stage 2 can look up 4 indices per cycle.
        self.pal_ram = pal_ram = Memory(32, 256, name="gfx2d_pal_ram")
        self.pal_w = pal_w = pal_ram.get_port(write_capable=True, clock_domain="sys")
        self.pal_r = pal_r = pal_ram.get_port(clock_domain="ddr")
        self.specials += pal_ram, pal_w, pal_r
        self.lut_rams = []
        lut_ws, lut_rs = [], []
        for c in range(4):
            lut_ram = Memory(8, 256, name="gfx2d_lut_ram%d" % c)
            lut_w = lut_ram.get_port(write_capable=True, clock_domain="sys")
            lut_r = lut_ram.get_port(clock_domain="ddr")
            self.specials += lut_ram, lut_w, lut_r
            self.lut_rams.append(lut_ram)
            lut_ws.append(lut_w)
            lut_rs.append(lut_r)

        self.engine = engine = ClockDomainsRenamer('ddr')(
            Gfx2DEngine(pal_r, lut_rs, ddr_size=ddr_size)
        )
        for i, name in enumerate(DESC_NAMES):
            self.comb += engine.desc[i].eq(desc[name])
        # The arbiter qualifies rd_data_valid with its ownership state; one
        # register keeps that cone off the 256-bit beat capture enables.
        rd_data_q = Signal(256, reset_less=True)
        rd_valid_q = Signal()
        self.sync.ddr += [
            rd_data_q.eq(self.ui.rd_data),
            rd_valid_q.eq(self.ui.rd_data_valid),
        ]
        self.comb += [
            engine.ui.cmd_ready.eq(self.ui.cmd_ready),
            engine.ui.wr_data_rdy.eq(self.ui.wr_data_rdy),
            engine.ui.rd_data.eq(rd_data_q),
            engine.ui.rd_data_valid.eq(rd_valid_q),
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

        # The fabric holds a transaction for at least one cycle before its
        # single commit cycle, so writes decode from local copies and the
        # register enables do not hang off the shared APB address.
        off_d = Signal(12, reset_less=True)
        wdata_d = Signal(32, reset_less=True)
        write_d = Signal(reset_less=True)
        self.sync += [off_d.eq(self.off), wdata_d.eq(self.wdata),
                      write_d.eq(self.selected & self.write & self.ok)]
        go = self.commit & write_d

        self.sync += self.irq.eq(self.irq_pending & self.irq_enable)

        self.sync += If(complete,
                        ack_seen.eq(ack_sync[1]), self.busy.eq(0), self.done.eq(1),
                        self.error.eq(engine.done_err != 0),
                        self.last_error.eq(engine.done_err),
                        self.irq_pending.eq(1))

        busy_write_err = Signal()
        self.comb += busy_write_err.eq(
            go & self.busy &
            (off_d != reg['IRQ_ENABLE']) &
            (off_d != reg['IRQ_STATUS']) &
            (off_d != reg['CLEAR_ERRORS'])
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

        # Table writes are registered so the APB decode does not fan out
        # straight into five BSRAM write ports.
        lut_wr_act = go & (off_d == reg['LUT_DATA']) & (~self.busy)
        pal_we_q = Signal()
        lut_we_q = Signal()
        tbl_adr_q = Signal(8, reset_less=True)
        tbl_dat_q = Signal(32, reset_less=True)
        self.sync += [
            pal_we_q.eq(lut_wr_act & desc['LUT_ADDR'][8]),
            lut_we_q.eq(lut_wr_act & (~desc['LUT_ADDR'][8])),
            tbl_adr_q.eq(desc['LUT_ADDR'][0:8]),
            tbl_dat_q.eq(wdata_d),
        ]
        self.comb += [
            pal_w.we.eq(pal_we_q),
            pal_w.adr.eq(tbl_adr_q),
            pal_w.dat_w.eq(tbl_dat_q),
        ]
        for c, lut_w in enumerate(lut_ws):
            self.comb += [
                lut_w.we.eq(lut_we_q),
                lut_w.adr.eq(tbl_adr_q),
                lut_w.dat_w.eq(tbl_dat_q[8*c:8*(c+1)]),
            ]

        desc_cases = {
            reg[name]: [If(~self.busy, sig.eq(wdata_d))]
            for name, sig in desc.items()
        }
        self.sync += If(go, Case(off_d, {
            reg['CONTROL']: [
                If(~self.busy,
                   self.commands.eq(self.commands + 1), self.busy.eq(1),
                   self.done.eq(0), self.error.eq(0), self.last_error.eq(ERR_NONE),
                   self.req.eq(~self.req))],
            reg['IRQ_ENABLE']: self.irq_enable.eq(wdata_d[0]),
            reg['IRQ_STATUS']: If(wdata_d[0], self.irq_pending.eq(0)),
            reg['CLEAR_ERRORS']: [
                If(wdata_d[0], self.done.eq(0), self.error.eq(0),
                   self.last_error.eq(ERR_NONE))],
            reg['LUT_DATA']: [
                If(~self.busy, desc['LUT_ADDR'][0:8].eq(desc['LUT_ADDR'][0:8] + 1))],
            **desc_cases,
        }))
