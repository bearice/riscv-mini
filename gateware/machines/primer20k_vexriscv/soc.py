"""60/120 MHz boot kernel with independently selected peripheral modules."""
from gateware.machines.primer20k_vexriscv.address_map import AddressMap
from pathlib import Path
from migen import ClockDomain, Signal, Instance, Cat, Mux
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
from gateware.gowin_platform import create_platform
from gateware.machines.primer20k_vexriscv.ports import bind_ports
from gateware.vendor.gowin_gw2a.gw2ddrphy import GW2DDRPHY
from gateware.machines.primer20k_vexriscv.memory.gw2_ddr import H5TQ1G63EFR
from gateware.machines.primer20k_vexriscv.ddr_boot import SoftwareDDRCore
from gateware.machines.primer20k_vexriscv.constraints import add_ddr_init_exceptions
from gateware.machines.primer20k_vexriscv.peripherals.video import RGBLCD
from gateware.machines.primer20k_vexriscv.peripherals.board_io import BoardIO, WS2812
from gateware.machines.primer20k_vexriscv.peripherals.sd import NativeSD
from gateware.machines.primer20k_vexriscv.peripherals.audio import Audio
from gateware.machines.primer20k_vexriscv.peripherals.microphone import Microphone
from gateware.machines.primer20k_vexriscv.peripherals.ethernet import add_ethernet
from gateware.machines.primer20k_vexriscv.peripherals.usb import add_usb
from gateware.machines.primer20k_vexriscv.features import Features
from gateware.machines.primer20k_vexriscv.config import cpu_capabilities
from gateware.machines.primer20k_vexriscv.flash_xip import FLASH_BASE, FLASH_SIZE, XIP_OFFSET
from gateware.machines.primer20k_vexriscv.peripherals.audio_clock import AudioDDS

class ClockResetGenerator(LiteXModule):
    def __init__(self,platform,features,machine,usb_backend='ultra'):
        self.cd_sys=ClockDomain('sys');self.cd_por=ClockDomain('por')
        self.cd_init=ClockDomain('init');self.cd_sys2x=ClockDomain('sys2x')
        self.cd_sys2x_i=ClockDomain('sys2x_i')
        config = machine
        board = config.board
        clock=platform.request('clk27'); reset_pad=platform.request('btn_n',0)
        reset_n = reset_pad if board.resources['core_reset'].signals['reset'].active_low else ~reset_pad
        counter=Signal(16,reset=65535)
        self.comb += self.cd_por.clk.eq(clock)
        self.sync.por += counter.eq(counter-(counter!=0))
        self.pll=pll=GW2APLL(devicename=platform.devicename,device=platform.device)
        self.comb += pll.reset.eq(counter!=0)
        self.stop=Signal();self.reset=Signal()
        # SoCController's full reset also quiesces DMA and restarts DDR boot.
        # Keep the init/DLL clock alive while resetting the sys peripherals.
        self.rst=Signal()
        pll.register_clkin(clock,board.input_clock_hz)
        pll.create_clkout(self.cd_sys2x_i,config.clocks.sys*2,margin=0,with_reset=False)
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
            vp.register_clkin(clock,board.input_clock_hz);vp.create_clkout(self.cd_video,config.clocks.video,margin=0,with_reset=False)
            video_reset=Signal(name_override='lcd_video_async_reset');video_reset.attr.add('keep')
            self.comb += video_reset.eq(~vp.locked|self.cd_sys.rst)
            self.specials += AsyncResetSynchronizer(self.cd_video,video_reset)
        if features.usb and usb_backend=='ohci':
            self.cd_usb=ClockDomain('usb')
            self.usb_pll=up=GW2APLL(devicename=platform.devicename,device=platform.device)
            self.comb += up.reset.eq((counter!=0)|~reset_n)
            up.register_clkin(clock,board.input_clock_hz);up.create_clkout(self.cd_usb,48e6,margin=0,with_reset=False)

class SDControl(LiteXModule):
    def __init__(self,reset):
        self._reset=CSRStorage(name="reset")
        self.comb += reset.eq(self._reset.storage)

