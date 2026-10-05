"""USB3317 initialization and selectable serial full-speed Host backends."""
from pathlib import Path
from migen import *
from migen.fhdl.specials import Tristate
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex import get_data_mod
from litex.gen import LiteXModule
from litex.soc.cores.clock.gowin_gw2a import GW2APLL
from litex.build.generic_platform import Pins, Subsignal, IOStandard
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import CSRStorage, CSRStatus
from litex.soc.integration.soc import SoCRegion
from gateware.bus import WishbonePipeline

class USBPHYInit(LiteXModule):
    def __init__(self,pads):
        self.di=di=Signal(8);self.do=do=Signal(8);self.oe=oe=Signal(8)
        output=Signal(8);stp=Signal(reset=1)
        self.init_output=output
        self.comb += pads.stp.eq(stp)
        self.ready=ready=Signal(name_override='usb_ulpi_ready');self.error=error=Signal(name_override='usb_ulpi_error')
        self.phy_id=phy_id=Signal(32,name_override='usb_ulpi_id');self.lines=lines=Signal(3,name_override='usb_serial_lines')
        self.comb += lines.eq(Cat(di[4],di[5],di[6]))
        step=Signal(3);address=Signal(6);value=Signal(8);read=Signal();wait=Signal(11,reset=32)
        watchdog=Signal(23)
        self.sync.ulpi += If(~ready & ~error,watchdog.eq(watchdog+1))
        self.comb += [address.eq(step),read.eq(step<4),value.eq(0)]
        self.comb += Case(step,{4:[address.eq(4),value.eq(0x45)],
            5:[address.eq(0x0a),value.eq(0x26)],6:[address.eq(7),value.eq(9)]})
        self.fsm=fsm=ClockDomainsRenamer('ulpi')(FSM(reset_state='EXIT'))
        self.dp_o=dp_o=Signal();self.dm_o=dm_o=Signal();self.dp_oe=dp_oe=Signal();self.dm_oe=dm_oe=Signal()
        self.comb += do.eq(Mux(ready,Cat(dp_oe|dm_oe,dp_o,~dp_o&~dm_o),output))
        # STP exits any previous serial session without a physical unplug/reset.
        fsm.act('EXIT',If(wait!=0,NextValue(wait,wait-1))
            .Elif(~pads.dir,NextValue(stp,0),NextValue(wait,16),NextState('BOOT')))
        fsm.act('BOOT',oe.eq(255),NextValue(wait,wait-1),
            If(wait==0,NextState('IDLE')))
        fsm.act('IDLE',oe.eq(255),
            If(watchdog>=6000000,NextValue(error,1),NextState('ERROR'))
            .Elif(~pads.dir,NextValue(output,Mux(read,0xc0,0x80)|address),NextState('COMMAND')))
        fsm.act('COMMAND',oe.eq(255),
            If(pads.dir,NextValue(output,0),NextState('IDLE')).Elif(pads.nxt,
                If(read,NextValue(output,0),NextState('READ')).Else(NextValue(output,value),NextState('WRITE'))))
        fsm.act('READ',If(pads.dir,NextState('READ_DATA')),
            If(watchdog>=6000000,NextValue(error,1),NextState('ERROR')))
        # NXT is LOW for register data; skip DIR's bus turnaround cycle.
        fsm.act('READ_DATA',NextValue(phy_id,(phy_id>>8)|(di<<24)),NextState('READ_END'))
        fsm.act('READ_END',If(~pads.dir,NextValue(step,step+1),NextState('IDLE')))
        fsm.act('WRITE',oe.eq(255),
            If(pads.dir,NextValue(output,0),NextState('IDLE'))
            .Elif(pads.nxt,NextValue(output,0),NextValue(stp,1),NextState('STOP')))
        # Every register write, including serial-mode entry, needs STP.
        fsm.act('STOP',oe.eq(255),NextValue(stp,0),
            If(step==6,NextValue(wait,1024),NextState('SETTLE'))
            .Else(NextValue(step,step+1),NextState('IDLE')))
        fsm.act('SETTLE',NextValue(wait,wait-1),If(wait==0,NextValue(ready,1),NextState('SERIAL')))
        fsm.act('SERIAL',oe.eq(7))
        fsm.act('ERROR')

