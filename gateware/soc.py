"""60/120 MHz boot kernel with independently selected peripheral modules."""
import json
from pathlib import Path
from migen import ClockDomain, Signal, Instance, Cat
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.soc.cores.clock.gowin_gw2a import GW2APLL
from litex.soc.integration.soc_core import SoCCore
from litex.soc.integration.soc import SoCRegion
from litex.soc.interconnect import wishbone
from litex.build.generic_platform import Pins, Subsignal, IOStandard, Misc
from litex.soc.cores.spi import SPIMaster
from litex.soc.cores.gpio import GPIOOut, GPIOIn
from litex.soc.cores.timer import Timer
from litex.soc.interconnect.csr import CSRStorage
from litex_boards.platforms.sipeed_tang_primer_20k import Platform
from litedram.frontend.wishbone import LiteDRAMWishbone2Native
from gateware.vendor.gw2ddrphy import GW2DDRPHY
from gateware.ddr import H5TQ1G63EFR
from gateware.ddr_boot import HardwareDDRCore
from gateware.constraints import add_ddr_init_exceptions
from gateware.memory import SharedNativePort
from gateware.l2 import ReadL2
from gateware.video import RGBLCD
from gateware.board_io import BoardIO, WS2812
from gateware.sd import NativeSD
from gateware.audio import Audio
from gateware.microphone import Microphone
from gateware.ethernet import add_ethernet
from gateware.usb import add_usb
from gateware.bus import WishbonePipeline
from gateware.features import Features
from gateware.config import cpu_capabilities
from gateware.audio_clock import AudioDDS

class ClockResetGenerator(LiteXModule):
    def __init__(self,platform,features,usb_backend='ultra'):
        self.cd_sys=ClockDomain('sys');self.cd_por=ClockDomain('por')
        self.cd_init=ClockDomain('init');self.cd_sys2x=ClockDomain('sys2x')
        self.cd_sys2x_i=ClockDomain('sys2x_i')
        clock=platform.request('clk27');reset_n=platform.request('btn_n',0)
        counter=Signal(16,reset=65535)
        self.comb += self.cd_por.clk.eq(clock)
        self.sync.por += counter.eq(counter-(counter!=0))
        self.pll=pll=GW2APLL(devicename=platform.devicename,device=platform.device)
        self.comb += pll.reset.eq(counter!=0)
        self.stop=Signal();self.reset=Signal()
        # SoCController's full reset also quiesces DMA and restarts DDR boot.
        # Keep the init/DLL clock alive while resetting the sys peripherals.
        self.rst=Signal()
        pll.register_clkin(clock,27e6)
        pll.create_clkout(self.cd_sys2x_i,120e6,margin=0,with_reset=False)
        self.specials += [
            Instance('DHCEN',i_CLKIN=self.cd_sys2x_i.clk,i_CE=self.stop,o_CLKOUT=self.cd_sys2x.clk),
            Instance('CLKDIV',p_DIV_MODE='2',i_CALIB=0,i_HCLKIN=self.cd_sys2x.clk,
                     i_RESETN=~self.reset,o_CLKOUT=self.cd_sys.clk),
            AsyncResetSynchronizer(self.cd_sys,~pll.locked|~reset_n|self.reset|self.rst)]
        self.comb += [self.cd_init.clk.eq(clock),self.cd_init.rst.eq(pll.reset)]
        if features.video:
            self.cd_video=ClockDomain('video')
            self.video_pll=vp=GW2APLL(devicename=platform.devicename,device=platform.device)
            self.comb += vp.reset.eq((counter!=0)|~reset_n)
            vp.register_clkin(clock,27e6);vp.create_clkout(self.cd_video,9e6,margin=0,with_reset=False)
            video_reset=Signal(name_override='lcd_video_async_reset');video_reset.attr.add('keep')
            self.comb += video_reset.eq(~vp.locked|self.cd_sys.rst)
            self.specials += AsyncResetSynchronizer(self.cd_video,video_reset)
        if features.usb and usb_backend=='ohci':
            self.cd_usb=ClockDomain('usb')
            self.usb_pll=up=GW2APLL(devicename=platform.devicename,device=platform.device)
            self.comb += up.reset.eq((counter!=0)|~reset_n)
            up.register_clkin(clock,27e6);up.create_clkout(self.cd_usb,48e6,margin=0,with_reset=False)

