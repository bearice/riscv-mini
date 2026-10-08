"""RMII pads omit F10: the shared PHY reset GPIO is its sole owner."""
from gateware.memory_map import ETHMAC_BASE
from migen import ClockDomain, ClockDomainsRenamer, ResetInserter
from migen.fhdl.structure import _Assign
from migen.genlib.cdc import GrayCounter, MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen import LiteXModule
from litex.build.generic_platform import Subsignal, Pins, IOStandard
from litex.soc.interconnect.csr import CSRStatus
from liteeth.phy.rmii import LiteEthPHYRMIICRG, LiteEthPHYRMIITX, LiteEthPHYRMIIRX
from liteeth.phy.common import LiteEthPHYMDIO

class DockRMII(LiteXModule):
    """Compose LiteEth PHY components; TX can send before any received frame.

    HAL advertises only 100M full duplex; both paths use that fixed speed.
    """
    dw=8
    tx_clk_freq=rx_clk_freq=50e6
    def __init__(self,clocks,pads):
        self.crg=LiteEthPHYRMIICRG(clocks,pads,None,with_hw_init_reset=False)
        self.tx=ClockDomainsRenamer('eth_tx')(LiteEthPHYRMIITX(pads,self.crg.clk_signal))
        # Capture RX pins in the middle of their 20ns symbol window. Internal
        # RX logic stays on rising edges, with a timed half-cycle input path.
        self.rx=ClockDomainsRenamer('eth_rx')(LiteEthPHYRMIIRX(pads,~self.crg.clk_signal))
        # The pinned RX defaults to 10M until its first preamble. That first
        # packet is lost at 100M. Replace its one speed-selector assignment;
        # retain the vendor receive datapath and verify the seam explicitly.
        selectors=[i for i,s in enumerate(self.rx._fragment.comb) if isinstance(s,_Assign) and s.l is self.rx.speed]
        if len(selectors)!=1:raise ValueError('Pinned RMII RX speed selector changed')
        self.rx._fragment.comb[selectors[0]]=self.rx.speed.eq(1)
        self.comb += self.tx.speed.eq(1)
        self.sink,self.source=self.tx.sink,self.rx.source
        self.mdio=LiteEthPHYMDIO(pads)
        for special in self.mdio._fragment.specials:
            if isinstance(special,MultiReg):special.i.name_override='eth_mdio_in'

class EthernetClock(LiteXModule):
    def __init__(self, clock, reset):
        self.cd_eth_ref=ClockDomain('eth_ref')
        self.comb += self.cd_eth_ref.clk.eq(clock)
        self.specials += AsyncResetSynchronizer(self.cd_eth_ref,reset)
        self.counter=ClockDomainsRenamer('eth_ref')(GrayCounter(32))
        self.counter.q.name_override='eth_ref_gray'
        self.comb += self.counter.ce.eq(1)
        self._gray=CSRStatus(32,name='gray')
        self.specials += MultiReg(self.counter.q,self._gray.status)

def add_ethernet(soc):
    platform=soc.platform
    platform.add_extension([('rmii',0,
        Subsignal('rx_data',Pins('F15 C9')),Subsignal('crs_dv',Pins('M6')),
        Subsignal('rx_er',Pins('L8')),Subsignal('tx_data',Pins('D16 E14')),
        Subsignal('tx_en',Pins('E16')),Subsignal('mdc',Pins('F14')),
        Subsignal('mdio',Pins('F16')),IOStandard('LVCMOS33'))])
    clocks=platform.request('eth_clocks')
    soc.ethphy=DockRMII(clocks,platform.request('rmii'))
    soc.eth_clock=EthernetClock(clocks.ref_clk,soc.crg.cd_sys.rst)
    # 8 selects the byte datapath in the PHY domain with 32-bit CPU packet SRAM.
    soc.mem_map['ethmac']=ETHMAC_BASE
    soc.add_ethernet(phy=soc.ethphy,data_width=8,nrxslots=2,ntxslots=2,
        rxslots_read_only=True,txslots_write_only=True,with_timing_constraints=False)
    platform.add_period_constraint(clocks.ref_clk,20)
    # One local reset clears both packet-slot ownership in sys and CDC/MAC
    # state in eth_rx/eth_tx, without driving the shared Ethernet/USB F10 pin.
    ResetInserter(['sys'])(soc.ethmac)
    soc.comb += soc.ethmac.reset_sys.eq(soc.ethphy.crg.reset)
