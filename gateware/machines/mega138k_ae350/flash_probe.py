"""Timing-only SDK Flash probe; no DDR or fabric peripherals, never a board image."""
from pathlib import Path
from migen import *
from litex.gen import LiteXModule
from gateware.io import IOBindings
from gateware.gowin_platform import create_platform
from gateware.machines.mega138k_ae350.clock import FabricPLL
from gateware.machines.mega138k_ae350.cpu import NativeAE350
from gateware.machines.mega138k_ae350.address_map import AddressMap


class FlashTimingProbe(LiteXModule):
    def __init__(self, machine, soc_sources_dir, implementation_options=None):
        self.address_map = AddressMap(machine)
        io = IOBindings(machine)
        io.pad('clk50', 'clock', 'clk')
        io.pad('reset_key', 'core_reset', 'reset')
        io.resource('serial', 'debug_uart')
        io.resource('cpu_jtag', machine.devices.cpu_jtag)
        io.resource('boot_flash', machine.devices.boot_flash)
        options = machine.rtl.implementation_options()
        if implementation_options is not None and implementation_options != options:
            raise ValueError('Implementation options must come from machine.json')
        self.platform = p = create_platform(io, 'clk50', dict(
            use_cpu_as_gpio=1, use_mspi_as_gpio=1, use_sspi_as_gpio=1,
            timing_driven=1, **options))
        clk = p.request('clk50')
        p.add_period_constraint(clk, 1e9 / machine.board.input_clock_hz)
        self.cd_input = ClockDomain('input', reset_less=True)
        self.cd_sys = ClockDomain('sys')
        self.cd_cpu = ClockDomain('cpu')
        self.comb += self.cd_input.clk.eq(clk)
        reset_key = p.request('reset_key')
        active = machine.board.resources['core_reset'].signals['reset'].active_low
        reset = ~reset_key if active else reset_key
        count = Signal(16, reset=65535)
        self.sync.input += If(reset, count.eq(65535)).Elif(count != 0, count.eq(count - 1))
        self.pll = FabricPLL(p, clk, [(self.cd_sys, machine.clocks.sys),
                                     (self.cd_cpu, machine.clocks.cpu)], machine.board.input_clock_hz)
        self.comb += self.pll.reset.eq(reset | (count != 0))
        p.toolchain.additional_cst_commands.append('INS_LOC "ae350_pll" PLL_R[0];')
        self.pll.clocks[0].attr.add(('syn_keep', 1))
        p.add_platform_command('CLOCK_LOC "{clock}" BUFG = CLK;', clock=self.pll.clocks[0])
        self.cpu = c = NativeAE350(wrapper=True, data_domain='sys', vendor_flash=True,
                                  module=machine.artifacts['cpu']['module'])
        c.connect_boot_flash(p.request('boot_flash'))
        c.connect_jtag(p.request('cpu_jtag'))
        p.add_period_constraint(p.lookup_request('cpu_jtag').tck, 10000)
        serial = p.request('serial')
        c.cpu_params.update(i_UART2_RXD=serial.rx, o_UART2_TXD=serial.tx)
        for name in ('CSN', 'CLK', 'MOSI', 'MISO', 'WPN', 'HOLDN'):
            c.cpu_params['i_SPI_' + name + '_IN'] = 1
        c.cpu_params.update(i_I2C_SCL_IN=1, i_I2C_SDA_IN=1)
        self.comb += [c.reset.eq(0), c.gpio_in.eq(0), c.interrupt.eq(0), c.dma_req.eq(0)]
        # Native extended APB -> native FR, same clock, as in the SDK example.
        # There is no SYSINFO/bootloader environment in this timing-only image.
        fr = c.flash_apb
        self.comb += [fr.addr.eq(0xf8000000 | c.apb_addr[:12]),
            fr.wdata.eq(c.apb_wdata), fr.write.eq(c.apb_write),
            fr.select.eq(c.apb_select), fr.enable.eq(c.apb_enable),
            c.apb_data.eq(fr.rdata), c.apb_ready.eq(fr.ready), c.apb_slverr.eq(0)]
        # No external data memories/FIFOs. Dummy termination keeps hard ports
        # defined; it deliberately provides no executable payload environment.
        for bus in (c.ahb_ram, c.ahb_exts):
            self.comb += [bus.rdata.eq(0), bus.readyout.eq(1), bus.resp.eq(0)]
        for name in (machine.artifacts['cpu']['primary'], 'riscv_ae350_soc.v', 'riscv_ae350_flash.v'):
            p.add_source(str(Path(soc_sources_dir) / name))
        p.add_verilog_include_path(str(soc_sources_dir))
        original = p.toolchain.build_timing_constraints
        def constraints(vns):
            name, ext = original(vns)
            prefix = c.module + '/u_RiscV_AE350_SOC/u_riscv_ae350_flash_top/u_gwspiflash/u_spi_spiif'
            period = 1e9 / machine.clocks.sys
            lines = [
                'create_clock -name sdk_flash_slave_clk -period %g [get_nets {%s/spi_w_clk_slv_20}]' % (period, prefix),
                'create_clock -name sdk_flash_pad_clk -period %g [get_ports {boot_flash_clk}]' % period,
                'set_clock_groups -exclusive -group [get_clocks {sdk_flash_slave_clk}] '
                '-group [get_clocks {sdk_flash_pad_clk}] -group [get_clocks {sys_clk}]',
                'set_clock_groups -asynchronous -group [get_clocks {sys_clk}] -group [get_clocks {cpu_clk}]',
            ]
            with open(name, 'a') as f:
                f.write('\n' + '\n'.join(lines) + '\n')
            return name, ext
        p.toolchain.build_timing_constraints = constraints
