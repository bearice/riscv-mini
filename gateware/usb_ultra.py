"""PIO full-speed Host/serial PHY at sys60; ULPI is only used for setup."""
from pathlib import Path
from migen import *
from migen.fhdl.specials import Tristate
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.soc.cores.clock.gowin_gw2a import GW2APLL
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.axi import AXILiteInterface
from litex.soc.interconnect.csr import CSRStorage, CSRStatus
from gateware.usb import USBPHYInit


class USBPIOBridge(LiteXModule):
    """One registered request/reply, with independent AXI AW/W acceptance."""
    def __init__(self,wb,axil):
        self.fsm=fsm=FSM(reset_state='IDLE')
        error=Signal();abandoned=Signal()
        fsm.act('IDLE',NextValue(abandoned,0),If(wb.cyc & wb.stb,
            If(wb.we,
                NextValue(axil.aw.addr,wb.adr<<2),NextValue(axil.aw.valid,1),
                NextValue(axil.w.data,wb.dat_w),NextValue(axil.w.strb,wb.sel),NextValue(axil.w.valid,1),
                NextState('WRITE')).Else(
                NextValue(axil.ar.addr,wb.adr<<2),NextValue(axil.ar.valid,1),NextState('READ'))))
        fsm.act('WRITE',If(~wb.cyc,NextValue(abandoned,1)),
            If(axil.aw.ready,NextValue(axil.aw.valid,0)),
            If(axil.w.ready,NextValue(axil.w.valid,0)),
            If(~axil.aw.valid & ~axil.w.valid,NextState('WRITE_REPLY')))
        fsm.act('WRITE_REPLY',axil.b.ready.eq(1),If(~wb.cyc,NextValue(abandoned,1)),
            If(axil.b.valid,NextValue(error,axil.b.resp!=0),NextValue(wb.dat_r,0),NextState('REPLY')))
        fsm.act('READ',If(~wb.cyc,NextValue(abandoned,1)),
            If(axil.ar.ready,NextValue(axil.ar.valid,0),NextState('READ_REPLY')))
        fsm.act('READ_REPLY',axil.r.ready.eq(1),If(~wb.cyc,NextValue(abandoned,1)),
            If(axil.r.valid,NextValue(error,axil.r.resp!=0),NextValue(wb.dat_r,axil.r.data),NextState('REPLY')))
        fsm.act('REPLY',wb.ack.eq(~error & ~abandoned),wb.err.eq(error & ~abandoned),NextState('IDLE'))


