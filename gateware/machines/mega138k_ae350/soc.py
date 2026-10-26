"""AE350 DDR system with the Gowin DDR3 hard memory interface.

Topology
--------
* SDK configuration Flash XIP, hard ILM/DLM and zero-based DDR; no work SRAM.
* Independent 64-bit data AHB crosses asynchronous FIFOs into
  the 256-bit DDR user interface (100 MHz for 400 MHz CK).
* CPU reset release uses synchronized calibration status and a bounded
  fabric watchdog; the ROM still boots if calibration fails.
* Diagnostics cross through a coherent request/acknowledge snapshot.

Board pins and machine clocks are resolved from the shared JSON contracts.
"""
import os
from pathlib import Path

from migen import *
from migen.fhdl.specials import Tristate
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.genlib.cdc import MultiReg, BusSynchronizer
from litex.gen import LiteXModule, Open
from litex.build.generic_platform import Pins, IOStandard, Subsignal, Misc
from litex.build.gowin.common import gowin_special_overrides
from litex.build.gowin.platform import GowinPlatform
from litex.soc.interconnect.ahb import AHBInterface

from gateware.machines.mega138k_ae350.cpu import NativeAE350
from gateware.machines.mega138k_ae350.interconnect import MemInterconnect, ErrorSlave
from gateware.machines.mega138k_ae350.fabric import AE350Fabric, AckCatch, GowinAckCatch
from gateware.machines.mega138k_ae350.peripherals.board_sense import TempAdc, GowinTempAdc

gowin_special_overrides[AckCatch] = GowinAckCatch
gowin_special_overrides[TempAdc] = GowinTempAdc
from gateware.gowin_platform import create_platform
from gateware.machines.mega138k_ae350.ports import bind_ports
from gateware.machines.mega138k_ae350.address_map import AddressMap
from gateware.machines.mega138k_ae350.clock import ClockReset, PLLStatusSynchronizer
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI, DDR3AHB
from gateware.machines.mega138k_ae350.video_memory import VideoMemoryArbiter
from gateware.machines.mega138k_ae350.peripherals.video import FabricVideo
from gateware.machines.mega138k_ae350.peripherals.sd import FabricSD
from gateware.machines.mega138k_ae350.sd_cdc import SDClockCrossing

DDR3_CTRL = ('cs_n','ras_n','cas_n','we_n','cke','odt','reset_n')



