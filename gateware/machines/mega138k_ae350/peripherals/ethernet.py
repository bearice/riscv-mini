"""RTL8211 management seam; packet MAC/RGMII data path is separate."""
from migen import *
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule

class EthernetManagement(LiteXModule):
    def __init__(self, clock_hz):
        if clock_hz != 50000000:
            raise ValueError('PHY reference divider requires sys50')
        self.off,self.wdata,self.rdata=Signal(12),Signal(32),Signal(32)
        self.selected,self.write,self.commit,self.ok=[Signal() for _ in range(4)]
        self.reset_n,self.mdc,self.mdio_low,self.refclk=[Signal() for _ in range(4)]
        self.mdio_i=Signal()
        mdio=Signal();self.specials += MultiReg(self.mdio_i,mdio)
        control=Signal(3)
        self.sync += self.refclk.eq(~self.refclk)
        self.comb += [self.reset_n.eq(control[0]),self.mdc.eq(control[1]),
            self.mdio_low.eq(control[2]),self.rdata.eq(0),
            self.ok.eq(self.selected & Mux(self.write,
                (self.off==4) & (self.wdata[3:]==0),
                (self.off==0)|(self.off==4)|(self.off==8)|(self.off==12))),
            Case(self.off,{0:self.rdata.eq(0xae350e01),4:self.rdata.eq(control),
                8:self.rdata.eq(mdio),12:self.rdata.eq(clock_hz//2)})]
        self.sync += If(self.commit & self.ok & self.write,control.eq(self.wdata[:3]))
