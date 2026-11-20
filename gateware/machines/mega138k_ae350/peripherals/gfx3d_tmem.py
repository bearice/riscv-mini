"""Stage 2 (Mode 7 + 4-Bank 2D TMEM) gateware for mega138k_ae350 GFX3D.

Implements:
- Tmem4Bank: 4-bank 2D texture memory organized by {v[0], u[0]} parity
  (Bank_EE=00, Bank_EO=01, Bank_OE=10, Bank_OO=11) for conflict-free 2x2
  quad reads at II=1.
- TmemSampler: Signed 48-bit Q32.16 UV corner extraction, independent 4-corner
  WRAP_REPEAT / WRAP_CLAMP / WRAP_BORDER_DISCARD, bounds_discard, and
  post-read crossbar (including same-bank broadcast for clamped edges and
  BG_COLOR substitution for out-of-bounds border corners).
- Mode7ScanlineStepper: Signed 48-bit Q32.16 bidirectional DDA scanline
  walker fed from 32-bit Q16.16 per-scanline parameters (U0, V0, dU_dx, dV_dx).
"""
from migen import *

WRAP_REPEAT = 0
WRAP_CLAMP = 1
WRAP_BORDER_DISCARD = 2


def _sext48_from_32(sig32):
    """Sign-extend a 32-bit Q16.16 value to 48-bit signed Q32.16."""
    return Cat(sig32, Replicate(sig32[31], 16))


def _bank_row_offset(v_half, log2_w):
    """Compute v_half * max(1, 2^(log2_w - 1)) without negative shift counts."""
    out = Signal(12)
    cases = {0: out.eq(v_half)}
    for k in range(1, 9):
        cases[k] = out.eq(v_half << (k - 1))
    cases["default"] = out.eq(v_half << 7)
    return out, Case(log2_w, cases)


class Tmem4Bank(Module):
    """4-Bank 2D Texture Memory (default 16KB = 4 x 1024 x 32-bit words).

    Bank index is {v[0], u[0]}:
      0 = Bank_EE (u[0]=0, v[0]=0)
      1 = Bank_EO (u[0]=1, v[0]=0)
      2 = Bank_OE (u[0]=0, v[0]=1)
      3 = Bank_OO (u[0]=1, v[0]=1)
    """

    def __init__(self, depth_per_bank=1024, clock_domain="ddr"):
        self.depth_per_bank = depth_per_bank
        self.adr_bits = (depth_per_bank - 1).bit_length()

        # Write port (addressed by texel (u, v) + base_offset + log2_tex_w)
        self.wr_en = Signal()
        self.wr_u = Signal(12)
        self.wr_v = Signal(12)
        self.wr_log2_w = Signal(4)
        self.wr_base = Signal(12)
        self.wr_dat = Signal(32)

        # 4 parallel read ports (one per parity bank)
        self.rd_re = Signal(reset=1)
        self.rd_adr = [Signal(self.adr_bits) for _ in range(4)]
        self.rd_dat = [Signal(32) for _ in range(4)]

        self.banks = [Memory(32, depth_per_bank) for _ in range(4)]
        self.specials += self.banks

        wr_ports = [
            m.get_port(write_capable=True, clock_domain=clock_domain)
            for m in self.banks
        ]
        rd_ports = [
            m.get_port(has_re=True, clock_domain=clock_domain)
            for m in self.banks
        ]
        self.specials += wr_ports + rd_ports

        wr_bank = Signal(2)
        wr_bank_adr = Signal(self.adr_bits)
        wr_v_half = Signal(11)
        wr_u_half = Signal(11)
        wr_row_off, wr_row_case = _bank_row_offset(wr_v_half, self.wr_log2_w)
        self.comb += [
            wr_bank.eq(Cat(self.wr_u[0], self.wr_v[0])),
            wr_v_half.eq(self.wr_v[1:12]),
            wr_u_half.eq(self.wr_u[1:12]),
            wr_row_case,
            wr_bank_adr.eq((self.wr_base >> 2) + wr_row_off + wr_u_half),
        ]

        for b in range(4):
            self.comb += [
                wr_ports[b].adr.eq(wr_bank_adr),
                wr_ports[b].dat_w.eq(self.wr_dat),
                wr_ports[b].we.eq(self.wr_en & (wr_bank == b)),
                rd_ports[b].adr.eq(self.rd_adr[b]),
                rd_ports[b].re.eq(self.rd_re),
                self.rd_dat[b].eq(rd_ports[b].dat_r),
            ]