class GowinDDR3(LiteXModule):
    """Gowin_PLL_DDR3 + DDR3_Memory_Interface_Top with an ``ddr`` clock domain."""
    def __init__(self, platform, pads, *, controller_module, pll_module, pll_initialization=False):
        ui = DDR3UI()
        self.ui = ui
        self.clk_out = Signal()
        self.pll_lock = Signal()
        # Keep the vendor lock expression on the synchronizer's data path.
        # Factoring PLL_INIT state into a synchronous RESET pin bypasses the
        # explicitly constrained first-stage data input of the lock receiver.
        self.pll_lock.attr.add(('syn_keep', 1))
        self.init_calib_complete = Signal()

        clk50 = platform.lookup_request('clk50')
        clk_ref = Signal(name_override='ddr3_clk_ref')
        memory_clk = Signal(name_override='ddr3_memory_clk')
        pll_stop = Signal()
        # Vendor Gowin_PLL_DDR3: 50 MHz in, CLKOUT0 50 MHz (IP reference clock),
        # CLKOUT2 = the DDR clock of the selected IP point.
        self.specials += Instance(pll_module, name='ddr3_pll',
            i_clkin=clk50, i_reset=0,
            i_enclk0=1, i_enclk2=pll_stop,
            o_clkout0=clk_ref, o_clkout2=memory_clk, o_lock=self.pll_lock,
            **({'i_init_clk': clk50} if pll_initialization else {}))
        pll_path = 'ddr3_pll/u_pll/PLL_inst' if pll_initialization else 'ddr3_pll/PLL_inst'
        platform.toolchain.additional_cst_commands.append('INS_LOC "%s" PLL_L[0];' % pll_path)

        self.specials += Instance(controller_module, name='ddr3',
            # Clocks / reset / calibration.
            i_clk             = clk_ref,
            i_memory_clk      = memory_clk,
            i_pll_lock        = self.pll_lock,
            o_pll_stop        = pll_stop,
            i_rst_n           = ~ResetSignal('sys'),
            o_clk_out         = self.clk_out,
            o_ddr_rst         = Open(),
            o_init_calib_complete = self.init_calib_complete,
            # User interface.
            o_cmd_ready       = ui.cmd_ready,
            i_cmd             = ui.cmd,
            i_cmd_en          = ui.cmd_en,
            i_addr            = ui.addr,
            o_wr_data_rdy     = ui.wr_data_rdy,
            i_wr_data         = ui.wr_data,
            i_wr_data_en      = ui.wr_data_en,
            i_wr_data_end     = ui.wr_data_end,
            i_wr_data_mask    = ui.wr_data_mask,
            o_rd_data         = ui.rd_data,
            o_rd_data_valid   = ui.rd_data_valid,
            o_rd_data_end     = Open(),
            i_sr_req          = 0,
            i_ref_req         = 0,
            o_sr_ack          = Open(),
            o_ref_ack         = Open(),
            i_burst           = ui.burst,
            # DDR3 pins.
            o_O_ddr_addr      = Cat(pads['addr']),
            o_O_ddr_ba        = Cat(pads['ba']),
            o_O_ddr_cs_n      = pads['cs_n'],
            o_O_ddr_ras_n     = pads['ras_n'],
            o_O_ddr_cas_n     = pads['cas_n'],
            o_O_ddr_we_n      = pads['we_n'],
            o_O_ddr_clk       = pads['ck'],
            o_O_ddr_clk_n     = pads['ck_n'],
            o_O_ddr_cke       = pads['cke'],
            o_O_ddr_odt       = pads['odt'],
            o_O_ddr_reset_n   = pads['reset_n'],
            o_O_ddr_dqm       = Cat(pads['dm']),
            io_IO_ddr_dq      = Cat(pads['dq']),
            io_IO_ddr_dqs     = Cat(pads['dqs']),
            io_IO_ddr_dqs_n   = Cat(pads['dqs_n']),
        )
        platform.toolchain.additional_cst_commands.extend([
            'INS_LOC "ddr3/gw3_top/u_ddr_phy_top/u_dll" DDRDLLM_BL;',
            'INS_LOC "ddr3/gw3_top/u_ddr_phy_top/fclkdiv" LEFTSIDE[4];',
        ])


