"""Native SD controller with bounded software-abort reset; one owner for SD pads."""
from types import SimpleNamespace
from migen import ResetInserter,Signal
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule
from litex.soc.interconnect import wishbone
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr_eventmanager import EventManager,EventSourcePulse
from litesdcard.phy import SDPHY
from gateware.vendor.sddma import SDBlock2MemDMA,SDMem2BlockDMA
from gateware.vendor.sdcore import SDCore

@ResetInserter()
class NativeSD(LiteXModule):
    def __init__(self,soc,profile='lite',native_ports=None):
        if profile not in ('lite','full'):raise ValueError('Native SD profile must be lite/full')
        pads=soc.platform.request('sdcard')
        card_detect=Signal()
        self.specials += MultiReg(pads.cd,card_detect)
        synchronized_pads=SimpleNamespace(clk=pads.clk,cmd=pads.cmd,data=pads.data,cd=card_detect)
        self.phy=SDPHY(synchronized_pads,soc.platform.device,soc.sys_clk_freq,cmd_timeout=.25,data_timeout=.5)
        # Keep each read descriptor stable for the complete PHY block, and
        # isolate the PHY's block-end/timeout logic from the 32-bit core counter.
        self.read_requests=ResetInserter()(stream.SyncFIFO([('block_length',10)],depth=2,buffered=True))
        self.comb += self.read_requests.source.connect(self.phy.datar.sink)
        core_phy=SimpleNamespace(cmdw=self.phy.cmdw,cmdr=self.phy.cmdr,dataw=self.phy.dataw,
            datar=SimpleNamespace(sink=self.read_requests.sink,source=self.phy.datar.source))
        self.core=SDCore(core_phy,bounded=profile=='lite')
        self.comb += self.read_requests.reset.eq(self.core.fsm.ongoing('IDLE'))
        reader=native_ports[0] if native_ports else wishbone.Interface(data_width=32,address_width=32,addressing='word',mode='r')
        writer=native_ports[1] if native_ports else wishbone.Interface(data_width=32,address_width=32,addressing='word',mode='w')
        self.block2mem=SDBlock2MemDMA(writer,soc.cpu.endianness,bounded=profile=='lite')
        self.mem2block=SDMem2BlockDMA(reader,soc.cpu.endianness,bounded=profile=='lite')
        self.comb += [self.core.source.connect(self.block2mem.sink),self.mem2block.source.connect(self.core.sink)]
        if not native_ports:
            soc.bus.add_master(name='sdcard_block2mem',master=writer)
            soc.bus.add_master(name='sdcard_mem2block',master=reader)
        self.ev=EventManager()
        self.ev.block2mem=EventSourcePulse();self.ev.mem2block=EventSourcePulse()
        self.ev.card_detect=EventSourcePulse();self.ev.command=EventSourcePulse()
        self.ev.finalize()
        self.comb += [self.ev.block2mem.trigger.eq(self.block2mem.irq),self.ev.mem2block.trigger.eq(self.mem2block.irq),
            self.ev.card_detect.trigger.eq(self.phy.card_detect_irq),self.ev.command.trigger.eq(self.core.irq)]
