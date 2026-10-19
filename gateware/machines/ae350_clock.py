"""AE350 platform clock and reset generation."""
from migen import ClockDomain, Signal, If, Instance
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.soc.cores.clock.gowin_gw5a import GW5APLL


class ClockReset(LiteXModule):
    def __init__(self, platform, fabric_hz=100e6, data_hz=None):
        self.cd_sys = ClockDomain('sys')
        self.cd_cpu = ClockDomain('cpu')
        self.cd_input = ClockDomain('input')
        clk = platform.request('clk50')
        platform.add_period_constraint(clk, 20)
        reset_key = platform.request('reset_key')
        self.comb += self.cd_input.clk.eq(clk)
        count = Signal(16, reset=65535)
        self.sync.input += If(reset_key, count.eq(65535)).Elif(count != 0, count.eq(count - 1))
        self.pll = pll = GW5APLL(platform.devicename, platform.device, name='ae350_pll')
        pll.register_clkin(clk, 50e6)
        pll.create_clkout(self.cd_sys, fabric_hz)
        # CORE_CLK stays on CLKOUT1, the dedicated PLL_R[0] path.
        pll.create_clkout(self.cd_cpu, 800e6, with_reset=False)
        if data_hz is not None:
            self.cd_mem = ClockDomain('mem')
            pll.create_clkout(self.cd_mem, data_hz)
        self.comb += pll.reset.eq(reset_key | (count != 0))
        self.specials += AsyncResetSynchronizer(self.cd_cpu, ~pll.locked | pll.reset)
        platform.toolchain.additional_cst_commands.append('INS_LOC "ae350_pll" PLL_R[0];')
        # Keep the fabric/BSRAM clock on the dedicated global clock network.
        fabric_clock = pll.clkouts[0].clk
        fabric_clock.attr.add(('syn_keep', 1))
        platform.add_platform_command('CLOCK_LOC "{clock}" BUFG = CLK;', clock=fabric_clock)
        if data_hz is not None:
            data_clock = pll.clkouts[2].clk
            data_clock.attr.add(('syn_keep', 1))
            platform.add_platform_command('CLOCK_LOC "{clock}" BUFG = CLK;', clock=data_clock)
        pll.add_generated_clock_constraints(platform)
        self.rtc = Signal()
        phase = Signal(32)
        self.sync.input += phase.eq(phase + round(32768 * 2**32 / 50000000))
        # Give RTC a real clock source net. A combinational alias to phase[31]
        # is removed by Gowin synthesis and leaves an invalid SDC clock object.
        self.specials += Instance('DFF', name='rtc_buffer', i_CLK=clk, i_D=phase[31], o_Q=self.rtc)
        platform.add_period_constraint(self.rtc, 1e9 / 32768)
