"""Emit the production Migen modules for primitive-model RTL simulations."""
from migen import ClockDomain, Module, Signal
from migen.fhdl import verilog
from gateware.devices.video.hdmi.tmds import TMDSEncoder
from gateware.devices.video.hdmi.phy import GowinHDMIPHY


def encoder_verilog():
    m=Module()
    m.clock_domains.cd_sys=ClockDomain('sys',reset_less=True)
    m.cd_sys.clk.name_override='clk'
    m.submodules.encoder=e=TMDSEncoder()
    ports={m.cd_sys.clk}
    for name in ('resetn','din','de','ctrl','dout'):
        signal=getattr(e,name);signal.name_override=name;ports.add(signal)
    return str(verilog.convert(m,ios=ports,name='tmds_encoder'))


def phy_verilog():
    m=Module()
    names=('clk_pixel','clk_5x_pixel','clk_reset','tmds_clk_p','tmds_clk_n','tmds_d_p','tmds_d_n')
    signals={n:Signal(3 if n.startswith('tmds_d_') else 1,name_override=n) for n in names}
    m.submodules.phy=p=GowinHDMIPHY(*[signals[n] for n in names])
    ports=set(signals.values())
    for name in ('resetn','locked','rgb','de','hsync','vsync','pixel_resetn_q','serializer_resetn'):
        signal=getattr(p,name);signal.name_override=name;ports.add(signal)
    for i,symbol in enumerate(p.symbols):
        symbol.name_override='symbol%d'%i;ports.add(symbol)
    return str(verilog.convert(m,ios=ports,name='hdmi_phy'))
