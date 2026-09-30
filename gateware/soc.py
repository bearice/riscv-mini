"""Single 60/120 MHz base system with Flash/UART boot and existing board IO."""
import json
from pathlib import Path
from migen import ClockDomain, Signal, Instance, Cat
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.soc.cores.clock.gowin_gw2a import GW2APLL
from litex.soc.integration.soc_core import SoCCore
from litex.soc.integration.soc import SoCRegion
from litex.soc.interconnect import wishbone
from litex.build.generic_platform import Pins, Subsignal, IOStandard
from litex.soc.cores.spi import SPIMaster
from litex.soc.cores.gpio import GPIOOut, GPIOIn
from litex_boards.platforms.sipeed_tang_primer_20k import Platform
from litedram.frontend.wishbone import LiteDRAMWishbone2Native
from gateware.vendor.gw2ddrphy import GW2DDRPHY
from gateware.ddr import H5TQ1G63EFR
from gateware.constraints import add_ddr_init_exceptions
from gateware.memory import SharedNativePort
from gateware.video import RGBLCD

class ClockResetGenerator(LiteXModule):
    def __init__(self,platform):
        self.cd_sys=ClockDomain('sys');self.cd_por=ClockDomain('por')
        self.cd_init=ClockDomain('init');self.cd_sys2x=ClockDomain('sys2x')
        self.cd_sys2x_i=ClockDomain('sys2x_i');self.cd_video=ClockDomain('video')
        clock=platform.request('clk27');reset_n=platform.request('btn_n',0)
        counter=Signal(16,reset=65535)
        self.comb += self.cd_por.clk.eq(clock)
        self.sync.por += counter.eq(counter-(counter!=0))
        self.pll=pll=GW2APLL(devicename=platform.devicename,device=platform.device)
        self.comb += pll.reset.eq(counter!=0)
        self.stop=Signal();self.reset=Signal()
        pll.register_clkin(clock,27e6)
        pll.create_clkout(self.cd_sys2x_i,120e6,margin=0,with_reset=False)
        self.specials += [
            Instance('DHCEN',i_CLKIN=self.cd_sys2x_i.clk,i_CE=self.stop,o_CLKOUT=self.cd_sys2x.clk),
            Instance('CLKDIV',p_DIV_MODE='2',i_CALIB=0,i_HCLKIN=self.cd_sys2x.clk,
                     i_RESETN=~self.reset,o_CLKOUT=self.cd_sys.clk),
            AsyncResetSynchronizer(self.cd_sys,~pll.locked|~reset_n|self.reset)]
        self.comb += [self.cd_init.clk.eq(clock),self.cd_init.rst.eq(pll.reset)]
        self.video_pll=vp=GW2APLL(devicename=platform.devicename,device=platform.device)
        self.comb += vp.reset.eq((counter!=0)|~reset_n)
        vp.register_clkin(clock,27e6);vp.create_clkout(self.cd_video,9e6,margin=0,with_reset=False)
        video_reset=Signal(name_override='lcd_video_async_reset');video_reset.attr.add('keep')
        self.comb += video_reset.eq(~vp.locked|self.cd_sys.rst)
        self.specials += AsyncResetSynchronizer(self.cd_video,video_reset)

class MiniSoC(SoCCore):
    def __init__(self,rom_data=None):
        platform=Platform(dock='standard',toolchain='gowin');self.crg=ClockResetGenerator(platform)
        SoCCore.__init__(self,platform,clk_freq=60e6,ident='riscv-mini base: Flash/UART -> DDR',
            cpu_type='vexriscv',cpu_variant='lite',integrated_rom_size=8*1024,
            integrated_rom_init=rom_data or [],integrated_sram_size=8*1024,
            integrated_main_ram_size=0,uart_name='serial',uart_baudrate=115200,
            with_timer=True,with_ctrl=True)
        add_ddr_init_exceptions(platform,with_video=True)
        self.ddrphy=GW2DDRPHY(platform.request('ddram'),sys_clk_freq=60e6,dll_off=True,cl=6,cwl=6)
        self.ddrphy.settings.rtt_nom='disabled';self.ddrphy.settings.rtt_wr='disabled'
        self.comb += [self.crg.stop.eq(self.ddrphy.init.stop),self.crg.reset.eq(self.ddrphy.init.reset)]
        self.add_sdram('sdram',phy=self.ddrphy,module=H5TQ1G63EFR(60e6,'1:2'),
                       l2_cache_size=0,with_soc_interconnect=False)
        self.memory_port=SharedNativePort(self.sdram.crossbar.get_port())
        wb_ram=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        self.bus.add_slave(name='main_ram',slave=wb_ram,region=SoCRegion(origin=0x40000000,size=128*1024*1024))
        wb_native=wishbone.Interface(data_width=128,address_width=32,addressing='word')
        self.submodules += wishbone.Converter(wb_ram,wb_native)
        self.wishbone_bridge=LiteDRAMWishbone2Native(wb_native,self.memory_port.cpu,base_address=0x40000000)
        platform.add_extension([
            ('spi_lcd',0,Subsignal('clk',Pins('F12')),Subsignal('mosi',Pins('L15')),
             Subsignal('cs_n',Pins('C16')),Subsignal('dc',Pins('J13')),
             Subsignal('rst_n',Pins('G13')),Subsignal('bl_n',Pins('P12')),IOStandard('LVCMOS33')),
            ('sd_detect',0,Pins('D15'),IOStandard('LVCMOS33'))])
        lcd=platform.request('spi_lcd');lcd.miso=Signal();self.comb += lcd.miso.eq(0)
        self.lcd_spi=SPIMaster(lcd,16,60e6,6e6,with_csr=False,mode='aligned')
        self.lcd_spi.add_csr(with_loopback=False);self.lcd_spi.add_clk_divider()
        self.lcd_gpio=GPIOOut(Cat(lcd.dc,lcd.rst_n,lcd.bl_n),reset=4)
        self.spisdcard=SPIMaster(platform.request('spisdcard'),8,60e6,400e3,with_csr=False,mode='aligned')
        self.spisdcard.add_csr(with_loopback=False);self.spisdcard.add_clk_divider()
        self.sd_detect=GPIOIn(platform.request('sd_detect'))
        self.flash_spi=SPIMaster(platform.request('spiflash'),32,60e6,10e6,with_csr=False,mode='aligned')
        self.flash_spi.add_csr(with_loopback=False);self.flash_spi.add_clk_divider()
        pins=json.loads(Path(__file__).with_name('board.json').read_text())['video']['rgb_lcd']['pins']
        platform.add_extension([('rgb_lcd',0,
            *[Subsignal(name,Pins(pins[name])) for name in ('clk','hsync','vsync','de')],
            *[Subsignal(color,Pins(' '.join(pins[color+'_lsb_first']))) for color in 'rgb'],IOStandard('LVCMOS33'))])
        self.rgb_lcd=RGBLCD(self.memory_port.video,platform.request('rgb_lcd'))
