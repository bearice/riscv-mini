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

        # Total sideband delay: PerspRecipUnit.LATENCY (9) + TmemSampler.LATENCY (3) = 12
        # Store sideband (x[12], y[12], z_hi[16], rgba[32] = 72b) in a 16-entry
        # circular BSRAM buffer to save ~800 FFs.
        sb_len = PerspRecipUnit.LATENCY + TmemSampler.LATENCY
        sb_mem = Memory(72, 16, name="g3d_sb_mem")
        sb_wr = sb_mem.get_port(write_capable=True, clock_domain=clock_domain)
        sb_rd = sb_mem.get_port(has_re=True, clock_domain=clock_domain)
        self.specials += [sb_mem, sb_wr, sb_rd]

        sb_wp = Signal(4)
        sb_rp = Signal(4)
        sb_q2 = Signal(72, reset_less=True)

        stall = Signal()
        self.comb += [
            stall.eq(~frag3d.ready),
            self.tri.ready.eq(frag3d.ready),
            self.persp.stall.eq(stall),
            self.sampler.stall.eq(stall),
            self.zbuf.stall.eq(stall),
            sb_wr.adr.eq(sb_wp),
            sb_wr.we.eq(~stall),
            sb_wr.dat_w.eq(Cat(self.tri.x_out, self.tri.y_out,
                               self.tri.z_out[16:32], self.tri.rgba_out)),
            sb_rp.eq(sb_wp - (sb_len - 2)),
            sb_rd.adr.eq(sb_rp),
            sb_rd.re.eq(~stall),
        ]

        self.sync += If(~stall,
            sb_wp.eq(sb_wp + 1),
            sb_q2.eq(sb_rd.dat_r),
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

            # Early-Z read at cycle sb_len - 2 (sb_rd.dat_r), ready at cycle sb_len - 1
            self.zbuf.rd_x.eq(sb_rd.dat_r[0:12]),
            self.zbuf.z_new_in.eq(sb_rd.dat_r[24:40]),

            # TmemSampler + ZBufferUnit -> Frag3DPort
            frag3d.valid.eq(self.sampler.valid_out),
            frag3d.p00.eq(self.sampler.p00),
            frag3d.p10.eq(self.sampler.p10),
            frag3d.p01.eq(self.sampler.p01),
            frag3d.p11.eq(self.sampler.p11),
            frag3d.wu.eq(self.sampler.wu),
            frag3d.wv.eq(self.sampler.wv),
            frag3d.bounds_discard.eq(self.sampler.bounds_discard),
            frag3d.x.eq(sb_q2[0:12]),
            frag3d.y.eq(sb_q2[12:24]),
            frag3d.z_new.eq(sb_q2[24:40]),
            frag3d.z_pass.eq(self.zbuf.z_pass_out),
            frag3d.rgba.eq(sb_q2[40:72]),
            frag3d.dst_adr.eq(sb_q2[0:10]),

            # Late-Z commit
            self.zbuf.out_valid.eq(frag3d.out_valid),
            self.zbuf.out_x.eq(frag3d.out_x[0:12]),
            self.zbuf.out_z_new.eq(frag3d.out_z),
            self.zbuf.out_we_color_raw.eq(frag3d.out_we_color),
            self.zbuf.out_we_z_raw.eq(frag3d.out_we_z),
        ]

        self.downstream_busy = Signal()
        self.pipe_busy = Signal()
        self.comb += [
            self.downstream_busy.eq(
                self.persp.busy | self.sampler.busy | frag3d.pipe_busy
            ),
            self.pipe_busy.eq(self.tri.busy | self.downstream_busy),
        ]
