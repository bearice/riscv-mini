"""Full-speed Host core boundary for the Dock's discrete Soft USB network.

This module requires a dedicated 48/60 MHz domain and an AXI-Lite master
already crossing into that domain. It does not expose the core on sys/APB.
Physical pad buffers must supply the two single-ended receive comparators.
External Host pull-downs are required; Device/HS termination pins stay Hi-Z.
"""
from pathlib import Path

from migen import ClockSignal, ResetSignal, Signal, Record, Instance
from litex.gen import LiteXModule
from litex.soc.interconnect.axi import AXILiteInterface


class SoftUSBHost(LiteXModule):
    def __init__(self, platform, clock_hz=48000000, domain='usb'):
        if clock_hz not in (48000000, 60000000):
            raise ValueError('USB FS core requires 48 or 60 MHz, not the 50 MHz sys clock')
        self.bus = bus = AXILiteInterface(data_width=32, address_width=32,
                                        clock_domain=domain)
        self.interrupt = Signal()
        self.rx_dp, self.rx_dn = Signal(), Signal()
        self.tx_dp, self.tx_dn, self.tx_oe = Signal(), Signal(), Signal()
        self.lines = Signal(2)
        # No Device pull-up, no zero-ohm termination drive in Host FS mode.
        self.pullup_oe = Signal()
        self.term_dp_oe, self.term_dn_oe = Signal(), Signal()
        self.comb += [self.pullup_oe.eq(0), self.term_dp_oe.eq(0),
                      self.term_dn_oe.eq(0)]
        utmi = Record([(n, w) for n, w in (
            ('data_in', 8), ('data_out', 8), ('txvalid', 1), ('txready', 1),
            ('rxvalid', 1), ('rxactive', 1), ('rxerror', 1), ('linestate', 2),
            ('op_mode', 2), ('xcvrselect', 2), ('termselect', 1),
            ('dppulldown', 1), ('dmpulldown', 1))])
        phy_inputs = ('data_out', 'txvalid', 'op_mode', 'xcvrselect',
                      'termselect', 'dppulldown', 'dmpulldown')
        phy_outputs = ('data_in', 'txready', 'rxvalid', 'rxactive',
                       'rxerror', 'linestate')
        oen = Signal()
        self.comb += [self.tx_oe.eq(~oen & ~ResetSignal(domain)),
                      self.lines.eq(utmi.linestate)]
        self.specials += Instance('usb_fs_phy', p_USB_CLK_FREQ=clock_hz,
            i_clk_i=ClockSignal(domain), i_rst_i=ResetSignal(domain),
            i_usb_rx_dp_i=self.rx_dp, i_usb_rx_dn_i=self.rx_dn,
            i_usb_rx_rcv_i=self.rx_dp, i_usb_reset_assert_i=0,
            o_usb_tx_dp_o=self.tx_dp, o_usb_tx_dn_o=self.tx_dn,
            o_usb_tx_oen_o=oen,
            **{'i_utmi_'+n+'_i': getattr(utmi, n) for n in phy_inputs},
            **{'o_utmi_'+n+'_o': getattr(utmi, n) for n in phy_outputs})
        ports = dict(p_USB_CLK_FREQ=clock_hz, i_clk_i=ClockSignal(domain),
                     i_rst_i=ResetSignal(domain), o_intr_o=self.interrupt)
        for channel, fields in (
            ('aw', ('valid', 'ready', 'addr')), ('w', ('valid', 'ready', 'data', 'strb')),
            ('b', ('valid', 'ready', 'resp')), ('ar', ('valid', 'ready', 'addr')),
            ('r', ('valid', 'ready', 'data', 'resp'))):
            for field in fields:
                incoming = field != 'ready' if channel in ('aw', 'w', 'ar') else field == 'ready'
                ports[('i_' if incoming else 'o_')+'cfg_'+channel+field+
                      ('_i' if incoming else '_o')] = getattr(getattr(bus, channel), field)
        ports.update({'o_utmi_'+n+'_o': getattr(utmi, n) for n in phy_inputs})
        ports.update({'i_utmi_'+n+'_i': getattr(utmi, n) for n in phy_outputs})
        self.specials += Instance('usbh_host', **ports)
        vendor = Path(__file__).resolve().parents[3] / 'vendor'
        platform.add_verilog_include_path(str(vendor / 'usb_host/src_v'))
        for source in sorted((vendor / 'usb_host/src_v').glob('*.v')):
            if source.name != 'usbh_host_defs.v':
                platform.add_source(str(source))
        platform.add_source(str(vendor / 'usb_fs_phy/usb_fs_phy.v'))


