"""Single-port OHCI controller and retained 32-bit Wishbone DDR DMA adapter."""
from pathlib import Path
from migen import *
from litex import get_data_mod
from litex.gen import LiteXModule
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.axi import AXILiteInterface, AXILite2Wishbone
from gateware.machines.mega138k_ae350.peripherals.ethernet_dma import EthernetDDRPort


class OHCIDDRPort(LiteXModule):
    """Own one beat through DDR completion; preserve byte enables on writes."""
    def __init__(self, bus, ram_bytes=1 << 30):
        self.memory = memory = EthernetDDRPort()
        q, r = memory.requests.sink, memory.responses.source
        address, data, enables = Signal(32), Signal(32), Signal(4)
        write = Signal()
        lane = address[2:5]
        # Explicit widths matter here: a multiply used as a Verilog shift
        # amount is self-sized and can truncate lane*32 or lane*4.
        data_shift = Cat(Constant(0, 5), lane)
        mask_shift = Cat(Constant(0, 2), lane)
        self.comb += [q.addr.eq(address & 0xffffffe0), q.write.eq(write),
                      q.data.eq(data << data_shift),
                      q.mask.eq(~(enables << mask_shift)),
                      bus.dat_r.eq(r.data >> data_shift)]
        self.fsm = f = FSM(reset_state='IDLE')
        f.act('IDLE', If(bus.cyc & bus.stb,
            NextValue(address, bus.adr << 2), NextValue(data, bus.dat_w),
            NextValue(enables, bus.sel), NextValue(write, bus.we),
            If((bus.adr << 2) >= ram_bytes, NextState('ERROR')).Else(NextState('SEND'))))
        f.act('SEND', q.valid.eq(1), If(q.ready, NextState('WAIT')))
        f.act('WAIT', If(r.valid, bus.ack.eq(bus.cyc & bus.stb), r.ready.eq(1), NextState('IDLE')))
        f.act('ERROR', bus.err.eq(bus.cyc & bus.stb), NextState('IDLE'))


class OHCIHost(LiteXModule):
    def __init__(self, platform, ram_bytes=1 << 30):
        self.bus = AXILiteInterface(data_width=32, address_width=32, clock_domain='usb')
        ctrl = wishbone.Interface(data_width=32, address_width=32, addressing='word')
        dma = wishbone.Interface(data_width=32, address_width=32, addressing='word')
        self.control = ClockDomainsRenamer('usb')(AXILite2Wishbone(self.bus, ctrl))
        self.dma = ClockDomainsRenamer({'sys': 'usb'})(OHCIDDRPort(dma, ram_bytes))
        self.rx_dp, self.rx_dn, self.tx_dp, self.tx_dn = [Signal() for _ in range(4)]
        dp_oe, dn_oe = Signal(), Signal()
        self.tx_oe, self.interrupt = Signal(), Signal()
        self.comb += self.tx_oe.eq((dp_oe | dn_oe) & ~ResetSignal('usb'))
        ports = dict(i_phy_clk=ClockSignal('usb'), i_phy_reset=ResetSignal('usb'),
            i_ctrl_clk=ClockSignal('usb'), i_ctrl_reset=ResetSignal('usb'),
            o_io_interrupt=self.interrupt, i_io_usb_0_dp_read=self.rx_dp,
            i_io_usb_0_dm_read=self.rx_dn, o_io_usb_0_dp_write=self.tx_dp,
            o_io_usb_0_dm_write=self.tx_dn, o_io_usb_0_dp_writeEnable=dp_oe,
            o_io_usb_0_dm_writeEnable=dn_oe)
        for name, signal in [('CYC', ctrl.cyc), ('STB', ctrl.stb), ('WE', ctrl.we),
                             ('ADR', ctrl.adr), ('DAT_MOSI', ctrl.dat_w), ('SEL', ctrl.sel)]:
            ports['i_io_ctrl_' + name] = signal
        ports.update(o_io_ctrl_ACK=ctrl.ack, o_io_ctrl_DAT_MISO=ctrl.dat_r)
        for name, signal in [('CYC', dma.cyc), ('STB', dma.stb), ('WE', dma.we),
                             ('ADR', dma.adr), ('DAT_MOSI', dma.dat_w), ('SEL', dma.sel),
                             ('CTI', dma.cti), ('BTE', dma.bte)]:
            ports['o_io_dma_' + name] = signal
        ports.update(i_io_dma_ACK=dma.ack, i_io_dma_DAT_MISO=dma.dat_r, i_io_dma_ERR=dma.err)
        name = 'UsbOhciWishbone_Dw32_Pc1_Pf48000000'
        self.specials += Instance(name, **ports)
        source = Path(get_data_mod('misc', 'usb_ohci').data_location) / (name + '.v')
        target = Path(__file__).resolve().parents[4] / 'build/vendor' / ('gowin_' + name + '.v')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text().replace('(* async_reg = "true" *)',
            '(* async_reg = "true", syn_preserve = 1, syn_keep = 1 *)'))
        platform.add_source(str(target))