class TmemSampler(Module):
    """2-stage pipelined (II=1) 2D TMEM sampler with signed UV & 4-corner wrap.

    Pipeline timing (when stall == 0):
      Stage 0 (comb -> reg):
        - Extract signed 32-bit u_raw0/1, v_raw0/1 from 48-bit Q32.16 U, V
        - Compute independent 4-corner wrap (REPEAT / CLAMP / BORDER_DISCARD)
        - Compute bounds_discard from primary corner (0,0) & border_kill_en
        - Drive Tmem4Bank.rd_adr[0..3] (BSRAM reads synchronously on cycle 0->1)
        - Register corner parity, oob flags, wu, wv, bounds_discard, valid
      Stage 1 (comb -> reg):
        - Crossbar Tmem4Bank.rd_dat[0..3] to p00, p10, p01, p11 by parity
        - Substitute BG_COLOR for any oob corner (when border_kill_en == 0)
        - Register p00, p10, p01, p11, wu, wv, bounds_discard, valid_out
    """

    LATENCY = 3

    def __init__(self, tmem: Tmem4Bank):
        self.tmem = tmem

        # Input fragment stream
        self.valid_in = Signal()
        self.stall = Signal()
        self.u_q32_16 = Signal((48, True))
        self.v_q32_16 = Signal((48, True))

        # Texture configuration (held constant per command)
        self.tex_en = Signal()
        self.bilinear_en = Signal()
        self.wrap_u = Signal(2)
        self.wrap_v = Signal(2)
        self.border_kill_en = Signal()
        self.log2_tex_w = Signal(4)
        self.log2_tex_h = Signal(4)
        self.tmem_base = Signal(12)
        self.bg_color = Signal(32)

        # Output to BilinearFilter / Frag3DPort
        self.valid_out = Signal()
        self.p00 = Signal(32)
        self.p10 = Signal(32)
        self.p01 = Signal(32)
        self.p11 = Signal(32)
        self.wu = Signal(8)
        self.wv = Signal(8)
        self.bounds_discard = Signal()

        # --- Stage 0: Signed UV corner wrap & bank address generation ---
        u_raw0 = Signal((32, True))
        u_raw1 = Signal((32, True))
        v_raw0 = Signal((32, True))
        v_raw1 = Signal((32, True))
        tex_w = Signal(13)
        tex_h = Signal(13)
        mask_w = Signal(12)
        mask_h = Signal(12)

        self.comb += [
            u_raw0.eq(self.u_q32_16[16:48]),
            u_raw1.eq(u_raw0 + 1),
            v_raw0.eq(self.v_q32_16[16:48]),
            v_raw1.eq(v_raw0 + 1),
            tex_w.eq(1 << self.log2_tex_w),
            tex_h.eq(1 << self.log2_tex_h),
            mask_w.eq(tex_w - 1),
            mask_h.eq(tex_h - 1),
        ]

        def wrap_coord(raw, size, mask, mode):
            """Compute wrapped 12-bit coordinate and OOB flag for one axis."""
            oob = Signal()
            clamped = Signal(12)
            wrapped = Signal(12)
            max_idx = Signal(12)
            self.comb += [
                max_idx.eq(size - 1),
                oob.eq((raw < 0) | (raw >= size)),
                clamped.eq(
                    Mux(raw < 0, 0, Mux(raw >= size, max_idx, raw[0:12]))
                ),
                wrapped.eq(Mux(mode == WRAP_REPEAT, raw[0:12] & mask, clamped)),
            ]
            return wrapped, oob

        u0_w, oob_u0 = wrap_coord(u_raw0, tex_w, mask_w, self.wrap_u)
        u1_w, oob_u1 = wrap_coord(u_raw1, tex_w, mask_w, self.wrap_u)
        v0_w, oob_v0 = wrap_coord(v_raw0, tex_h, mask_h, self.wrap_v)
        v1_w, oob_v1 = wrap_coord(v_raw1, tex_h, mask_h, self.wrap_v)

        # --- Stage 0 -> Stage 1: Register wrapped coords & wu/wv ---
        s0_valid = Signal()
        u0_r = Signal(12, reset_less=True)
        u1_r = Signal(12, reset_less=True)
        v0_r = Signal(12, reset_less=True)
        v1_r = Signal(12, reset_less=True)
        oob_u0_r = Signal(reset_less=True)
        oob_u1_r = Signal(reset_less=True)
        oob_v0_r = Signal(reset_less=True)
        oob_v1_r = Signal(reset_less=True)
        s0_wu = Signal(8, reset_less=True)
        s0_wv = Signal(8, reset_less=True)

        self.sync += If(~self.stall,
            s0_valid.eq(self.valid_in),
            u0_r.eq(u0_w), u1_r.eq(u1_w),
            v0_r.eq(v0_w), v1_r.eq(v1_w),
            oob_u0_r.eq(oob_u0), oob_u1_r.eq(oob_u1),
            oob_v0_r.eq(oob_v0), oob_v1_r.eq(oob_v1),
            s0_wu.eq(Mux(self.bilinear_en, self.u_q32_16[8:16], 0)),
            s0_wv.eq(Mux(self.bilinear_en, self.v_q32_16[8:16], 0)),
        )

        # --- Stage 1: Bank address generation ---
        # 4 corners: (k, m) -> c00=(u0,v0), c10=(u1,v0), c01=(u0,v1), c11=(u1,v1)
        corners_uv = [
            (u0_r, v0_r, oob_u0_r, oob_v0_r),  # 0: p00
            (u1_r, v0_r, oob_u1_r, oob_v0_r),  # 1: p10
            (u0_r, v1_r, oob_u0_r, oob_v1_r),  # 2: p01
            (u1_r, v1_r, oob_u1_r, oob_v1_r),  # 3: p11
        ]

        c_bank = [Signal(2) for _ in range(4)]
        c_badr = [Signal(tmem.adr_bits) for _ in range(4)]
        c_oob = [Signal() for _ in range(4)]

        for idx, (uk, vm, ou, ov) in enumerate(corners_uv):
            v_half = Signal(11)
            u_half = Signal(11)
            row_off, row_case = _bank_row_offset(v_half, self.log2_tex_w)
            self.comb += [
                c_bank[idx].eq(Cat(uk[0], vm[0])),
                v_half.eq(vm[1:12]),
                u_half.eq(uk[1:12]),
                row_case,
                c_badr[idx].eq((self.tmem_base >> 2) + row_off + u_half),
                c_oob[idx].eq(
                    ((self.wrap_u == WRAP_BORDER_DISCARD) & ou)
                    | ((self.wrap_v == WRAP_BORDER_DISCARD) & ov)
                ),
            ]

        # Drive each bank's read address from whichever corner maps to that bank
        # (any two corners mapping to the same bank have identical (u, v) coords).
        self.comb += tmem.rd_re.eq(~self.stall)
        for b in range(4):
            self.comb += tmem.rd_adr[b].eq(
                Mux(c_bank[0] == b, c_badr[0],
                Mux(c_bank[1] == b, c_badr[1],
                Mux(c_bank[2] == b, c_badr[2], c_badr[3])))
            )

        # Stage 1 -> Stage 2 registers
        s1_valid = Signal()
        s1_bank = [Signal(2, reset_less=True) for _ in range(4)]
        s1_oob = [Signal(reset_less=True) for _ in range(4)]
        s1_wu = Signal(8, reset_less=True)
        s1_wv = Signal(8, reset_less=True)
        s1_bdisc = Signal(reset_less=True)

        self.sync += If(~self.stall,
            s1_valid.eq(s0_valid),
            s1_wu.eq(s0_wu),
            s1_wv.eq(s0_wv),
            s1_bdisc.eq(self.tex_en & self.border_kill_en & c_oob[0]),
            *[s1_bank[i].eq(c_bank[i]) for i in range(4)],
            *[s1_oob[i].eq(c_oob[i]) for i in range(4)],
        )

        # --- Stage 1: Post-read Crossbar & OOB Border Substitution ---
        # Note: if stall asserts on the cycle s1_valid is high, rd_dat already
        # captured the BSRAM output from cycle 0; holding s2 registers on stall
        # preserves the pipeline cleanly as long as upstream holds valid/data.
        xbar_pix = [Signal(32) for _ in range(4)]
        for i in range(4):
            bank_val = Signal(32)
            self.comb += [
                Case(s1_bank[i], {
                    0: bank_val.eq(tmem.rd_dat[0]),
                    1: bank_val.eq(tmem.rd_dat[1]),
                    2: bank_val.eq(tmem.rd_dat[2]),
                    3: bank_val.eq(tmem.rd_dat[3]),
                }),
                xbar_pix[i].eq(
                    Mux(s1_oob[i] & ~self.border_kill_en, self.bg_color, bank_val)
                ),
            ]

        self.sync += If(~self.stall,
            self.valid_out.eq(s1_valid),
            self.p00.eq(xbar_pix[0]),
            self.p10.eq(xbar_pix[1]),
            self.p01.eq(xbar_pix[2]),
            self.p11.eq(xbar_pix[3]),
            self.wu.eq(s1_wu),
            self.wv.eq(s1_wv),
            self.bounds_discard.eq(s1_bdisc),
        )


