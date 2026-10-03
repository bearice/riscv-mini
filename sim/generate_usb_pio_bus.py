"""Generate the real PIO bridge + upstream Host for an Icarus bus test."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module, Instance, ClockSignal, ResetSignal, ResetInserter, Signal
from migen.fhdl import verilog
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.axi import AXILiteInterface
from gateware.usb_ultra import USBPIOBridge

def generate(path):
    m=Module();w=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    a=AXILiteInterface(data_width=32,address_width=32)
    core_reset=Signal(name_override='core_reset')
    m.submodules.bridge=ResetInserter()(USBPIOBridge(w,a));m.comb+=m.bridge.reset.eq(core_reset)
    ports=dict(p_USB_CLK_FREQ=60000000,i_clk_i=ClockSignal(),i_rst_i=ResetSignal()|core_reset,
        i_utmi_data_in_i=0,i_utmi_txready_i=1,i_utmi_rxvalid_i=0,i_utmi_rxactive_i=0,
        i_utmi_rxerror_i=0,i_utmi_linestate_i=1)
    for channel,names in [('aw',('valid','ready','addr')),('w',('valid','ready','data','strb')),
                          ('b',('valid','ready','resp')),('ar',('valid','ready','addr')),
                          ('r',('valid','ready','data','resp'))]:
        for name in names:
            inp=(name!='ready') if channel in ('aw','w','ar') else name=='ready'
            ports[('i_' if inp else 'o_')+'cfg_'+channel+name+('_i' if inp else '_o')]=getattr(getattr(a,channel),name)
    m.specials+=Instance('usbh_host',**ports)
    ios={core_reset}
    for name in ('adr','dat_w','dat_r','we','sel','cyc','stb','ack','err'):
        signal=getattr(w,name);signal.name_override='wb_'+name;ios.add(signal)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(str(verilog.convert(m,ios=ios,name='usb_pio_bus')))

if __name__=='__main__':generate(Path(sys.argv[1]))
