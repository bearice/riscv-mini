"""P1 DDR-independent ROM/RAM system using the AE350 silicon macro."""
from migen import ClockDomain, Signal, If, ResetSignal, Constant, Instance
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.build.generic_platform import Pins, IOStandard, Subsignal, Misc
from litex.build.gowin.platform import GowinPlatform
from litex.soc.cores.clock.gowin_gw5a import GW5APLL
from .ae350_native import NativeAE350, AHBMemory, AE350ROM, APB_CE_APB, APB_CE_UART2, APB_CE_PIT, APB_CE_GPIO


class ClockReset(LiteXModule):
    def __init__(self, platform):
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
        pll.create_clkout(self.cd_sys, 100e6)
        pll.create_clkout(self.cd_cpu, 800e6, with_reset=False)
        self.comb += pll.reset.eq(reset_key | (count != 0))
        self.specials += AsyncResetSynchronizer(self.cd_cpu, ~pll.locked | pll.reset)
        platform.toolchain.additional_cst_commands.append('INS_LOC "ae350_pll" PLL_R[0];')
        pll.add_generated_clock_constraints(platform)
        self.rtc = Signal()
        phase = Signal(32)
        self.sync.input += phase.eq(phase + round(32768 * 2**32 / 50000000))
        # Give RTC a real clock source net. A combinational alias to phase[31]
        # is removed by Gowin synthesis and leaves an invalid SDC clock object.
        self.specials += Instance('DFF', name='rtc_buffer', i_CLK=clk, i_D=phase[31], o_Q=self.rtc)
        platform.add_period_constraint(self.rtc, 1e9 / 32768)


class MinimalSoC(LiteXModule):
    def __init__(self, board, rom):
        resources = board['resources']
        uart = resources['debug_uart']
        jtag = resources['cpu_jtag']
        io = [
            ('clk50', 0, Pins('P16'), IOStandard('LVCMOS33')),
            ('reset_key', 0, Pins('U4'), IOStandard('LVCMOS15')),
            ('serial', 0, Subsignal('rx', Pins(uart['rx'])), Subsignal('tx', Pins(uart['tx'])), IOStandard('LVCMOS33')),
            ('cpu_jtag', 0, Subsignal('tck', Pins(jtag['tck']['pin']), Misc('PULL_MODE=DOWN')),
             Subsignal('tms', Pins(jtag['tms']['pin']), Misc('PULL_MODE=UP')),
             Subsignal('tdi', Pins(jtag['tdi']['pin']), Misc('PULL_MODE=UP')),
             Subsignal('tdo', Pins(jtag['tdo']['pin'])), IOStandard(jtag['io_standard'])),
            ('fan_pwm', 0, Pins('T18'), IOStandard('LVCMOS33')),
        ]
        platform = GowinPlatform(board['reference_part'], io, devicename=board['reference_device'])
        platform.toolchain.options.update(use_cpu_as_gpio=1, use_mspi_as_gpio=1, use_sspi_as_gpio=1,
                                          place_option=2, route_option=2)
        platform.default_clk_name = 'clk50'
        platform.default_clk_period = 20
        self.platform = platform
        self.crg = ClockReset(platform)
        self.cpu = NativeAE350()
        self.comb += [self.cpu.ahb_rom.sel.eq(1), self.cpu.ahb_ram.sel.eq(1)]
        self.rom = AE350ROM(self.cpu.ahb_rom, 8192, init=rom)
        self.sram = AHBMemory(self.cpu.ahb_ram, 8192)
        serial = platform.request('serial')
        self.cpu.cpu_params.update(i_UART2_RXD=serial.rx, o_UART2_TXD=serial.tx,
                                   i_APB_CE=Constant(APB_CE_APB | APB_CE_UART2 | APB_CE_PIT | APB_CE_GPIO, 8),
                                   i_RTC_CLK=self.crg.rtc,
                                   i_POR_N=~ResetSignal('sys'))
        self.cpu.connect_jtag(platform.request('cpu_jtag'))
        platform.add_period_constraint(platform.lookup_request('cpu_jtag').tck, 10000)
        self.comb += platform.request('fan_pwm').eq(1)
