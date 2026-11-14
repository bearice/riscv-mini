"""Integrated GFX3D Pipeline (Stage 2..4) connecting to Gfx2DEngine.frag3d.

Composes:
  TriRasterizer -> PerspRecipUnit (5 cyc) -> TmemSampler + ZBufferUnit (2 cyc)
  -> Frag3DPort (Gfx2DEngine) -> ZBufferUnit Late-Z commit.
"""
from migen import *

from gateware.machines.mega138k_ae350.peripherals.gfx3d_persp import (
    PerspRecipUnit,
    ZBufferUnit,
)
from gateware.machines.mega138k_ae350.peripherals.gfx3d_raster import TriRasterizer
from gateware.machines.mega138k_ae350.peripherals.gfx3d_tmem import (
    Tmem4Bank,
    TmemSampler,
)


class Gfx3DPipeline(Module):
    """Complete GFX3D raster + perspective + TMEM + Z-buffer front-end."""

    def __init__(self, frag3d, tmem_depth_per_bank=1024, z_max_width=2048,
                 clock_domain="ddr"):
        self.frag3d = frag3d

        self.tri = TriRasterizer()
        self.persp = PerspRecipUnit(clock_domain=clock_domain)
        self.tmem = Tmem4Bank(depth_per_bank=tmem_depth_per_bank,
                              clock_domain=clock_domain)
        self.sampler = TmemSampler(self.tmem)
        self.zbuf = ZBufferUnit(max_width=z_max_width,
                                clock_domain=clock_domain)

        self.submodules += [
            self.tri,
            self.persp,
            self.tmem,
            self.sampler,
            self.zbuf,
        ]

        # Total sideband delay: PerspRecipUnit.LATENCY (5) + TmemSampler.LATENCY (2) = 7
        sb_len = PerspRecipUnit.LATENCY + TmemSampler.LATENCY
        x_d = [Signal(12, reset_less=True) for _ in range(sb_len)]
        y_d = [Signal(12, reset_less=True) for _ in range(sb_len)]
        z_d = [Signal(32, reset_less=True) for _ in range(sb_len)]
        rgba_d = [Signal(32, reset_less=True) for _ in range(sb_len)]

        stall = Signal()
        self.comb += [
            stall.eq(~frag3d.ready),
            self.tri.ready.eq(frag3d.ready),
            self.persp.stall.eq(stall),
            self.sampler.stall.eq(stall),
        ]

        self.sync += If(~stall,
            x_d[0].eq(self.tri.x_out),
            y_d[0].eq(self.tri.y_out),
            z_d[0].eq(self.tri.z_out),
            rgba_d[0].eq(self.tri.rgba_out),
            *[x_d[i].eq(x_d[i - 1]) for i in range(1, sb_len)],
            *[y_d[i].eq(y_d[i - 1]) for i in range(1, sb_len)],
            *[z_d[i].eq(z_d[i - 1]) for i in range(1, sb_len)],
            *[rgba_d[i].eq(rgba_d[i - 1]) for i in range(1, sb_len)],
        )

        # TriRasterizer -> PerspRecipUnit -> TmemSampler
        self.comb += [
            self.persp.valid_in.eq(self.tri.valid_out),
            self.persp.persp_en.eq(self.tri.persp_en_out),
            self.persp.s_q12_20.eq(self.tri.s_q12_20),
            self.persp.t_q12_20.eq(self.tri.t_q12_20),
            self.persp.q_clamped.eq(self.tri.q_clamped),

            self.sampler.valid_in.eq(self.persp.valid_out),
            self.sampler.u_q32_16.eq(self.persp.u_q32_16),
            self.sampler.v_q32_16.eq(self.persp.v_q32_16),

            # Early-Z read at cycle sb_len - 2 (= 5), ready at cycle sb_len - 1 (= 6)
            self.zbuf.rd_x.eq(x_d[sb_len - 2]),
            self.zbuf.z_new_in.eq(z_d[sb_len - 2][16:32]),

            # TmemSampler + ZBufferUnit -> Frag3DPort
            frag3d.valid.eq(self.sampler.valid_out),
            frag3d.p00.eq(self.sampler.p00),
            frag3d.p10.eq(self.sampler.p10),
            frag3d.p01.eq(self.sampler.p01),
            frag3d.p11.eq(self.sampler.p11),
            frag3d.wu.eq(self.sampler.wu),
            frag3d.wv.eq(self.sampler.wv),
            frag3d.bounds_discard.eq(self.sampler.bounds_discard),
            frag3d.x.eq(x_d[sb_len - 1]),
            frag3d.y.eq(y_d[sb_len - 1]),
            frag3d.z_new.eq(z_d[sb_len - 1][16:32]),
            frag3d.z_pass.eq(self.zbuf.z_pass_out),
            frag3d.rgba.eq(rgba_d[sb_len - 1]),
            frag3d.dst_adr.eq(x_d[sb_len - 1][0:10]),

            # Late-Z commit
            self.zbuf.out_valid.eq(frag3d.out_valid),
            self.zbuf.out_x.eq(frag3d.out_x[0:12]),
            self.zbuf.out_z_new.eq(frag3d.out_z),
            self.zbuf.out_we_color_raw.eq(frag3d.out_we_color),
            self.zbuf.out_we_z_raw.eq(frag3d.out_we_z),
        ]

        self.pipe_busy = Signal()
        self.comb += self.pipe_busy.eq(
            self.tri.busy | self.persp.valid_out | self.sampler.valid_out | frag3d.pipe_busy
        )
