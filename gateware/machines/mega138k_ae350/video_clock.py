"""Runtime GW5A video PLL. Dynamic codes follow installed UG306 PLL model.

Parameters change only while RESET is asserted by the drained-stop sequencer.
"""
from migen import Case, Constant, Instance, Signal
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from gateware.machines.mega138k_ae350.video_modes import MODES, pll_config


RESET_FINE = 2  # eighths of one VCO period


class VideoPLL(LiteXModule):
    def __init__(self, clkin, outputs, input_hz):
        self.reset = Signal()
        self.locked = Signal()
        self.mode = Signal(max=len(MODES))
        idsel, mdsel = Signal(6), Signal(7)
        fraction = Signal(3)
        pixel_div, serial_div = Signal(7), Signal(7)
        configs = [pll_config(m.pixel_hz, int(input_hz)) for m in MODES]
        self.comb += Case(self.mode,{i:[idsel.eq(64-c['idiv']),mdsel.eq(128-c['mdiv']),
            fraction.eq(7-c['fraction']),pixel_div.eq(128-c['pixel_div']),
            serial_div.eq(128-c['serial_div'])] for i,c in enumerate(configs)})
        boot=configs[0]
        # The implementation tool derives analog loop settings from the
        # static recipe even when the divider ports are dynamic.
        params=dict(p_FCLKIN=str(input_hz/1e6),p_FBDIV_SEL=1,p_CLKFB_SEL='INTERNAL',
            p_IDIV_SEL=boot['idiv'],p_MDIV_SEL=boot['mdiv'],p_MDIV_FRAC_SEL=boot['fraction'],
            p_ODIV0_SEL=boot['pixel_div'],p_ODIV1_SEL=boot['serial_div'],p_ODIV2_SEL=boot['pixel_div'],
            p_DYN_IDIV_SEL='TRUE',p_DYN_MDIV_SEL='TRUE',p_CLK4_IN_SEL=Constant(0,2),
            p_RESET_I_EN='FALSE',p_RESET_O_EN='FALSE',p_SSC_EN='FALSE',
            i_CLKIN=clkin,i_CLKFB=0,i_RESET=self.reset,i_PLLPWD=0,i_RESET_I=0,i_RESET_O=0,
            i_IDSEL=idsel,i_MDSEL=mdsel,i_MDSEL_FRAC=fraction,i_ODSEL0_FRAC=Constant(7,3),
            o_LOCK=self.locked)
        for name,bits in dict(FBDSEL=6,PSSEL=3,PSDIR=1,PSPULSE=1,SSCPOL=1,SSCON=1,
                             SSCMDSEL=7,SSCMDSEL_FRAC=3,ICPSEL=6,LPFRES=3,LPFCAP=2).items():
            params['i_'+name]=Constant(0,bits)
        for i in range(7):
            params.update({'p_CLKOUT%d_EN'%i:'TRUE' if i<3 else 'FALSE',
                           'i_ENCLK%d'%i:1,'i_ODSEL%d'%i:serial_div if i==1 else pixel_div,
                           'p_DYN_ODIV%d_SEL'%i:'TRUE' if i<3 else 'FALSE'})
        for i,cd in enumerate(outputs):
            params['o_CLKOUT%d'%i]=cd.clk
            if i==0:self.specials += AsyncResetSynchronizer(cd,~self.locked | self.reset)
        # A quarter-VCO offset balances lane-local reset propagation against
        # both adjacent FCLK edges. All recovery/removal paths remain timed.
        params['p_CLKOUT2_PE_FINE']=RESET_FINE
        self.specials += Instance('PLL',name='ae350_hdmi_pll',**params)
        # Declare each physical mode, mutually exclusive on each PLL output.
        # Keep the fastest mode on the historical names for clock-group use.
        self.constraints=[]
        unique={tuple(c.values()):c for c in configs}
        clock_sets=[]
        for n,c in enumerate(sorted(unique.values(),key=lambda c:c['actual_hz'],reverse=True)):
            names=[]
            for i,base in enumerate(('hdmi_pix','hdmi_5x','hdmi_reset')):
                name=base+'_clk'+('' if n==0 else '_mode%d'%n)
                names.append(name)
                div=c['serial_div'] if i==1 else c['pixel_div']
                multiply=c['mdiv']*8+c['fraction']
                phase=45*RESET_FINE/div if i==2 else 0
                self.constraints.append('create_generated_clock -name %s -add -source [get_ports {clk50}] '
                    '-divide_by %d -multiply_by %d -phase %g [get_pins {ae350_hdmi_pll/CLKOUT%d}]'
                    %(name,c['idiv']*8*div,multiply,phase,i))
            clock_sets.append(names)
        self.clock_names=' '.join(name for group in clock_sets for name in group)
        self.constraints.append('set_clock_groups -exclusive '+
            ' '.join('-group [get_clocks {%s}]'%' '.join(group) for group in clock_sets))