class Mode7ScanlineStepper(Module):
    """48-bit signed Q32.16 bidirectional DDA stepper for a Mode 7 scanline."""

    def __init__(self):
        self.start = Signal()
        self.ready = Signal(reset=1)
        self.busy = Signal()
        self.done = Signal()

        # Scanline parameters (32-bit signed Q16.16)
        self.width = Signal(12)
        self.y_in = Signal(12)
        self.u0_q16_16 = Signal(32)
        self.v0_q16_16 = Signal(32)
        self.du_dx_q16_16 = Signal(32)
        self.dv_dx_q16_16 = Signal(32)

        # Output stream
        self.valid_out = Signal()
        self.x_out = Signal(12)
        self.y_out = Signal(12)
        self.u_q32_16 = Signal((48, True))
        self.v_q32_16 = Signal((48, True))

        active = Signal()
        rem = Signal(12)
        x_cur = Signal(12)
        y_reg = Signal(12)
        u_acc = Signal((48, True))
        v_acc = Signal((48, True))
        du_reg = Signal((48, True))
        dv_reg = Signal((48, True))

        self.comb += [
            self.busy.eq(active),
            self.valid_out.eq(active),
            self.x_out.eq(x_cur),
            self.y_out.eq(y_reg),
            self.u_q32_16.eq(u_acc),
            self.v_q32_16.eq(v_acc),
        ]

        self.sync += [
            self.done.eq(0),
            If(~active,
                If(self.start & (self.width != 0),
                    active.eq(1),
                    rem.eq(self.width - 1),
                    x_cur.eq(0),
                    y_reg.eq(self.y_in),
                    u_acc.eq(_sext48_from_32(self.u0_q16_16)),
                    v_acc.eq(_sext48_from_32(self.v0_q16_16)),
                    du_reg.eq(_sext48_from_32(self.du_dx_q16_16)),
                    dv_reg.eq(_sext48_from_32(self.dv_dx_q16_16)),
                ),
            ).Else(
                If(self.ready,
                    If(rem == 0,
                        active.eq(0),
                        self.done.eq(1),
                    ).Else(
                        rem.eq(rem - 1),
                        x_cur.eq(x_cur + 1),
                        u_acc.eq(u_acc + du_reg),
                        v_acc.eq(v_acc + dv_reg),
                    )
                )
            ),
        ]