class USBRegisterBridge(LiteXModule):
    """One owned APB operation, independent AXI address/data acceptance."""
    def __init__(self, native, clock_hz=60000000):
        from migen import FSM, If, NextValue, NextState
        from litex.soc.interconnect.axi import AXILiteClockDomainCrossing
        self.off, self.wdata, self.rdata = Signal(12), Signal(32), Signal(32)
        self.selected, self.write, self.commit, self.complete, self.error = [Signal() for _ in range(5)]
        self.raw_status = Signal(4)
        self.bus = b = AXILiteInterface(data_width=32, address_width=32)
        self.cdc = AXILiteClockDomainCrossing(b, native, cd_from='sys', cd_to='usb')
        self.fsm = f = FSM(reset_state='IDLE')
        f.act('IDLE', If(self.selected & self.commit,
            If(self.off >= 0x100,
                NextValue(self.error, self.write | ((self.off != 0x100) & (self.off != 0x104) & (self.off != 0x108))),
                NextValue(self.rdata, 0),
                If(self.off == 0x100, NextValue(self.rdata, 0xae350c01)),
                If(self.off == 0x104, NextValue(self.rdata, self.raw_status)),
                If(self.off == 0x108, NextValue(self.rdata, clock_hz)),
                NextState('DONE')
            ).Else(
            NextValue(b.aw.addr, self.off), NextValue(b.ar.addr, self.off),
            NextValue(b.w.data, self.wdata), NextValue(b.w.strb, 15),
            If(self.write, NextValue(b.aw.valid, 1), NextValue(b.w.valid, 1), NextState('WRITE'))
            .Else(NextValue(b.ar.valid, 1), NextState('READ')))))
        f.act('WRITE', If(b.aw.ready, NextValue(b.aw.valid, 0)),
            If(b.w.ready, NextValue(b.w.valid, 0)),
            If(~b.aw.valid & ~b.w.valid, NextState('BRESP')))
        f.act('BRESP', b.b.ready.eq(1), If(b.b.valid,
            NextValue(self.error, b.b.resp != 0), NextValue(self.rdata, 0), NextState('DONE')))
        f.act('READ', If(b.ar.ready, NextValue(b.ar.valid, 0), NextState('RRESP')))
        f.act('RRESP', b.r.ready.eq(1), If(b.r.valid,
            NextValue(self.error, b.r.resp != 0), NextValue(self.rdata, b.r.data), NextState('DONE')))
        f.act('DONE', self.complete.eq(1), If(self.selected & self.commit, NextState('IDLE')))


class USBClock(LiteXModule):
    """50 MHz input * 24 / 20 = 60 MHz; independent 1200 MHz VCO."""
    def __init__(self, platform, clkin, reset, clock_hz):
        if clock_hz != 60000000:
            raise ValueError("Dock USB PLL currently requires machine clocks.usb=60000000")
        from migen import ClockDomain, Constant
        from migen.genlib.resetsync import AsyncResetSynchronizer
        self.cd_usb = ClockDomain('usb')
        self.locked = Signal()
        params = dict(p_FCLKIN='50.0', p_IDIV_SEL=1, p_FBDIV_SEL=1,
            p_MDIV_SEL=24, p_ODIV0_SEL=20, p_CLKFB_SEL='INTERNAL',
            p_CLK4_IN_SEL=Constant(0,2), p_RESET_I_EN='FALSE', p_RESET_O_EN='FALSE',
            p_SSC_EN='FALSE', i_CLKIN=clkin, i_CLKFB=0, i_RESET=reset,
            i_PLLPWD=0, i_RESET_I=0, i_RESET_O=0, o_LOCK=self.locked,
            o_CLKOUT0=self.cd_usb.clk)
        for name,width in dict(FBDSEL=6,IDSEL=6,MDSEL=7,MDSEL_FRAC=3,ODSEL0_FRAC=3,
            PSSEL=3,PSDIR=1,PSPULSE=1,SSCPOL=1,SSCON=1,SSCMDSEL=7,
            SSCMDSEL_FRAC=3,ICPSEL=6,LPFRES=3,LPFCAP=2).items():
            params['i_'+name] = Constant(0,width)
        for i in range(7):
            params.update({'p_CLKOUT%d_EN'%i:'TRUE' if i==0 else 'FALSE',
                           'i_ENCLK%d'%i:1, 'i_ODSEL%d'%i:Constant(0,7)})
        self.specials += Instance('PLL', name='usb_pll', **params)
        self.specials += AsyncResetSynchronizer(self.cd_usb, reset | ~self.locked)
        platform.add_generated_clock_constraint(self.cd_usb.clk, clkin,
            divide_by=20, multiply_by=24, pin='usb_pll/CLKOUT0')
