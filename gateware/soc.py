"""M0 CPU/UART/timer SoC. No DDR or display is enabled at bootstrap."""
from migen import ClockDomain, Signal, Instance, Cat
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.soc.cores.clock.gowin_gw2a import GW2APLL
from litex.soc.integration.soc_core import SoCCore
from litex_boards.platforms.sipeed_tang_primer_20k import Platform


class ClockResetGenerator(LiteXModule):
    def __init__(self, platform, frequency, with_ddr=False):
        self.cd_sys = ClockDomain('sys')
        self.cd_por = ClockDomain('por')
        clock = platform.request('clk27')
        reset_n = platform.request('btn_n', 0)
        counter = Signal(16, reset=65535)
        self.comb += self.cd_por.clk.eq(clock)
        self.sync.por += counter.eq(counter - (counter != 0))
        self.pll = pll = GW2APLL(devicename=platform.devicename, device=platform.device)
        self.comb += pll.reset.eq(counter != 0)
        pll.register_clkin(clock, 27e6)
        # We own sys reset below, combining PLL lock and the physical reset button.
        self.stop = Signal()
        self.reset = Signal()
        if with_ddr:
            self.cd_init = ClockDomain('init')
            self.cd_sys2x = ClockDomain('sys2x')
            self.cd_sys2x_i = ClockDomain('sys2x_i')
            pll.create_clkout(self.cd_sys2x_i, 2*frequency, with_reset=False)
            self.specials += [
                Instance('DHCEN', i_CLKIN=self.cd_sys2x_i.clk, i_CE=self.stop,
                         o_CLKOUT=self.cd_sys2x.clk),
                Instance('CLKDIV', p_DIV_MODE='2', i_CALIB=0,
                         i_HCLKIN=self.cd_sys2x.clk, i_RESETN=~self.reset,
                         o_CLKOUT=self.cd_sys.clk)]
            self.comb += [self.cd_init.clk.eq(clock), self.cd_init.rst.eq(pll.reset)]
            # Gowin derives rPLL/CLKDIV clocks; the fabric net is optimized away
            # during PnR and cannot be constrained by its pre-synthesis name.
        else:
            pll.create_clkout(self.cd_sys, frequency, with_reset=False)
        self.specials += AsyncResetSynchronizer(self.cd_sys, ~pll.locked | ~reset_n | self.reset)


class MiniSoC(SoCCore):
    def __init__(self, rom_data=None, with_ddr=False, with_io=False):
        platform = Platform(dock='standard', toolchain='gowin')
        self.crg = ClockResetGenerator(platform, 48e6, with_ddr)
        SoCCore.__init__(
            self, platform, clk_freq=48e6,
            ident=f'riscv-mini {"M2" if with_io else "M1" if with_ddr else "M0"}: Tang Primer 20K + Dock 3713',
            cpu_type='vexriscv', cpu_variant='lite',
            integrated_rom_size=32*1024,
            integrated_rom_init=rom_data or [],
            integrated_sram_size=16*1024,
            integrated_main_ram_size=0,
            uart_name='serial', uart_baudrate=115200,
            with_timer=True, with_ctrl=True,
        )
        if with_ddr:
            from gateware.vendor.gw2ddrphy import GW2DDRPHY
            from gateware.ddr import H5TQ1G63EFR
            from gateware.constraints import add_ddr_init_exceptions
            add_ddr_init_exceptions(platform)
            self.ddrphy = GW2DDRPHY(platform.request('ddram'), sys_clk_freq=48e6, dll_off=True)
            self.ddrphy.settings.rtt_nom = 'disabled'
            self.ddrphy.settings.rtt_wr = 'disabled'
            self.comb += [self.crg.stop.eq(self.ddrphy.init.stop),
                          self.crg.reset.eq(self.ddrphy.init.reset)]
            self.add_sdram('sdram', phy=self.ddrphy,
                           module=H5TQ1G63EFR(48e6, '1:2'), l2_cache_size=0)
        if with_io:
            from litex.build.generic_platform import Pins, Subsignal, IOStandard
            from litex.soc.cores.spi import SPIMaster
            from litex.soc.cores.gpio import GPIOOut, GPIOIn
            platform.add_extension([
                ('spi_lcd', 0, Subsignal('clk', Pins('F12')),
                 Subsignal('mosi', Pins('L15')), Subsignal('cs_n', Pins('C16')),
                 Subsignal('dc', Pins('J13')), Subsignal('rst_n', Pins('G13')),
                 Subsignal('bl_n', Pins('P12')), IOStandard('LVCMOS33')),
                ('sd_detect', 0, Pins('D15'), IOStandard('LVCMOS33'))])
            lcd = platform.request('spi_lcd')
            lcd.miso = Signal()
            self.comb += lcd.miso.eq(0)
            self.lcd_spi = SPIMaster(lcd, 16, 48e6, 6e6, mode='aligned')
            self.lcd_spi.add_clk_divider()
            self.lcd_gpio = GPIOOut(Cat(lcd.dc, lcd.rst_n, lcd.bl_n), reset=4)
            self.add_spi_sdcard(spi_clk_freq=400e3)
            self.sd_detect = GPIOIn(platform.request('sd_detect'))