class USBHost(LiteXModule):
    def __init__(self, soc, pads):
        self._enable=CSRStorage(name='enable')
        self._reset=CSRStorage(reset=1,name='reset')
        self._ready=CSRStatus(name='ready')
        self._error=CSRStatus(name='error')
        self._id=CSRStatus(32,name='id')
        self._lines=CSRStatus(3,name='lines')
        self.cd_ulpi=ClockDomain('ulpi')
        # T15's incoming clock route is too slow for the ULPI output setup
        # budget. Regenerate the same synchronous 60MHz clock on a PLL route.
        self.ulpi_pll=pll=GW2APLL(devicename=soc.platform.devicename,device=soc.platform.device)
        # The init FSM has synchronous state resets. Disabling its PLL can
        # stop the clock before ready/id clear, leaving stale status in sys.
        # Keep the clock running for USB-only stop; F10 still resets the PLL.
        self.comb += pll.reset.eq(~soc.phy_reset.out.storage)
        pll.register_clkin(pads.clk,60e6)
        # 247.5 degrees adds one 22.5-degree tap (~1.04ns at 60MHz) to the
        # previous 225-degree phase. Keep output-enable hold margin when
        # placement changes, while retaining the external setup budget.
        pll.create_clkout(self.cd_ulpi,60e6,phase=247.5,margin=0,with_reset=False)
        self.specials += AsyncResetSynchronizer(self.cd_ulpi,
            soc.crg.cd_sys.rst|~soc.phy_reset.out.storage|~self._enable.storage|~pll.locked)
        init_pads=Record([('dir',1),('nxt',1),('stp',1)])
        self.phy=phy=USBPHYInit(init_pads)
        raw_data=Signal(8);physical_oe=Signal(8)
        self.comb += [pads.stp.eq(init_pads.stp),
            physical_oe.eq(Mux(phy.ready,7,phy.oe & Replicate(~pads.dir,8)))]
        self.specials += Tristate(pads.data,phy.do,physical_oe,raw_data)
        # Falling-edge sampling avoids the delayed FPGA clock's ULPI hold path.
        # Native negative-edge FFs share the PHY's global clock route. A fabric
        # inverter creates a second, skewed clock and breaks the half-cycle path.
        for source,target,width in [(raw_data,phy.di,8),(pads.dir,init_pads.dir,1),(pads.nxt,init_pads.nxt,1)]:
            for bit in range(width):
                self.specials += Instance('DFFN',i_CLK=ClockSignal('ulpi'),i_D=source[bit],o_Q=target[bit])
        serial_dp=Signal();serial_dm=Signal()
        raw_dp=Signal(name_override='usb_serial_dp_raw');raw_dm=Signal(name_override='usb_serial_dm_raw')
        self.comb += [raw_dp.eq(raw_data[4]),raw_dm.eq(raw_data[5])]
        self.specials += [MultiReg(raw_dp,serial_dp,'usb'),MultiReg(raw_dm,serial_dm,'usb')]
        for a,b in [(phy.ready,self._ready.status),(phy.error,self._error.status),(phy.phy_id,self._id.status),(phy.lines,self._lines.status)]:
            self.specials += MultiReg(a,b)
        control_reset=Signal(name_override='usb_control_reset')
        self.comb += control_reset.eq(soc.crg.cd_sys.rst|self._reset.storage|~self._ready.status)
        self.specials += AsyncResetSynchronizer(soc.crg.cd_usb,control_reset|~soc.crg.usb_pll.locked)
        self.wb_ctrl=ctrl=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        self.wb_dma=dma=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        self.interrupt=Signal();self.ev=type('IRQ',(),{})();self.ev.irq=self.interrupt
        ports=dict(i_phy_clk=ClockSignal('usb'),i_phy_reset=ResetSignal('usb'),
            i_ctrl_clk=ClockSignal('sys'),i_ctrl_reset=control_reset,o_io_interrupt=self.interrupt,
            i_io_usb_0_dp_read=serial_dp,i_io_usb_0_dm_read=serial_dm,
            o_io_usb_0_dp_write=phy.dp_o,o_io_usb_0_dm_write=phy.dm_o,
            o_io_usb_0_dp_writeEnable=phy.dp_oe,o_io_usb_0_dm_writeEnable=phy.dm_oe)
        for name,sig in [('CYC',ctrl.cyc),('STB',ctrl.stb),('WE',ctrl.we),('ADR',ctrl.adr),('DAT_MOSI',ctrl.dat_w),('SEL',ctrl.sel)]:
            ports['i_io_ctrl_'+name]=sig
        ports.update(o_io_ctrl_ACK=ctrl.ack,o_io_ctrl_DAT_MISO=ctrl.dat_r)
        for name,sig in [('CYC',dma.cyc),('STB',dma.stb),('WE',dma.we),('ADR',dma.adr),('DAT_MOSI',dma.dat_w),('SEL',dma.sel),('CTI',dma.cti),('BTE',dma.bte)]:
            ports['o_io_dma_'+name]=sig
        ports.update(i_io_dma_ACK=dma.ack,i_io_dma_DAT_MISO=dma.dat_r,i_io_dma_ERR=dma.err)
        netlist='UsbOhciWishbone_Dw32_Pc1_Pf48000000'
        self.specials += Instance(netlist,**ports)
        source=Path(get_data_mod('misc','usb_ohci').data_location)/(netlist+'.v')
        target=Path(__file__).resolve().parents[1]/'build/vendor'/('gowin_'+netlist+'.v')
        target.parent.mkdir(parents=True,exist_ok=True)
        # Preserve Spinal's CDC stages as flip-flops on Gowin as well.
        rtl=source.read_text().replace('(* async_reg = "true" *)',
            '(* async_reg = "true", syn_preserve = 1, syn_keep = 1 *)')
        target.write_text(rtl)
        soc.platform.add_source(str(target))

