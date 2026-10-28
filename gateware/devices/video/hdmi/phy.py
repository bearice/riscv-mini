"""Gowin TMDS PHY in Migen, with explicit serializer and asynchronous FF primitives.

Derived from SVO, Copyright (C) 2014 Clifford Wolf <clifford@clifford.at>.
Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.
THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""
from migen import Cat, ClockDomain, ClockDomainsRenamer, Instance, Signal
from litex.gen import LiteXModule
from gateware.devices.video.hdmi.tmds import TMDSEncoder


class GowinHDMIPHY(LiteXModule):
    def __init__(self, clk_pixel, clk_5x, clk_reset, clk_p, clk_n, data_p, data_n):
        self.resetn = Signal()
        self.locked = Signal()
        self.rgb = Signal(24)
        self.de, self.hsync, self.vsync = Signal(), Signal(), Signal()
        self.cd_phy_encode = ClockDomain('phy_encode', reset_less=True)
        self.comb += self.cd_phy_encode.clk.eq(clk_pixel)
        reset = Signal()
        self.comb += reset.eq(~self.resetn | ~self.locked)
        # Explicit asynchronous storage preserves the original assertion and
        # release edges. A domain reset synchronizer would add release cycles.
        def async_register(name, output, value, clock):
            for bit in range(len(output)):
                self.specials += Instance('DFFCE', name='%s_%d' % (name, bit),
                    attr={'syn_keep', 'syn_preserve'} if name.startswith('hdmi_lane_') else set(),
                    i_CLK=clock, i_CE=1, i_CLEAR=reset, i_D=value[bit], o_Q=output[bit])
        self.pixel_resetn_q = Signal(4)
        self.serializer_resetn_q = Signal(3)
        self.lane_resetn = [Signal(name_override='hdmi_lane_resetn_%d' % i,
                                  attr={'syn_keep', 'syn_preserve'}) for i in range(3)]
        self.serializer_resetn = self.lane_resetn[0]
        async_register('pixel_release', self.pixel_resetn_q, Cat(1, self.pixel_resetn_q[:3]), clk_pixel)
        async_register('serializer_release', self.serializer_resetn_q, Cat(1, self.serializer_resetn_q[:2]), clk_reset)
        for lane, q in enumerate(self.lane_resetn):
            async_register('hdmi_lane_resetn_ff%d' % lane, q, self.serializer_resetn_q[2], clk_reset)
        rgb_q, de_q, hs_q, vs_q = Signal(24), Signal(), Signal(), Signal()
        for name, output, value in (('rgb', rgb_q, self.rgb), ('de', de_q, self.de),
                                    ('hs', hs_q, self.hsync), ('vs', vs_q, self.vsync)):
            async_register('pixel_'+name, output, value, clk_pixel)
        serial = Signal(3)
        self.symbols = []
        for lane in range(3):
            encoder = ClockDomainsRenamer('phy_encode')(TMDSEncoder())
            setattr(self.submodules, 'encoder%d' % lane, encoder)
            self.symbols.append(encoder.dout)
            self.comb += [encoder.resetn.eq(self.pixel_resetn_q[3]), encoder.de.eq(de_q),
                          encoder.ctrl.eq(Cat(hs_q, vs_q) if lane == 0 else 0),
                          encoder.din.eq(rgb_q[16-8*lane:24-8*lane])]
            self.specials += Instance('OSER10', o_Q=serial[lane],
                **{'i_D%d' % i: encoder.dout[i] for i in range(10)},
                i_PCLK=clk_pixel, i_FCLK=clk_5x, i_RESET=~self.lane_resetn[lane])
            self.specials += Instance('ELVDS_OBUF', i_I=serial[lane], o_O=data_p[lane], o_OB=data_n[lane])
        self.specials += Instance('ELVDS_OBUF', i_I=clk_pixel, o_O=clk_p, o_OB=clk_n)
