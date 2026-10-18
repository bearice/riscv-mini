# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause
# AE350 primitive port defaults adapted from LiteX gowin_ae350/core.py.
"""Native hard-macro ports, without a fabric bus adapter."""
from migen import Signal, Constant, ClockSignal, ResetSignal, Instance, Memory, FSM, If, NextValue, NextState, Case
from litex.gen import LiteXModule, Open
from litex.soc.interconnect.ahb import AHBInterface

APB_CE_APB = 1
APB_CE_UART2 = 4
APB_CE_GPIO = 16
APB_CE_PIT = 32


class AHBMemory(LiteXModule):
    """Bounded synchronous memory with one wait state and native byte lanes.

    Accept NONSEQ and SEQ address phases only while HREADY is high. The
    following data phase stalls once for the synchronous RAM read/write, then
    completes and can accept the next address. Invalid accesses return the
    required two-cycle ERROR response without touching memory.
    """
    def __init__(self, bus, size, base=0, init=None, read_only=False):
        lanes = bus.data_width // 8
        shift = (lanes - 1).bit_length()
        memory = Memory(bus.data_width, size // lanes, init=init)
        rd = memory.get_port(write_capable=not read_only, we_granularity=8 if not read_only else 0)
        self.specials += memory, rd
        address = Signal(max=size // lanes)
        mask = Signal(lanes)
        writing = Signal()
        error = Signal()
        self.comb += [rd.adr.eq(address), bus.rdata.eq(rd.dat_r)]
        lane_mask = Signal(lanes)
        aligned = Signal()
        self.comb += Case(bus.size, {
            n: [lane_mask.eq(((1 << (1 << n)) - 1) << bus.addr[:shift]),
                aligned.eq((bus.addr & ((1 << n) - 1)) == 0)]
            for n in range(shift + 1)
        })
        valid = (bus.addr >= base) & (bus.addr < base + size) & aligned & (bus.size <= shift)
        if read_only:
            valid = valid & ~bus.write
        def accept():
            return If(bus.trans[1], NextValue(address, bus.addr[shift:shift + len(address)]),
                      NextValue(mask, lane_mask), NextValue(writing, bus.write), NextValue(error, ~valid),
                      NextState('MEMORY')).Else(NextState('ADDRESS'))
        self.fsm = fsm = FSM(reset_state='ADDRESS')
        fsm.act('ADDRESS', bus.readyout.eq(1), accept())
        fsm.act('MEMORY', bus.resp.eq(error), NextState('COMPLETE'))
        fsm.act('COMPLETE', bus.readyout.eq(1), bus.resp.eq(error), accept())
        if not read_only:
            wr = rd
            self.comb += [wr.adr.eq(address), wr.dat_w.eq(bus.wdata),
                          wr.we.eq(0)]
            self.comb += If(fsm.ongoing('MEMORY') & writing & ~error, wr.we.eq(mask))


class AE350ROM(LiteXModule):
    """AE350's HSIZE-less ROM port returns the containing 32-bit word.

    HADDR retains the byte offset for load-byte/halfword accesses; byte lane
    selection is inside the hard macro. Do not apply generic word-alignment
    checks to this port. RAM continues to use the full AHB transfer size.
    """
    def __init__(self, bus, size, base=0x80000000, init=None):
        if bus.data_width != 32 or base % 4 or size % 4:
            raise ValueError('AE350 ROM requires 32-bit words and word-aligned bounds')
        word = AHBInterface(data_width=32, address_width=32)
        self.memory = AHBMemory(word, size, base=base, init=init, read_only=True)
        self.comb += [word.addr.eq(bus.addr & 0xfffffffc), word.size.eq(2),
                      word.trans.eq(bus.trans), word.write.eq(bus.write),
                      bus.rdata.eq(word.rdata), bus.readyout.eq(word.readyout),
                      bus.resp.eq(word.resp)]


class NativeAE350(LiteXModule):
    def __init__(self):
        self.reset = Signal()
        self.ahb_rom = ahb_rom = AHBInterface(data_width=32, address_width=32)
        self.ahb_ram = ahb_ram = AHBInterface(data_width=64, address_width=32)
        ahb_exts = AHBInterface(data_width=32, address_width=32)
        self.interrupt = Signal(16)
        self.cpu_params = dict(
            # Clk/Rst.
            i_CORE_CLK       = ClockSignal("cpu"),
            i_DDR_CLK        = ClockSignal("sys"),
            i_AHB_CLK        = ClockSignal("sys"),
            i_APB_CLK        = ClockSignal("sys"),
            i_POR_N          = 1,
            i_HW_RSTN        = ~(ResetSignal("sys") | self.reset),
            o_PRESETN        = Open(),
            o_HRESETN        = Open(),
            o_DDR_RSTN       = Open(),

            # Features/Peripherals Enable.
            i_CORE_CE        = 1,
            i_AXI_CE         = 1,
            i_DDR_CE         = 1,
            i_AHB_CE         = 1,
            i_APB_CE         = Constant(APB_CE_APB, 8),
            i_APB2AHB_CE     = 1,

            # WFI.
            o_CORE0_WFI_MODE = Open(),
            i_WAKEUP_IN      = 0,

            # RTC.
            i_RTC_CLK        = ClockSignal("sys"),
            o_RTC_WAKEUP     = Open(),

            # Interrupts.
            i_GP_INT         = self.interrupt,

            # DMA.
            i_DMA_REQ        = Constant(0, 8),
            o_DMA_ACK        = Open(8),

            # AHB-Lite ROM Interface (CPU Master).
            i_ROM_HRDATA     = ahb_rom.rdata,
            i_ROM_HREADY     = ahb_rom.readyout,
            i_ROM_HRESP      = ahb_rom.resp,
            o_ROM_HADDR      = ahb_rom.addr,
            o_ROM_HTRANS     = ahb_rom.trans,
            o_ROM_HWRITE     = ahb_rom.write,

            # APB Fabric Interface (CPU Master, Unused).
            o_APB_PADDR      = Open(32),
            o_APB_PENABLE    = Open(),
            i_APB_PRDATA     = Constant(0, 32),
            i_APB_PREADY     = 0,
            o_APB_PSEL       = Open(),
            o_APB_PWDATA     = Open(32),
            o_APB_PWRITE     = Open(),
            i_APB_PSLVERR    = 0,
            o_APB_PPROT      = Open(3),
            o_APB_PSTRB      = Open(4),

            # AHB-Lite Peripheral Interface (CPU Master).
            i_EXTS_HRDATA    = ahb_exts.rdata,
            i_EXTS_HREADYIN  = ahb_exts.readyout,
            i_EXTS_HRESP     = ahb_exts.resp,
            o_EXTS_HADDR     = ahb_exts.addr,
            o_EXTS_HBURST    = ahb_exts.burst,
            o_EXTS_HPROT     = ahb_exts.prot,
            o_EXTS_HSEL      = ahb_exts.sel,
            o_EXTS_HSIZE     = ahb_exts.size,
            o_EXTS_HTRANS    = ahb_exts.trans,
            o_EXTS_HWDATA    = ahb_exts.wdata,
            o_EXTS_HWRITE    = ahb_exts.write,

            # AHB-Lite Local Memory Interface (CPU Slave, Unused).
            i_EXTM_HADDR     = Constant(0, 32),
            i_EXTM_HBURST    = Constant(0, 3),
            i_EXTM_HPROT     = Constant(0, 4),
            o_EXTM_HRDATA    = Open(64),
            i_EXTM_HREADY    = 0,
            o_EXTM_HREADYOUT = Open(),
            o_EXTM_HRESP     = Open(),
            i_EXTM_HSEL      = 0,
            i_EXTM_HSIZE     = Constant(0, 3),
            i_EXTM_HTRANS    = Constant(0, 2),
            i_EXTM_HWDATA    = Constant(0, 64),
            i_EXTM_HWRITE    = 0,

            # AHB-Lite RAM Interface (CPU Master).
            i_DDR_HRDATA     = ahb_ram.rdata,
            i_DDR_HREADY     = ahb_ram.readyout,
            i_DDR_HRESP      = ahb_ram.resp,
            o_DDR_HADDR      = ahb_ram.addr,
            o_DDR_HBURST     = ahb_ram.burst,
            o_DDR_HPROT      = ahb_ram.prot,
            o_DDR_HSIZE      = ahb_ram.size,
            o_DDR_HTRANS     = ahb_ram.trans,
            o_DDR_HWDATA     = ahb_ram.wdata,
            o_DDR_HWRITE     = ahb_ram.write,

            # GPIOs.
            i_GPIO_IN        = Constant(0, 32),
            o_GPIO_OUT       = Open(32),
            o_GPIO_OE        = Open(32),

            # SCAN.
            i_SCAN_EN        = 0,
            i_SCAN_TEST      = 0,
            i_SCAN_IN        = Constant(0xfffff, 20),
            o_SCAN_OUT       = Open(20),

            # Memory Self-Test JTAG (held in reset).
            i_INTEG_TCK      = 1,
            i_INTEG_TDI      = 1,
            i_INTEG_TMS      = 1,
            i_INTEG_TRST     = 0,
            o_INTEG_TDO      = Open(),

            # SRAM Power, Retention and Timing Controls.
            i_PGEN_CHAIN_I   = 1,
            o_PRDYN_CHAIN_O  = Open(),
            i_EMA            = Constant(0b011, 3),
            i_EMAW           = Constant(0b01, 2),
            i_EMAS           = 0,
            i_RET1N          = 1,
            i_RET2N          = 1,

            # SPI.
            i_SPI2_HOLDN_IN  = 0,
            i_SPI2_WPN_IN    = 0,
            i_SPI2_CLK_IN    = 0,
            i_SPI2_CSN_IN    = 0,
            i_SPI2_MISO_IN   = 0,
            i_SPI2_MOSI_IN   = 0,
            o_SPI2_HOLDN_OUT = Open(),
            o_SPI2_HOLDN_OE  = Open(),
            o_SPI2_WPN_OUT   = Open(),
            o_SPI2_WPN_OE    = Open(),
            o_SPI2_CLK_OUT   = Open(),
            o_SPI2_CLK_OE    = Open(),
            o_SPI2_CSN_OUT   = Open(),
            o_SPI2_CSN_OE    = Open(),
            o_SPI2_MISO_OUT  = Open(),
            o_SPI2_MISO_OE   = Open(),
            o_SPI2_MOSI_OUT  = Open(),
            o_SPI2_MOSI_OE   = Open(),

            # I2C.
            i_I2C_SCL_IN     = 0,
            i_I2C_SDA_IN     = 0,
            o_I2C_SCL        = Open(),
            o_I2C_SDA        = Open(),

            # PIT/PWM.
            o_CH0_PWM        = Open(),
            o_CH0_PWMOE      = Open(),
            o_CH1_PWM        = Open(),
            o_CH1_PWMOE      = Open(),
            o_CH2_PWM        = Open(),
            o_CH2_PWMOE      = Open(),
            o_CH3_PWM        = Open(),
            o_CH3_PWMOE      = Open(),

            # UART1.
            o_UART1_TXD      = Open(),
            o_UART1_RTSN     = Open(),
            i_UART1_RXD      = 0,
            i_UART1_CTSN     = 0,
            i_UART1_DSRN     = 0,
            i_UART1_DCDN     = 0,
            i_UART1_RIN      = 0,
            o_UART1_DTRN     = Open(),
            o_UART1_OUT1N    = Open(),
            o_UART1_OUT2N    = Open(),

            # UART2.
            o_UART2_TXD      = Open(),
            o_UART2_RTSN     = Open(),
            i_UART2_RXD      = 0,
            i_UART2_CTSN     = 1,
            i_UART2_DCDN     = 1,
            i_UART2_DSRN     = 1,
            i_UART2_RIN      = 1,
            o_UART2_DTRN     = Open(),
            o_UART2_OUT1N    = Open(),
            o_UART2_OUT2N    = Open(),

            # JTAG.
            i_DBG_TCK        = 1,
            i_TMS_IN         = 1,
            i_TRST_IN        = 1,
            i_TDI_IN         = 0,
            o_TDO_OUT        = Open(),
            o_TDO_OE         = Open(),

            # Test.
            i_TEST_CLK       = 0,
            i_TEST_MODE      = 0,
            i_TEST_RSTN      = 1,
        )

        self.cpu_params.update(i_EXTS_HREADYIN=1, i_EXTS_HRDATA=Constant(0, 32), i_EXTS_HRESP=0,
                               i_APB_PREADY=1, i_APB_PSLVERR=1)

    def connect_jtag(self, pads):
        self.cpu_params.update(i_DBG_TCK=pads.tck, i_TMS_IN=pads.tms, i_TRST_IN=1,
                               i_TDI_IN=pads.tdi, o_TDO_OUT=pads.tdo)

    def do_finalize(self):
        self.specials += Instance('AE350_SOC', **self.cpu_params)