class MiniSoC(SoCCore):
    def __init__(self, machine, rom_data=None, cpu_verilog=None):
        config = machine
        settings = config.rtl
        features = Features(**settings.features)
        cpu_variant = settings.cpu_variant
        sd_profile = settings.sd_profile
        sd_backend = 'native' if sd_profile in ('lite', 'full') else sd_profile
        usb_backend = settings.usb_backend
        audio_clock = settings.audio_clock
        l2_size = settings.l2_size_bytes
        rom_size = config.boot.rom_size_bytes
        boot_mode = config.boot.mode
        if 'cpu' not in config.artifacts:
            raise ValueError('MiniSoC requires the CPU IP resolved from machine.json')
        selected_cpu = Path(__file__).resolve().parents[3] / settings.cpu_verilog
        if cpu_verilog is None:
            cpu_verilog = selected_cpu
        elif Path(cpu_verilog).read_bytes() != selected_cpu.read_bytes():
            raise ValueError('CPU RTL differs from the resolved machine IP')
        if l2_size not in (4096,8192,16384):raise ValueError('Software DDR boot supports 4, 8 or 16 KiB shared L2')
        if rom_size not in (4096,8192):raise ValueError('Boot ROM size must be 4 or 8 KiB')
        if boot_mode not in ('rom','xip'):raise ValueError('Invalid boot mode')
        # Validate feature selections and select matching CPU RTL.
        features = features or Features()
        if boot_mode=='xip' and not features.flash:raise ValueError('XIP requires Flash')
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
        config = machine
        self.address_map = AddressMap(config.memory)
        RAM_BASE, RAM_SIZE = self.address_map.RAM_BASE, self.address_map.RAM_SIZE
        self.mem_map = {**SoCCore.mem_map, "rom": FLASH_BASE, "main_ram": RAM_BASE}
        board = config.board
        self.io = bind_ports(config)
        platform = create_platform(self.io, 'clk27', dict(use_mspi_as_gpio=1, use_sspi_as_gpio=1,
            use_ready_as_gpio=1, use_done_as_gpio=1, rw_check_on_ram=1))
        resources, bindings = board.resources, config.devices
        gpio = config.gpio
        sys_hz = config.clocks.sys
        self.crg=ClockResetGenerator(platform,features,config,usb_backend)
        # SUG1220: prioritize timing over compilation speed at high utilization.
        platform.toolchain.options.update(timing_driven=1,**config.rtl.implementation_options())
        from litex.soc.cores.cpu.vexriscv.core import VexRiscv
        io_regions={0xf0000000:0x02000000,0xf2000000:0x01000000,
                    0xf3400000:0x00400000,0xf3800000:0x00800000,
                    0xf4000000:0x04000000,0xf8000000:0x08000000}
        previous_io_regions=VexRiscv.io_regions
        VexRiscv.io_regions=io_regions
        try:
            SoCCore.__init__(
                self, platform, clk_freq=sys_hz, ident='riscv-mini',
                cpu_type=config.cpu.implementation, cpu_variant=cpu_variant,
                integrated_rom_size=rom_size if boot_mode=='rom' else 0,
                integrated_rom_init=rom_data or [],
                cpu_reset_address=FLASH_BASE if boot_mode=='rom' else FLASH_BASE+XIP_OFFSET,
                integrated_sram_size=0, integrated_main_ram_size=0,
                uart_name='serial', uart_baudrate=115200,
                with_timer=True, with_ctrl=True,
            )
        finally:
            VexRiscv.io_regions=previous_io_regions
        # Device MMIO is high; the XIP window remains cacheable.
        self.cpu.io_regions=io_regions
        if cpu_verilog is not None:
            self.cpu.use_external_variant(str(Path(cpu_verilog).resolve()))
        # Independent machine timer for SBI TIME; peripheral IRQ timers remain.
        if features.mmu:
            self.cpu.add_timer()
        capabilities=cpu_capabilities(cpu_verilog) if cpu_verilog else {}
        if cpu_verilog and not capabilities.get('high_mmio_xip'):
            raise ValueError('Regenerate CPU RTL for the default RAM-zero/high-MMIO memory map')
        self.add_constant('CONFIG_CPU_COMPRESSED',int(capabilities.get('compressed',False)))
        self.add_constant('CONFIG_CPU_BITMANIP',sum(1<<i for i,name in enumerate(('Zba','Zbb','Zbs'))
            if name in capabilities.get('bitmanip',[])))
        add_ddr_init_exceptions(platform,with_video=features.video,usb_backend=usb_backend)

        # DDR PHY, shared native port and CPU memory bridge.
        self.ddrphy=GW2DDRPHY(platform.request('ddram'),sys_clk_freq=sys_hz,dll_off=True,cl=6,cwl=6)
        self.ddrphy.settings.rtt_nom='disabled'
        self.ddrphy.settings.rtt_wr='disabled'
        self.comb += [self.crg.stop.eq(self.ddrphy.init.stop),self.crg.reset.eq(self.ddrphy.init.reset)]
        self.sdram=SoftwareDDRCore(self.ddrphy,H5TQ1G63EFR(sys_hz,'1:2'),self.sys_clk_freq)
        # The memory controller owns client burst buffers and the shared cache.
        dma_sd=features.sd and sd_profile=='lite'
        backend=self.sdram.crossbar.get_port()
        self.add_constant('CONFIG_DMA_NATIVE',int(dma_sd))
        self.add_constant('CONFIG_BUFFERED_MEMORY_PORTS',1)
        wb_ram=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        self.bus.add_slave(name='main_ram',slave=wb_ram,
            region=SoCRegion(origin=RAM_BASE,size=RAM_SIZE))
        ram_request=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        self.add_constant('CONFIG_L2_SIZE',l2_size)
        self.add_constant('CONFIG_L2_MODE',2)
        from gateware.machines.primer20k_vexriscv.memory.ports import SharedMemoryController
        self.comb += wb_ram.connect(ram_request)
        self.memory=SharedMemoryController(ram_request,backend,size=l2_size,
            enabled=self.sdram.boot.ready,video=features.video,sd=dma_sd,eth=features.eth_dma, layout=self.address_map)
        self.l2=self.memory.l2
        sd_ports=(self.memory.sd_read,self.memory.sd_write) if dma_sd else None
        self.add_constant('CONFIG_SHARED_L2',1)

        # System timers and board IO.
        self.timer0.add_uptime()
        self.timer1=Timer()
        self.irq.add('timer1',use_loc_if_exists=True)

        if features.board_io:
            leds = Cat(*[platform.request('board_led', i) for i in range(len(gpio.leds))])
            buttons = Cat(*[platform.request('btn_n', i + 1) for i in range(len(gpio.buttons))])
            switches = Cat(*[platform.request('board_switch', i) for i in range(len(gpio.switches))])
            led_mask = sum(int(resources[ref].signals['pad'].active_low) << i for i, ref in enumerate(gpio.leds))
            button_mask = sum(int(resources[ref].signals['pad'].active_low) << i for i, ref in enumerate(gpio.buttons))
            switch_mask = sum(int(resources[ref].signals['pad'].active_low) << i for i, ref in enumerate(gpio.switches))
            self.board_io=BoardIO(leds, buttons, switches,
                debounce_cycles=round(sys_hz * gpio.debounce_ms / 1000),
                led_active_low_mask=led_mask, button_active_low_mask=button_mask,
                switch_active_low_mask=switch_mask)
            self.irq.add('board_io',use_loc_if_exists=True)
        if features.ws2812:
            self.ws2812=WS2812(platform.request('rgb_led'), clock_hz=sys_hz)

        # SPI Flash and SD storage.
        if features.flash:
            flash_pads=platform.request('spiflash')
            self.flash_spi=SPIMaster(flash_pads if boot_mode=='rom' else None,32,sys_hz,10e6,with_csr=False,mode='aligned')
            self.flash_spi.add_csr(with_loopback=False)
            self.flash_spi.add_clk_divider()
            if boot_mode=='xip':
                from gateware.machines.primer20k_vexriscv.flash_xip import FlashXIP
                self.flash_xip=FlashXIP(flash_pads,self.flash_spi.pads)
                self.bus.add_slave(name='spiflash',slave=self.flash_xip.bus,
                    region=SoCRegion(origin=FLASH_BASE,size=FLASH_SIZE,mode='r',cached=True))
        self.add_constant('CONFIG_BOOT_XIP',int(boot_mode=='xip'))
        self.add_constant('CONFIG_SD_NATIVE',int(sd_backend=='native'))
        self.add_constant('CONFIG_SD_LITE',int(sd_profile=='lite'))
        self.add_constant('CONFIG_SD_PROFILE',('none','spi','lite','full').index(sd_profile))
        if features.sd:
            if sd_backend=='native':
                self.sdcard=NativeSD(self,profile=sd_profile,native_ports=sd_ports)
                self.irq.add("sdcard",use_loc_if_exists=True)
                self.sd_control=SDControl(self.sdcard.reset)
                if dma_sd:self.comb += self.memory.sd_reset.eq(self.sdcard.reset)
            else:
                self.spisdcard = SPIMaster(
                    platform.request('spisdcard'), 8, sys_hz, 400e3,
                    with_csr=False, mode='aligned',
                )
                self.spisdcard.add_csr(with_loopback=False)
                self.spisdcard.add_clk_divider()
                detect_pad=platform.request('sd_detect')
                # Hierarchical instance sd_detect must not shadow its input port.
                detect_pad.name_override='sd_detect_pad'
                detect_active_low=resources[bindings.sd].signals['detect'].active_low
                self.sd_detect=GPIOIn(detect_pad if detect_active_low else ~detect_pad)

        # SPI LCD and DDR-backed RGB LCD.
        if features.spi_lcd:
            lcd=platform.request('spi_lcd')
            lcd.miso=Signal()
            self.comb += lcd.miso.eq(0)
            self.lcd_spi=SPIMaster(lcd,16,sys_hz,6e6,with_csr=False,mode='aligned')
            self.lcd_spi.add_csr(with_loopback=False)
            self.lcd_spi.add_clk_divider()
            self.lcd_gpio=GPIOOut(Cat(lcd.dc,lcd.rst_n,lcd.bl_n),reset=4)
        if features.video:
            self.rgb_lcd=RGBLCD(self.memory.video,platform.request('rgb_lcd'),
                               video_clk_freq=config.clocks.video, layout=self.address_map)

        # Shared audio reference, DAC output and microphone inputs.
        audio_tick=mic_tick=1
        if audio_clock=='dds' and (features.audio or features.mic):
            self.audio_reference=AudioDDS(self.sys_clk_freq)
            audio_tick=self.audio_reference.dac_tick
            mic_tick=self.audio_reference.mic_tick
        self.add_constant('CONFIG_AUDIO_DDS',int(audio_clock=='dds' and (features.audio or features.mic)))
        self.add_constant('AUDIO_REFERENCE_HZ',int(self.sys_clk_freq))
        if features.audio:
            audio_bus=wishbone.Interface(data_width=32,address_width=32,addressing='word',mode='r')
            self.bus.add_master(name='audio_dma',master=audio_bus)
            self.audio = Audio(
                platform.request('audio_dac'), audio_bus,
                half_period=20 if audio_clock=='legacy' else 1,
                clock_enable=audio_tick, layout=self.address_map,
            )
            self.add_constant('AUDIO_SAMPLE_RATE',46875 if audio_clock=='legacy' else 48000)
            self.add_constant('AUDIO_FIFO_FRAMES',512)
        if features.mic:
            self.mic = Microphone(
                platform.request('microphone'),
                second=platform.request('microphone_second') if features.mic_stereo else None,
                half_period=10 if audio_clock=='legacy' else 1,
                clock_enable=mic_tick,
            )
        self.add_constant('MIC_SAMPLE_RATE',46875 if audio_clock=='legacy' else 48000)
        self.add_constant('MIC_SNAPSHOT_SAMPLES',512)

        # Shared PHY reset, Ethernet and USB.
        # One output owns F10: both Ethernet and USB PHYs reset together.
        if features.eth or features.usb:
            self.phy_reset=GPIOOut(platform.request('shared_phy_reset_n'),reset=0)
        if features.eth:
            add_ethernet(self)
        if features.usb:
            add_usb(self,usb_backend,compact_serial_outputs=True)

        # Firmware feature constants.
        for name,value in features.as_dict().items():
            self.add_constant("MINI_FEATURE_"+name.upper(),int(value))
