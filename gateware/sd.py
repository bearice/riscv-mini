"""Native SD controller with bounded software-abort reset; one owner for SD pads."""
from types import SimpleNamespace
from migen import ResetInserter,Signal
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr_eventmanager import EventManager,EventSourcePulse
from litesdcard.phy import SDPHY
from litesdcard.frontend.dma import SDBlock2MemDMA,SDMem2BlockDMA
from gateware.vendor.sdcore import SDCore

@ResetInserter()
class NativeSD(LiteXModule):
    def __init__(self,soc):
        pads=soc.platform.request('sdcard')
        card_detect=Signal()
        self.specials += MultiReg(pads.cd,card_detect)
        synchronized_pads=SimpleNamespace(clk=pads.clk,cmd=pads.cmd,data=pads.data,cd=card_detect)
        self.phy=SDPHY(synchronized_pads,soc.platform.device,soc.sys_clk_freq,cmd_timeout=.25,data_timeout=.5)
        self.core=SDCore(self.phy)
        reader=wishbone.Interface(data_width=32,address_width=32,addressing='word',mode='r')
        writer=wishbone.Interface(data_width=32,address_width=32,addressing='word',mode='w')
        self.block2mem=SDBlock2MemDMA(writer,soc.cpu.endianness)
        self.mem2block=SDMem2BlockDMA(reader,soc.cpu.endianness)
        self.comb += [self.core.source.connect(self.block2mem.sink),self.mem2block.source.connect(self.core.sink)]
        soc.bus.add_master(name='sdcard_block2mem',master=writer)
        soc.bus.add_master(name='sdcard_mem2block',master=reader)
        self.ev=EventManager()
        self.ev.block2mem=EventSourcePulse();self.ev.mem2block=EventSourcePulse()
        self.ev.card_detect=EventSourcePulse();self.ev.command=EventSourcePulse()
        self.ev.finalize()
        self.comb += [self.ev.block2mem.trigger.eq(self.block2mem.irq),self.ev.mem2block.trigger.eq(self.mem2block.irq),
            self.ev.card_detect.trigger.eq(self.phy.card_detect_irq),self.ev.command.trigger.eq(self.core.irq)]