class DDRSoC(LiteXModule):
    def __init__(self, machine=None, soc_sources_dir=None, implementation_options=None):
        config = machine
        if config is None:
            raise ValueError('DDRSoC requires an explicitly resolved machine')
        missing = {'cpu', 'ddr3', 'ddr_pll'} - config.artifacts.keys()
        if missing:
            raise ValueError('DDRSoC requires generated IP artifacts: ' + ', '.join(sorted(missing)))
        self.ddr_ck_hz = config.ip['ddr3'].parameters['Memory_Clock'] * 1000000
        ae350_map = self.address_map = AddressMap(config)
        ae350_map.require()
        board = config.board
        resources = board.resources
        io_map = self.io = bind_ports(config)
        platform = create_platform(io_map, 'clk50', dict(use_cpu_as_gpio=1,
            use_mspi_as_gpio=1, use_sspi_as_gpio=1, timing_driven=1,
            **config.rtl.implementation_options()))
        root = Path(__file__).resolve().parents[3]
        for name in ('ddr3', 'ddr_pll'):
            artifact = config.artifacts[name]
            directory = root / artifact['directory']
            for filename in sorted(artifact['files']):
                source = directory / filename
                if source.suffix.lower() in ('.v', '.sv', '.vhd', '.vhdl'):
                    platform.add_source(str(source))
                    platform.add_verilog_include_path(str(source.parent))
            platform.add_verilog_include_path(str(directory))
            platform.add_verilog_include_path(str((directory / artifact['primary']).parent))
        video_dir = Path(__file__).resolve().parents[2] / 'devices' / 'video' / 'hdmi'
        for name in ('ae350_hdmi_phy.v', 'svo_tmds.v'):
            platform.add_source(str(video_dir / name))
        platform.add_verilog_include_path(str(video_dir))
        # SUG1220: the 80/100 MHz fabric paths close by tens of picoseconds.
        options = dict(config.rtl.implementation_options())
        if implementation_options is not None and implementation_options != options:
            raise ValueError('Implementation options must come from machine.json')
        platform.toolchain.options.update(use_cpu_as_gpio=1,use_mspi_as_gpio=1,use_sspi_as_gpio=1,
                                          timing_driven=1,**options)
        platform.default_clk_name = 'clk50'
        platform.default_clk_period = 1e9 / board.input_clock_hz
        self.platform = platform

        pads = {
            'addr':    [platform.request('ddr3_addr', i) for i in range(15)],
            'ba':      [platform.request('ddr3_ba', i) for i in range(3)],
            'dm':      [platform.request('ddr3_dm', i) for i in range(4)],
            'dq':      [platform.request('ddr3_dq', i) for i in range(32)],
            'dqs':     [platform.request(f'ddr3_dqs{i}') for i in range(4)],
            'dqs_n':   [platform.request(f'ddr3_dqs{i}_n') for i in range(4)],
            'ck':      platform.request('ddr3_ck'),
            'ck_n':    platform.request('ddr3_ck_n'),
        }
        for name in DDR3_CTRL:
            pads[name] = platform.request(f'ddr3_{name}')
        # Stable primitive port names; LiteX emits the physical constraints.
        pads['ck'].name_override = 'ddr3_ck'
        pads['ck_n'].name_override = 'ddr3_ck_n'
        for index in range(4):
            pads['dqs'][index].name_override = 'ddr3_dqs%d' % index
            pads['dqs_n'][index].name_override = 'ddr3_dqs%d_n' % index

        self.crg = ClockReset(platform, fabric_hz=ae350_map.FABRIC_HZ, data_hz=ae350_map.DATA_HZ,
            input_hz=board.input_clock_hz, cpu_hz=config.clocks.cpu,
            pixel_hz=config.clocks.video, reset_active_low=resources['core_reset'].signals['reset'].active_low)
        self.cpu = NativeAE350(wrapper=True, data_domain='mem', vendor_flash=True, module=config.artifacts['cpu']['module'])
        self.cpu.connect_boot_flash(platform.request('boot_flash'))
        self.ddr = GowinDDR3(platform, pads, controller_module=config.artifacts['ddr3']['module'],
                             pll_module=config.artifacts['ddr_pll']['module'],
                             pll_initialization=any(Path(name).name.lower() == 'pll_init.v'
                                 for name in config.artifacts['ddr_pll']['files']))

        # DDR UI clock domain and its asynchronous reset.
        self.cd_ddr = ClockDomain('ddr')
        self.comb += self.cd_ddr.clk.eq(self.ddr.clk_out)
        self.specials += AsyncResetSynchronizer(self.cd_ddr, ~self.ddr.pll_lock | ResetSignal('sys'))
        calib_sys = Signal()
        self.lock_status = PLLStatusSynchronizer(self.ddr.pll_lock)
        lock_sys = self.lock_status.o
        self.specials += MultiReg(self.ddr.init_calib_complete, calib_sys, 'sys')
        calib_mem = Signal()
        self.specials += MultiReg(self.ddr.init_calib_complete, calib_mem, 'mem')

        # Calibration watchdog and CPU release in the always-running sys domain.
        self.calib_timeout = calib_timeout = Signal()
        self.calib_cycles = calib_cycles = Signal(32)
        cpu_hold = Signal(reset=1)
        wait = Signal(28)
        cycles_latched = Signal()
        self.comb += self.cpu.reset.eq(cpu_hold)
        self.sync += [
            If(~calib_sys,
                If(wait != ae350_map.FABRIC_HZ // 4,
                    wait.eq(wait + 1),
                ).Else(
                    calib_timeout.eq(1),
                ),
            ).Else(
                If(~cycles_latched,
                    calib_cycles.eq(wait),
                    cycles_latched.eq(1),
                ),
            ),
            If(calib_sys | calib_timeout,
                cpu_hold.eq(0),
            ),
        ]

        # Liveness of the DDR UI clock, sampled from the ddr domain.
        alive = Signal(16)
        self.sync.ddr += alive.eq(alive + 1)
        alive_sys = Signal()
        self.specials += MultiReg(alive[10], alive_sys, 'sys')

        self.comb += [self.cpu.ahb_rom.sel.eq(1), self.cpu.ahb_ram.sel.eq(1)]

        ddr_bus = AHBInterface(data_width=64, address_width=32)
        err_bus = AHBInterface(data_width=64, address_width=32)

        # The hard macro resolves ILM/DLM internally. This port owns only DDR.
        cpu_ui = DDR3UI()
        self.ddr_port = ClockDomainsRenamer({'sys': 'mem'})(DDR3AHB(ddr_bus, cpu_ui,
                                ready_i=calib_mem,
                                accepted_i=ddr_bus.trans[1],
                                end_burst_i=self.cpu.ahb_ram.readyout & (self.cpu.ahb_ram.trans == 0),
                                lookahead=self.cpu.ahb_ram,
                                base=ae350_map.DDR_BASE, size=ae350_map.DDR_SIZE))
        self.err = ClockDomainsRenamer({'sys': 'mem'})(ErrorSlave(err_bus))
        # One interconnect, one address-acceptance rule for every slave: the
        # address, direction and selected slave are captured together at the
        # acceptance edge, and the response mux uses that captured slave.
        self.ic = ClockDomainsRenamer({'sys': 'mem'})(MemInterconnect(self.cpu.ahb_ram, [
            (ae350_map.DDR_BASE, ae350_map.DDR_SIZE, ddr_bus),
            (0, 1 << 32, err_bus),
        ], registered=True))
        self.submodules += self.ddr_port, self.err, self.ic

        # Status is on the platform APB extension, not in boot memory.
        self.status = status = Signal(32)
        error_sys = Signal()
        self.specials += MultiReg(self.ddr_port.error_sticky, error_sys, 'sys')
        self.comb += status.eq(Cat(
            lock_sys,                     # [0]
            calib_sys,                    # [1]
            calib_timeout,                 # [2]
            alive_sys,                     # [3]
            error_sys,                    # [4]
            cpu_hold,                      # [5]
            Constant(0, 2),                # [7:6]
            Constant(0x02, 8),             # [15:8] status format version
            Constant(0, 16),               # [31:16]
        ))
        # Transfer one coherent, held diagnostic snapshot through a request /
        # acknowledge handshake. MultiReg on each live counter bit is unsafe.
        # Only the exported words cross; the full recorder stays in DDR.
        recorder = [self.ddr_port.rec_w, self.ddr_port.rec_w_mask,
                    self.ddr_port.rec_w_data[:32], self.ddr_port.rec_r,
                    self.ddr_port.rec_r_sel[:32], self.ddr_port.rec_r_blk[:32],
                    self.ddr_port.rec_r_blk[32:64],
                    Cat(self.ddr_port.rec_w_n, self.ddr_port.rec_r_n)]
        self.recorder_cdc = BusSynchronizer(256, 'ddr', 'sys')
        self.comb += self.recorder_cdc.i.eq(Cat(*recorder))
        snapshot = [self.recorder_cdc.o[i*32:(i+1)*32] for i in range(8)]
        # 112 bits are the existing diagnostic words. The next two 32-bit
        # words are mem-domain counters at 0x44 and 0x48, outside the image ABI.
        self.data_cdc = BusSynchronizer(176, 'mem', 'sys')
        self.comb += self.data_cdc.i.eq(Cat(self.ddr_port.debug, self.ddr_port.debug_hi,
                                          self.ddr_port.last_control,
                                          self.ddr_port.write_control, self.ddr_port.read_control,
                                          self.ddr_port.n_rline_hit, self.ddr_port.n_wait_cycles))
        # Platform CSR and the extended-AHB loopback. DDR diagnostics stay at
        # their P2 offsets; writes to that page still return an error.
        registers = {0: status, 4: calib_cycles, 8: self.data_cdc.o[:32],
            12: self.data_cdc.o[32:64], 16: self.data_cdc.o[64:80],
            20: snapshot[7][:16], 24: snapshot[7][16:32],
            28: self.data_cdc.o[80:96], 64: self.data_cdc.o[96:112],
            0x44: self.data_cdc.o[112:144], 0x48: self.data_cdc.o[144:176],
            **{32 + 4*i: word for i, word in enumerate(snapshot)}}
        # Not self.platform: that attribute is the Gowin board platform.
        video = FabricVideo()
        self.sd_native = ClockDomainsRenamer('mem')(FabricSD(platform.request('sdcard'),platform.device,ae350_map.DATA_HZ,ae350_map.DDR_SIZE))
        self.sd = SDClockCrossing(self.sd_native)
        self.ext = AE350Fabric(self.cpu, registers, io_map, ae350_map, video=video, sd=self.sd,
                               flash=self.cpu.flash_apb)
        self.video_arbiter = arbiter = ClockDomainsRenamer('ddr')(
            VideoMemoryArbiter(self.ddr.ui, cpu_ui, video.ui))
        self.comb += [arbiter.cpu_pending.eq(self.ddr_port.ui_pending_ddr),
                      arbiter.video_pending.eq(video.reader.pending),
                      video.cpu_commands.eq(arbiter.cpu_commands),
                      video.video_commands.eq(arbiter.video_commands),
                      video.max_wait.eq(arbiter.max_video_wait),
                      video.memory_fault.eq(arbiter.unexpected_response),
                      video.reset.eq(ResetSignal('sys') | ~self.ddr.init_calib_complete |
                                     ~self.crg.hdmi_pll.locked)]
        self.specials += Instance('ae350_hdmi_phy',
            i_resetn=~ResetSignal('sys'), i_locked=self.crg.hdmi_pll.locked,
            i_clk_pixel=ClockSignal('hdmi_pix'), i_clk_5x_pixel=ClockSignal('hdmi_5x'),
            i_clk_reset=ClockSignal('hdmi_reset'), i_rgb=video.pixels.rgb,
            i_de=video.pixels.de, i_hsync=video.pixels.hsync, i_vsync=video.pixels.vsync,
            o_tmds_clk_p=platform.request('hdmi_clk_p'), o_tmds_clk_n=platform.request('hdmi_clk_n'),
            o_tmds_d_p=Cat(*[platform.request('hdmi_data_p', i) for i in range(3)]),
            o_tmds_d_n=Cat(*[platform.request('hdmi_data_n', i) for i in range(3)]))
        tx_pad = platform.request('audio')
        rx_pad = platform.request('microphone')
        for name in ('bck', 'ws', 'din', 'pa_en'):
            self.comb += getattr(tx_pad, name).eq(getattr(self.ext.audio.tx_pads, name))
        for name in ('bck', 'lr', 'ws'):
            self.comb += getattr(rx_pad, name).eq(getattr(self.ext.audio.rx_pads, name))
        self.comb += self.ext.audio.rx_pads.data.eq(rx_pad.data)
        self.comb += self.cpu.gpio_in.eq(self.ext.board.gpio_pin)
        for led in io_map.leds():
            pad = platform.request('user_led', led['index'])
            driven = 0 if led['active_low'] else 1
            self.specials += Tristate(pad, driven, self.ext.board.leds[led['index']])
        for button in io_map.buttons():
            pad = platform.request('user_button', button['index'])
            level = ~pad if button['active_low'] else pad
            self.comb += self.ext.board.button_async[button['index']].eq(level)
        self.comb += platform.request('ws2812').eq(self.ext.board.ws_pad)

        serial = platform.request('serial')
        self.cpu.cpu_params.update(i_UART2_RXD=serial.rx, o_UART2_TXD=serial.tx,
                                   i_RTC_CLK=self.crg.rtc)
        # The configured AE350 subsystem hardens UART2/PIT/GPIO/RTC internally
        # (see gateware/vendor/gowin_gw5ast/ae350_soc/riscv_ae350_config.v), so there is no
        # fabric UART implementation or port-level peripheral clock enable.
        # Only the sources the configured subsystem actually needs: the
        # embedded SPI-flash/DDR3 paths stay out (their DDR3 IP would collide
        # with the standalone 400 MHz IP instance and they are not enabled).
        soc_sources = (config.artifacts['cpu']['primary'], 'riscv_ae350_soc.v', 'riscv_ae350_flash.v')
        soc_sources_dir = Path(soc_sources_dir) if soc_sources_dir is not None else root / config.artifacts['cpu']['directory']
        for name in soc_sources:
            platform.add_source(str(soc_sources_dir / name))
        platform.add_verilog_include_path(str(soc_sources_dir))
        self.cpu.connect_jtag(platform.request('cpu_jtag'))
        self.cpu.connect_management(platform.request('user_flash'),platform.request('system_i2c'))
        # Mux selection always has a defined output (reset channel 0).
        self.comb += platform.request('twi_mux_sel').eq(self.cpu.gpio_out[ae350_map.TWI_MUX_GPIO_SHIFT:ae350_map.TWI_MUX_GPIO_SHIFT+3])
        platform.add_period_constraint(platform.lookup_request('cpu_jtag').tck, 10000)
        self.specials += Tristate(platform.request('fan_pwm'), 0, self.ext.sense.pull)
        self.comb += self.ext.sense.tach.eq(platform.request('fan_tach'))

        # Extra timing constraints the LiteX backend cannot express:
        # the DDR user clock and memory clock are unrelated to the fabric clocks.
        toolchain = platform.toolchain
        _orig_sdc = toolchain.build_timing_constraints

        def build_timing_constraints(vns):
            name, ext = _orig_sdc(vns)
            extra = [
                # PLL LOCK is asynchronous even when its initialization FSM
                # uses clk50. Only the metastability catcher is exempt; the
                # second stage and every status consumer remain timed.
                'set_false_path -to [get_pins {%s}]' % self.lock_status.first_stage_pin,
                *self.crg.pll.extra_constraints,
                *self.crg.hdmi_clock_constraints,
                # SDK example/Flash defines the slave/input/pad clocks
                # separately. Bound them by the fastest source (AHB/APB sys)
                # rather than reusing its example's 50-MHz period. Only these
                # SDK mode clocks and sys are mutually exclusive; the rest of
                # our bus/DDR timing groups stay unchanged.
                'create_clock -name sdk_flash_slave_clk -period %g [get_nets {%s/u_RiscV_AE350_SOC/u_riscv_ae350_flash_top/u_gwspiflash/u_spi_spiif/spi_w_clk_slv_20}]'
                    % (1e9 / ae350_map.FABRIC_HZ, self.cpu.module),
                'create_clock -name sdk_flash_pad_clk -period %g [get_ports {boot_flash_clk}]'
                    % (1e9 / ae350_map.FABRIC_HZ),
                'set_clock_groups -exclusive -group [get_clocks {sdk_flash_slave_clk}] '
                '-group [get_clocks {sdk_flash_pad_clk}] -group [get_clocks {sys_clk}]',
                'create_clock -name ddr_ui_clk -period %.4f -waveform {0 %.4f} [get_pins {ddr3/gw3_top/u_ddr_phy_top/fclkdiv/CLKOUT}]'
                    % (4000.0 / (self.ddr_ck_hz / 1e6), 2000.0 / (self.ddr_ck_hz / 1e6)),
                'create_clock -name ddr_memory_clk -period %.4f -waveform {0 %.4f} [get_nets {ddr3_memory_clk}]'
                    % (1000.0 / (self.ddr_ck_hz / 1e6), 500.0 / (self.ddr_ck_hz / 1e6)),
                'create_clock -name ddr_clk_ref -period 20 -waveform {0 10} [get_nets {ddr3_clk_ref}]',
                'set_clock_groups -asynchronous '
                '-group [get_clocks {sys_clk}] -group [get_clocks {cpu_clk}] '
                '-group [get_clocks {mem_clk}] '
                '-group [get_clocks {hdmi_pix_clk hdmi_5x_clk hdmi_reset_clk}] '
                '-group [get_clocks {ddr_ui_clk}] -group [get_clocks {ddr_memory_clk}] '
                '-group [get_clocks {ddr_clk_ref}]',
            ]
            with open(name, 'a') as f:
                f.write('\n' + '\n'.join(extra) + '\n')
            return name, ext

        toolchain.build_timing_constraints = build_timing_constraints