class USBHostUltra(LiteXModule):
    def __init__(self,soc,pads,compact_serial_outputs=False):
        self._enable=CSRStorage(name='enable')
        self._reset=CSRStorage(reset=1,name='reset')
        self._ready=CSRStatus(name='ready');self._error=CSRStatus(name='error')
        self._id=CSRStatus(32,name='id');self._lines=CSRStatus(3,name='lines')
        self.cd_ulpi=ClockDomain('ulpi')
        self.ulpi_pll=pll=GW2APLL(devicename=soc.platform.devicename,device=soc.platform.device)
        self.comb += pll.reset.eq(~soc.phy_reset.out.storage)
        pll.register_clkin(pads.clk,60e6)
        pll.create_clkout(self.cd_ulpi,60e6,phase=247.5,margin=0,with_reset=False)
        self.specials += AsyncResetSynchronizer(self.cd_ulpi,
            soc.crg.cd_sys.rst|~soc.phy_reset.out.storage|~self._enable.storage|~pll.locked)
        init_pads=Record([('dir',1),('nxt',1),('stp',1)])
        self.phy=phy=USBPHYInit(init_pads)
        raw=Signal(8);data=Signal(8);direction=Signal();nxt=Signal()
        for source,target,width in [(raw,data,8),(pads.dir,direction,1),(pads.nxt,nxt,1)]:
            for bit in range(width):
                self.specials += Instance('DFFN',i_CLK=ClockSignal('ulpi'),i_D=source[bit],o_Q=target[bit])
        self.comb += [phy.di.eq(data),init_pads.dir.eq(direction),init_pads.nxt.eq(nxt)]
        core_reset=Signal(name_override='usb_ultra_core_reset');core_reset.attr.add('keep')
        self.comb += core_reset.eq(soc.crg.cd_sys.rst|self._reset.storage|~self._ready.status)
        utmi=Record([('data_in',8),('data_out',8),('txvalid',1),('txready',1),
            ('rxvalid',1),('rxactive',1),('rxerror',1),('linestate',2),('op_mode',2),
            ('xcvrselect',2),('termselect',1),('dppulldown',1),('dmpulldown',1)])
        dp=Signal();dn=Signal();oen=Signal()
        self.specials += Instance('usb_fs_phy',p_USB_CLK_FREQ=60000000,
            i_clk_i=ClockSignal('sys'),i_rst_i=core_reset,
            i_usb_rx_dp_i=raw[4],i_usb_rx_dn_i=raw[5],i_usb_rx_rcv_i=raw[6],
            i_usb_reset_assert_i=0,o_usb_tx_dp_o=dp,o_usb_tx_dn_o=dn,o_usb_tx_oen_o=oen,
            **{('i_utmi_'+n+'_i'):getattr(utmi,n) for n in
               ('data_out','txvalid','op_mode','xcvrselect','termselect','dppulldown','dmpulldown')},
            **{('o_utmi_'+n+'_o'):getattr(utmi,n) for n in
               ('data_in','txready','rxvalid','rxactive','rxerror','linestate')})
        oe=Signal(8)
        serial_data=Cat(~oen,dp,~dp&~dn)
        if compact_serial_outputs:
            # SERIAL has oe=0b00000111: D[7:3] are PHY inputs. Their
            # inactive output data does not need a ready-controlled mux.
            # Preserve immediate raw DIR gating during ULPI initialization.
            # The init FSM already releases D[7:3] in SERIAL. Avoid a
            # redundant ready mux on these enables; DIR still acts directly.
            self.comb += oe.eq(Cat(
                Mux(phy.ready,7,phy.oe[:3] & Replicate(~pads.dir,3)),
                phy.oe[3:] & Replicate(~pads.dir,5)))
            drive=Cat(Mux(phy.ready,serial_data,phy.do[:3]),phy.init_output[3:])
        else:
            self.comb += oe.eq(Mux(phy.ready,7,phy.oe & Replicate(~pads.dir,8)))
            drive=Mux(phy.ready,serial_data,phy.do)
        self.comb += pads.stp.eq(init_pads.stp)
        self.specials += Tristate(pads.data,drive,oe,raw)
        self.wb_ctrl=wishbone.Interface(data_width=32,address_width=32,addressing='word')
        axil=AXILiteInterface(data_width=32,address_width=32)
        self.bridge=ResetInserter()(USBPIOBridge(self.wb_ctrl,axil))
        self.comb += self.bridge.reset.eq(core_reset)
        self.interrupt=Signal()
        self.ev=type('IRQ',(),{})();self.ev.irq=self.interrupt
        ports=dict(p_USB_CLK_FREQ=60000000,i_clk_i=ClockSignal('sys'),i_rst_i=core_reset,o_intr_o=self.interrupt)
        for channel,names in [('aw',('valid','ready','addr')),('w',('valid','ready','data','strb')),
                              ('b',('valid','ready','resp')),('ar',('valid','ready','addr')),
                              ('r',('valid','ready','data','resp'))]:
            for name in names:
                is_input=(name!='ready') if channel in ('aw','w','ar') else (name=='ready')
                ports[('i_' if is_input else 'o_')+'cfg_'+channel+name+('_i' if is_input else '_o')]=getattr(getattr(axil,channel),name)
        for name in ('data_in','txready','rxvalid','rxactive','rxerror','linestate'):
            ports['i_utmi_'+name+'_i']=getattr(utmi,name)
        for name in ('data_out','txvalid','op_mode','xcvrselect','termselect','dppulldown','dmpulldown'):
            ports['o_utmi_'+name+'_o']=getattr(utmi,name)
        self.specials += Instance('usbh_host',**ports)
        lines=Signal(3,name_override='usb_ultra_lines')
        self.comb += lines.eq(utmi.linestate)
        for a,b in [(phy.ready,self._ready.status),(phy.error,self._error.status),
                    (phy.phy_id,self._id.status)]:self.specials += MultiReg(a,b)
        self.comb += self._lines.status.eq(lines)
        vendor=Path(__file__).parent/'vendor'
        soc.platform.add_verilog_include_path(str(vendor/'usb_host/src_v'))
        for source in sorted((vendor/'usb_host/src_v').glob('*.v')):
            if source.name!='usbh_host_defs.v':soc.platform.add_source(str(source))
        soc.platform.add_source(str(vendor/'usb_fs_phy/usb_fs_phy.v'))