def add_usb(soc,backend='ultra',compact_serial_outputs=False):
    soc.platform.add_extension([('usb_ulpi',0,Subsignal('data',Pins('G11 H12 J12 H13 T14 R13 P13 R12')),
        Subsignal('clk',Pins('T15')),Subsignal('stp',Pins('K11')),Subsignal('dir',Pins('K12')),
        Subsignal('nxt',Pins('K13')),IOStandard('LVCMOS33'))])
    pads=soc.platform.request('usb_ulpi')
    if backend=='ultra':
        from gateware.usb_ultra import USBHostUltra
        soc.usb_host=USBHostUltra(soc,pads,compact_serial_outputs=compact_serial_outputs)
        soc.bus.add_slave(name='usb_pio',slave=soc.usb_host.wb_ctrl,
            region=SoCRegion(origin=0xb1000000,size=4096,cached=False))
        soc.add_constant('CONFIG_USB_ULTRA',1)
        soc.irq.add('usb_host',use_loc_if_exists=True)
        soc.platform.add_period_constraint(pads.clk,1e9/60e6)
        return
    soc.usb_host=USBHost(soc,pads)
    ohci_bus=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    soc.usb_pipeline=WishbonePipeline(ohci_bus,soc.usb_host.wb_ctrl)
    soc.bus.add_slave(name='usb_ohci',slave=ohci_bus,
        region=SoCRegion(origin=0xb1000000,size=4096,cached=False))
    soc.bus.add_master(name='usb_dma',master=soc.usb_host.wb_dma)
    soc.irq.add('usb_host',use_loc_if_exists=True)
    soc.platform.add_period_constraint(pads.clk,1e9/60e6)
