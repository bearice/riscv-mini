"""AE350 platform clock and reset generation."""
from migen import ClockDomain, Constant, Signal, If, Instance
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from gateware.machines.mega138k_ae350.video_clock import VideoPLL


class PLLStatusSynchronizer(LiteXModule):
    """Two resetless stages for the vendor's asynchronous lock indication."""
    first_stage_pin = 'ddr_pll_lock_meta_s*/D'

    def __init__(self, lock):
        self.meta = Signal(name_override='ddr_pll_lock_meta', reset_less=True)
        self.o = Signal(name_override='ddr_pll_lock_sync', reset_less=True)
        for stage in (self.meta, self.o):
            stage.attr.update({('syn_keep', 1), ('syn_preserve', 1)})
        self.sync += [self.meta.eq(lock), self.o.eq(self.meta)]


class FabricPLL(LiteXModule):
    """800MHz VCO for the machine's fabric/core/data clocks (Gowin UG306).

    The data output uses +50ps falling-edge trim. Its generated clock has the
    matching duty cycle; minimum high AND low pulses are still checked by STA.
    This local primitive adapter exposes the static trim absent from GW5APLL.
    """
    def __init__(self, platform, clkin, outputs, input_hz):
        self.reset = Signal()
        self.locked = Signal()
        self.clocks = []
        self.extra_constraints = []
        multiply = round(800e6 / input_hz)
        if not 1 <= multiply <= 128 or input_hz * multiply != 800e6:
            raise ValueError('fabric PLL input must produce its 800MHz VCO with an integer multiplier')
        params = dict(p_FCLKIN=str(input_hz / 1e6), p_IDIV_SEL=1, p_FBDIV_SEL=1, p_MDIV_SEL=multiply,
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
                    '-divide_by %d -multiply_by %d -duty_cycle %g [get_pins {ae350_pll/CLKOUT2}]'
                    % (divide, multiply, duty))
            else:
                platform.add_generated_clock_constraint(cd.clk, clkin, divide_by=divide,
                    multiply_by=multiply, pin='ae350_pll/CLKOUT%d' % index)
        self.specials += Instance('PLL', name='ae350_pll', **params)


class ClockReset(LiteXModule):
    def __init__(self, platform, fabric_hz=100e6, data_hz=None, input_hz=50e6, cpu_hz=800e6, pixel_hz=25.175e6, reset_active_low=False):
        self.cd_sys = ClockDomain('sys')
        self.cd_cpu = ClockDomain('cpu')
        self.cd_input = ClockDomain('input')
        self.cd_hdmi_pix = ClockDomain('hdmi_pix')
        self.cd_hdmi_5x = ClockDomain('hdmi_5x')
        self.cd_hdmi_reset = ClockDomain('hdmi_reset', reset_less=True)
        clk = platform.request('clk50')
        platform.add_period_constraint(clk, 1e9 / input_hz)
        reset_pad = platform.request('reset_key')
        reset_key = ~reset_pad if reset_active_low else reset_pad
        self.comb += self.cd_input.clk.eq(clk)
        count = Signal(16, reset=65535)
        self.sync.input += If(reset_key, count.eq(65535)).Elif(count != 0, count.eq(count - 1))
        outputs = [(self.cd_sys, fabric_hz), (self.cd_cpu, cpu_hz)]
        if data_hz is not None:
            self.cd_mem = ClockDomain('mem')
            outputs.append((self.cd_mem, data_hz))
        # CORE_CLK stays on CLKOUT1, the dedicated PLL_R[0] path.
        self.pll = pll = FabricPLL(platform, clk, outputs, input_hz)
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

        from gateware.machines.mega138k_ae350.video_modes import MODES
        if int(pixel_hz) != MODES[0].pixel_hz:
            raise ValueError('AE350 boot video clock must match runtime mode zero')
        self.hdmi_pll = hdmi_pll = VideoPLL(clk,
            (self.cd_hdmi_pix,self.cd_hdmi_5x,self.cd_hdmi_reset),input_hz)
        self.video_clock_reset = Signal()
        self.comb += hdmi_pll.reset.eq(reset_key | (count != 0) | self.video_clock_reset)
        self.hdmi_clock_constraints = hdmi_pll.constraints
        self.rtc = Signal()
        phase = Signal(32)
        self.sync.input += phase.eq(phase + round(32768 * 2**32 / input_hz))
        # Give RTC a real clock source net. A combinational alias to phase[31]
        # is removed by Gowin synthesis and leaves an invalid SDC clock object.
        self.specials += Instance('DFF', name='rtc_buffer', i_CLK=clk, i_D=phase[31], o_Q=self.rtc)
        platform.add_period_constraint(self.rtc, 1e9 / 32768)