class SDControl(LiteXModule):
    def __init__(self,reset):
        self._reset=CSRStorage(name="reset")
        self.comb += reset.eq(self._reset.storage)

class MiniSoC(SoCCore):
    def __init__(
        self,
        rom_data=None,
        sd_backend="native",
        features=None,
        cpu_variant='lite',
        cpu_verilog=None,
        usb_backend='ultra',
        sd_profile=None,
        audio_clock='dds',
        l2_size=0,
        l2_mode='burst-refill',
    ):
        # Validate feature selections and select matching CPU RTL.
        features = features or Features()
        if cpu_verilog is None and (features.mmu or features.fpu):
            from gateware.config import cpu_filename
            cpu_verilog = (
                Path(__file__).resolve().parents[1] / 'build/cpu-features'
                / cpu_filename(features.mmu, features.fpu)
            )
            if not cpu_verilog.is_file():
                raise ValueError('Generate matching CPU RTL with scripts/cpu_generate.py first')
            cpu_variant='linux' if features.mmu else 'full'
        self.features=features
        sd_profile=sd_profile or ('lite' if sd_backend=='native' else sd_backend)
        if sd_profile not in ('none','spi','lite','full'):
            raise ValueError('Invalid SD profile')
        if features.sd != (sd_profile!='none'):
            if not features.sd:
                sd_profile='none'
            else:
                raise ValueError('SD none requires SD feature disabled')
        sd_backend='native' if sd_profile in ('lite','full') else sd_profile
        if audio_clock=='sys':
            audio_clock='dds'
        if audio_clock not in ('dds','legacy'):
            raise ValueError('Invalid audio clock')

        # Platform, clock/reset domains and CPU.
        platform=Platform(dock='standard',toolchain='gowin')
        self.crg=ClockResetGenerator(platform,features,usb_backend)
        # SUG1220: prioritize timing over compilation speed at high utilization.
        platform.toolchain.options.update(timing_driven=1,place_option=3,route_option=2)
        SoCCore.__init__(
            self, platform, clk_freq=60e6, ident='riscv-mini',
            cpu_type='vexriscv', cpu_variant=cpu_variant,
            integrated_rom_size=4*1024, integrated_rom_init=rom_data or [],
            integrated_sram_size=0, integrated_main_ram_size=0,
            uart_name='serial', uart_baudrate=115200,
            with_timer=True, with_ctrl=True,
        )
        if cpu_verilog is not None:
            self.cpu.use_external_variant(str(Path(cpu_verilog).resolve()))
        # Independent machine timer for SBI TIME; peripheral IRQ timers remain.
        if features.mmu:
            self.cpu.add_timer()
        capabilities=cpu_capabilities(cpu_verilog) if cpu_verilog else {}
        self.add_constant('CONFIG_CPU_COMPRESSED',int(capabilities.get('compressed',False)))
        self.add_constant('CONFIG_CPU_BITMANIP',sum(1<<i for i,name in enumerate(('Zba','Zbb','Zbs'))
            if name in capabilities.get('bitmanip',[])))
        add_ddr_init_exceptions(platform,with_video=features.video,usb_backend=usb_backend)

        # DDR PHY, shared native port and CPU memory bridge.
        self.ddrphy=GW2DDRPHY(platform.request('ddram'),sys_clk_freq=60e6,dll_off=True,cl=6,cwl=6)
        self.ddrphy.settings.rtt_nom='disabled'
        self.ddrphy.settings.rtt_wr='disabled'
        self.comb += [self.crg.stop.eq(self.ddrphy.init.stop),self.crg.reset.eq(self.ddrphy.init.reset)]
        self.sdram=HardwareDDRCore(self.ddrphy,H5TQ1G63EFR(60e6,'1:2'),self.sys_clk_freq)
        self.memory_port=SharedNativePort(self.sdram.crossbar.get_port(),enabled=self.sdram.boot.ready,spacing_csr=l2_mode!='baseline')
        if not features.video:
            self.comb += [self.memory_port.video.cmd.valid.eq(0),self.memory_port.video.rdata.ready.eq(1)]
        wb_ram=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        self.bus.add_slave(
            name='main_ram', slave=wb_ram,
            region=SoCRegion(origin=0x40000000, size=128*1024*1024),
        )
        ram_request=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        if l2_mode in ('baseline','burst','burst-refill','prefetch','writeback'):
            self.ram_pipeline=WishbonePipeline(wb_ram,ram_request,burst_read=l2_mode in ('burst','burst-refill'))
        else:
            self.comb += wb_ram.connect(ram_request)
        wb_native=wishbone.Interface(data_width=128,address_width=32,addressing='word')
        self.add_constant('CONFIG_L2_SIZE',l2_size)
        self.add_constant('CONFIG_L2_MODE',{'baseline':0,'burst':1,'writeback':2,'prefetch':3,'burst-refill':4}[l2_mode])
        if l2_size:
            if l2_mode=='writeback':
                from gateware.l2_writeback import WritebackL2
                self.l2=WritebackL2(ram_request,wb_native,size=l2_size)
            else:
                self.l2=ReadL2(ram_request,wb_native,size=l2_size,bursting=l2_mode in ('burst','burst-refill'),prefetch=l2_mode=='prefetch',refill_bypass=l2_mode=='burst-refill')
        else:
            self.submodules += wishbone.Converter(ram_request,wb_native)
        self.wishbone_bridge=LiteDRAMWishbone2Native(wb_native,self.memory_port.cpu,base_address=0x40000000)

        # System timers and board IO.
        self.timer0.add_uptime()
        self.timer1=Timer()
        self.irq.add('timer1',use_loc_if_exists=True)

        platform.add_extension([
            ('board_leds',0,Pins('C13 A13 N16 N14 L14 L16'),IOStandard('LVCMOS33')),
            ('board_switches',0,Pins('E9 E8 T4 T5'),IOStandard('LVCMOS15'))])
        if features.board_io:
            buttons=Cat(*[platform.request('btn_n',i) for i in range(1,5)])
            self.board_io=BoardIO(platform.request('board_leds'),buttons,platform.request('board_switches'))
            self.irq.add('board_io',use_loc_if_exists=True)
        if features.ws2812:
            self.ws2812=WS2812(platform.request('rgb_led'))

        # SPI Flash and SD storage.
        platform.add_extension([
            ('sd_detect',0,Pins('D15'),IOStandard('LVCMOS33'))])
        if features.flash:
            self.flash_spi=SPIMaster(platform.request('spiflash'),32,60e6,10e6,with_csr=False,mode='aligned')
            self.flash_spi.add_csr(with_loopback=False)
            self.flash_spi.add_clk_divider()
        self.add_constant('CONFIG_SD_NATIVE',int(sd_backend=='native'))
        self.add_constant('CONFIG_SD_LITE',int(sd_profile=='lite'))
        self.add_constant('CONFIG_SD_PROFILE',('none','spi','lite','full').index(sd_profile))
        if features.sd:
            if sd_backend=='native':
                self.sdcard=NativeSD(self,profile=sd_profile)
                self.irq.add("sdcard",use_loc_if_exists=True)
                self.sd_control=SDControl(self.sdcard.reset)
            else:
                self.spisdcard = SPIMaster(
                    platform.request('spisdcard'), 8, 60e6, 400e3,
                    with_csr=False, mode='aligned',
                )
                self.spisdcard.add_csr(with_loopback=False)
                self.spisdcard.add_clk_divider()
                detect_pad=platform.request('sd_detect')
                # Hierarchical instance sd_detect must not shadow its input port.
                detect_pad.name_override='sd_detect_pad'
                self.sd_detect=GPIOIn(detect_pad)

        # SPI LCD and DDR-backed RGB LCD.
        platform.add_extension([
            ('spi_lcd',0,Subsignal('clk',Pins('F12')),Subsignal('mosi',Pins('L15')),
             Subsignal('cs_n',Pins('C16')),Subsignal('dc',Pins('J13')),
             Subsignal('rst_n',Pins('G13')),Subsignal('bl_n',Pins('P12')),IOStandard('LVCMOS33'))])
        if features.spi_lcd:
            lcd=platform.request('spi_lcd')
            lcd.miso=Signal()
            self.comb += lcd.miso.eq(0)
            self.lcd_spi=SPIMaster(lcd,16,60e6,6e6,with_csr=False,mode='aligned')
            self.lcd_spi.add_csr(with_loopback=False)
            self.lcd_spi.add_clk_divider()
            self.lcd_gpio=GPIOOut(Cat(lcd.dc,lcd.rst_n,lcd.bl_n),reset=4)
        if features.video:
            pins=json.loads(Path(__file__).with_name('board.json').read_text())['video']['rgb_lcd']['pins']
            platform.add_extension([('rgb_lcd',0,
                *[Subsignal(name,Pins(pins[name])) for name in ('clk','hsync','vsync','de')],
                *[Subsignal(color,Pins(' '.join(pins[color+'_lsb_first']))) for color in 'rgb'],IOStandard('LVCMOS33'))])
            self.rgb_lcd=RGBLCD(self.memory_port.video,platform.request('rgb_lcd'))

        # Shared audio reference, DAC output and microphone inputs.
        audio_tick=mic_tick=1
        if audio_clock=='dds' and (features.audio or features.mic):
            self.audio_reference=AudioDDS(self.sys_clk_freq)
            audio_tick=self.audio_reference.dac_tick
            mic_tick=self.audio_reference.mic_tick
        self.add_constant('CONFIG_AUDIO_DDS',int(audio_clock=='dds' and (features.audio or features.mic)))
        self.add_constant('AUDIO_REFERENCE_HZ',int(self.sys_clk_freq))
        if features.audio:
            platform.add_extension([('audio_dac',0,
                Subsignal('bck',Pins('N15')),Subsignal('din',Pins('P15')),
                Subsignal('ws',Pins('P16')),Subsignal('pa_en',Pins('R16')),IOStandard('LVCMOS33'))])
            audio_bus=wishbone.Interface(data_width=32,address_width=32,addressing='word',mode='r')
            self.bus.add_master(name='audio_dma',master=audio_bus)
            self.audio = Audio(
                platform.request('audio_dac'), audio_bus,
                half_period=20 if audio_clock=='legacy' else 1,
                clock_enable=audio_tick,
            )
            self.add_constant('AUDIO_SAMPLE_RATE',46875 if audio_clock=='legacy' else 48000)
            self.add_constant('AUDIO_FIFO_FRAMES',512)
        if features.mic:
            # User-confirmed wiring: DA/CK/LR/WS = P11/R11/M15/J16.
            platform.add_extension([('microphone',0,
                Subsignal('data',Pins('P11'),Misc('PULL_MODE=DOWN')),
                Subsignal('bck',Pins('R11'),Misc('PULL_MODE=NONE')),
                Subsignal('lr',Pins('M15'),Misc('PULL_MODE=NONE')),
                Subsignal('ws',Pins('J16'),Misc('PULL_MODE=NONE')),IOStandard('LVCMOS33'))])
            if features.mic_stereo:
                platform.add_extension([('microphone_second',0,
                    Subsignal('data',Pins('T6'),Misc('PULL_MODE=DOWN')),
                    Subsignal('bck',Pins('R8'),Misc('PULL_MODE=NONE')),
                    Subsignal('lr',Pins('T8'),Misc('PULL_MODE=NONE')),
                    Subsignal('ws',Pins('P9'),Misc('PULL_MODE=NONE')),IOStandard('LVCMOS33'))])
            self.mic = Microphone(
                platform.request('microphone'),
                second=platform.request('microphone_second') if features.mic_stereo else None,
                half_period=10 if audio_clock=='legacy' else 1,
                clock_enable=mic_tick,
            )
        self.add_constant('MIC_SAMPLE_RATE',46875 if audio_clock=='legacy' else 48000)
        self.add_constant('MIC_SNAPSHOT_SAMPLES',512)

        # Shared PHY reset, Ethernet and USB.
        platform.add_extension([
            ('shared_phy_reset_n',0,Pins('F10'),IOStandard('LVCMOS33'))])
        # One output owns F10: both Ethernet and USB PHYs reset together.
        if features.eth or features.usb:
            self.phy_reset=GPIOOut(platform.request('shared_phy_reset_n'),reset=0)
        if features.eth:
            add_ethernet(self)
        if features.usb:
            add_usb(self,usb_backend)

        # Firmware feature constants.
        for name,value in features.as_dict().items():
            self.add_constant("MINI_FEATURE_"+name.upper(),int(value))
