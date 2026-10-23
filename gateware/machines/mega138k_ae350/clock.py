"""AE350 platform clock and reset generation."""
from migen import ClockDomain, Constant, Signal, If, Instance
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.soc.cores.clock.gowin_gw5a import GW5APLL


class FabricPLL(LiteXModule):
    """800MHz VCO for the machine's fabric/core/data clocks (Gowin UG306).

    The data output uses +50ps falling-edge trim. Its generated clock has the
    matching duty cycle; minimum high AND low pulses are still checked by STA.
    This local primitive adapter exposes the static trim absent from GW5APLL.
    """
    def __init__(self, platform, clkin, outputs):
        self.reset = Signal()
        self.locked = Signal()
        self.clocks = []
        self.extra_constraints = []
        params = dict(p_FCLKIN='50.0', p_IDIV_SEL=1, p_FBDIV_SEL=1, p_MDIV_SEL=16,
                      p_CLKFB_SEL='INTERNAL', p_CLK4_IN_SEL=Constant(0, 2),
                      p_RESET_I_EN='FALSE', p_RESET_O_EN='FALSE', p_SSC_EN='FALSE',
                      i_CLKIN=clkin, i_CLKFB=0, i_RESET=self.reset,
                      i_PLLPWD=0, i_RESET_I=0, i_RESET_O=0, o_LOCK=self.locked)
        inputs = dict(FBDSEL=6, IDSEL=6, MDSEL=7, MDSEL_FRAC=3, ODSEL0_FRAC=3,
                      PSSEL=3, PSDIR=1, PSPULSE=1, SSCPOL=1, SSCON=1,
                      SSCMDSEL=7, SSCMDSEL_FRAC=3, ICPSEL=6, LPFRES=3, LPFCAP=2)
        params.update({'i_' + name: Constant(0, width) for name, width in inputs.items()})
        for index in range(7):
            params.update({'p_CLKOUT%d_EN' % index: 'TRUE' if index < len(outputs) else 'FALSE',
                           'i_ENCLK%d' % index: 1, 'i_ODSEL%d' % index: Constant(0, 7)})
        for index in range(4):
            params.update({'i_DT%d' % index: Constant(0, 4),
                           'p_CLKOUT%d_DT_DIR' % index: 1,
                           'p_CLKOUT%d_DT_STEP' % index: 1 if index == 2 else 0})
        for index, (cd, frequency) in enumerate(outputs):
            divide = round(800e6 / frequency)
            if not 1 <= divide <= 128 or 800e6 / divide != frequency:
                raise ValueError('fabric PLL frequency must divide its 800MHz VCO exactly')
            clock = Signal(name='fabric_pll_clkout%d' % index)
            self.clocks.append(clock)
            params['p_ODIV%d_SEL' % index] = divide
            params['o_CLKOUT%d' % index] = clock
            self.comb += cd.clk.eq(clock)
            # Preserve the existing output-reset contract: sys/mem release
            # through LOCK, rather than a raw clk50 counter into PRESET.
            self.specials += AsyncResetSynchronizer(cd,
                ~self.locked | (self.reset if index == 1 else 0))
            if index == 2:
                duty = 50 + 0.05 / (1e9 / frequency) * 100
                self.extra_constraints.append(
                    'create_generated_clock -name mem_clk -source [get_ports {clk50}] '
                    '-divide_by %d -multiply_by 16 -duty_cycle %g [get_pins {ae350_pll/CLKOUT2}]'
                    % (divide, duty))
            else:
                platform.add_generated_clock_constraint(cd.clk, clkin, divide_by=divide,
                    multiply_by=16, pin='ae350_pll/CLKOUT%d' % index)
        self.specials += Instance('PLL', name='ae350_pll', **params)


class ClockReset(LiteXModule):
    def __init__(self, platform, fabric_hz=100e6, data_hz=None):
        self.cd_sys = ClockDomain('sys')
        self.cd_cpu = ClockDomain('cpu')
        self.cd_input = ClockDomain('input')
        self.cd_hdmi_pix = ClockDomain('hdmi_pix')
        self.cd_hdmi_5x = ClockDomain('hdmi_5x')
        self.cd_hdmi_reset = ClockDomain('hdmi_reset', reset_less=True)
        clk = platform.request('clk50')
        platform.add_period_constraint(clk, 20)
        reset_key = platform.request('reset_key')
        self.comb += self.cd_input.clk.eq(clk)
        count = Signal(16, reset=65535)
        self.sync.input += If(reset_key, count.eq(65535)).Elif(count != 0, count.eq(count - 1))
        outputs = [(self.cd_sys, fabric_hz), (self.cd_cpu, 800e6)]
        if data_hz is not None:
            self.cd_mem = ClockDomain('mem')
            outputs.append((self.cd_mem, data_hz))
        # CORE_CLK stays on CLKOUT1, the dedicated PLL_R[0] path.
        self.pll = pll = FabricPLL(platform, clk, outputs)
        self.comb += pll.reset.eq(reset_key | (count != 0))
        platform.toolchain.additional_cst_commands.append('INS_LOC "ae350_pll" PLL_R[0];')
        # Keep the fabric/BSRAM clock on the dedicated global clock network.
        fabric_clock = pll.clocks[0]
        fabric_clock.attr.add(('syn_keep', 1))
        platform.add_platform_command('CLOCK_LOC "{clock}" BUFG = CLK;', clock=fabric_clock)
        if data_hz is not None:
            data_clock = pll.clocks[2]
            data_clock.attr.add(('syn_keep', 1))
            platform.add_platform_command('CLOCK_LOC "{clock}" BUFG = CLK;', clock=data_clock)

        # The 640x480 TMDS mode uses the nearest 50MHz-input integer solution.
        # The 1% tolerance admits 125/25MHz while preserving the exact 5:1
        # serializer-to-pixel relationship. Reset release uses an 8ns shift;
        # recovery/removal analysis includes the reset net's propagation delay.
        # Keep fabric reset synchronizers off the fast I/O clock network.
        self.hdmi_pll = hdmi_pll = GW5APLL(platform.devicename, platform.device, name='ae350_hdmi_pll')
        hdmi_pll.register_clkin(clk, 50e6)
        hdmi_pll.create_clkout(self.cd_hdmi_pix, 25.175e6, margin=1e-2)
        hdmi_pll.create_clkout(self.cd_hdmi_5x, 125.875e6, margin=1e-2, with_reset=False)
        hdmi_pll.create_clkout(self.cd_hdmi_reset, 25.175e6, phase=72, margin=1e-2, with_reset=False)
        self.comb += hdmi_pll.reset.eq(reset_key | (count != 0))
        # The backend generated-clock helper has no phase argument. Emit all
        # HDMI clocks together with their actual PLL ratios and phase instead.
        hdmi_config = hdmi_pll.compute_config()
        self.hdmi_clock_constraints = [
            'create_generated_clock -name %s_clk -source [get_ports {clk50}] '
            '-divide_by %d -multiply_by %d -phase %g [get_pins {ae350_hdmi_pll/CLKOUT%d}]'
            % (cd.name, hdmi_config['idiv'] * hdmi_config['odiv%d' % n],
               hdmi_config['fdiv'] * hdmi_config['mdiv'], hdmi_pll.clkouts[n].phase, n)
            for n, cd in hdmi_pll.clkout_cds.items()
        ]
        self.rtc = Signal()
        phase = Signal(32)
        self.sync.input += phase.eq(phase + round(32768 * 2**32 / 50000000))
        # Give RTC a real clock source net. A combinational alias to phase[31]
        # is removed by Gowin synthesis and leaves an invalid SDC clock object.
        self.specials += Instance('DFF', name='rtc_buffer', i_CLK=clk, i_D=phase[31], o_Q=self.rtc)
        platform.add_period_constraint(self.rtc, 1e9 / 32768)
