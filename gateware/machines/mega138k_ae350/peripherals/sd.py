"""Native LiteSDCard PHY/Core, hard-DMAC AHB FIFOs and APB control.

Software disables requests and aborts/drains its DMAC channels before reset.
No SD CSR is a second data FIFO alias.
"""
from types import SimpleNamespace
from migen import *
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule
from litex.soc.interconnect import stream, wishbone
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus
from litesdcard.phy import SDPHY
from gateware.vendor.litesdcard.sdcore import SDCore
from litex.soc.interconnect.ahb import AHBInterface
from gateware.machines.mega138k_ae350.peripherals.audio import AudioData
from gateware.machines.mega138k_ae350 import address_map as amap


def bind_csr(owner, reg, offset, go, off, wdata):
    """Own compound-CSR field logic and drive its simple bus interface.

    Attaching only storage/status signals loses CSRField aggregation logic.
    Use the same finalize/module ownership contract as LiteX GenericBank.
    """
    if isinstance(reg,(CSRStorage,CSRStatus)):
        reg.finalize(32,'big')
        owner.submodules += reg
        simple=reg.get_simple_csrs()
    else:
        simple=[reg]
    for index,csr in enumerate(simple):
        owner.comb += [csr.wr_data.eq(wdata),
            csr.wr_stb.eq(go & (off==offset+index*4)),csr.rd_stb.eq(0)]


class SDTransport(LiteXModule):
    def __init__(self, pads, device, clock_hz, ram_bytes):
        cd = Signal(reset=1)
        self.specials += MultiReg(pads.cd, cd)
        self.phy = SDPHY(SimpleNamespace(clk=pads.clk, cmd=pads.cmd, data=pads.data, cd=cd),
                         device, clock_hz, cmd_timeout=.25, data_timeout=.5)
        self.read_requests = ResetInserter()(stream.SyncFIFO([('block_length',10)],depth=2,buffered=True))
        self.comb += self.read_requests.source.connect(self.phy.datar.sink)
        phy = self.phy
        self.command_pipe = stream.SyncFIFO(phy.cmdw.sink.description,depth=2,buffered=True)
        self.comb += self.command_pipe.source.connect(phy.cmdw.sink)
        self.core = SDCore(SimpleNamespace(cmdw=SimpleNamespace(sink=self.command_pipe.sink),cmdr=phy.cmdr,dataw=phy.dataw,
            datar=SimpleNamespace(sink=self.read_requests.sink,source=phy.datar.source)))
        self.comb += self.read_requests.reset.eq(self.core.fsm.ongoing('IDLE'))


class WordFIFO(LiteXModule):
    """Small register FIFO: no BSRAM clock-pulse requirement at 100MHz."""
    def __init__(self,depth=16):
        if depth<2 or depth&(depth-1):raise ValueError('FIFO depth must be a power of two')
        self.sink=stream.Endpoint([('data',32)])
        self.source=stream.Endpoint([('data',32)])
        self.level=Signal(max=depth+1)
        head,tail=Signal(max=depth),Signal(max=depth)
        words=Array(Signal(34) for _ in range(depth))
        self.comb += [self.sink.ready.eq(self.level<depth),self.source.valid.eq(self.level!=0),
            self.source.data.eq(words[head][:32]),self.source.first.eq(words[head][32]),self.source.last.eq(words[head][33])]
        push=self.sink.valid & self.sink.ready
        pop=self.source.valid & self.source.ready
        self.sync += [If(push,words[tail].eq(Cat(self.sink.data,self.sink.first,self.sink.last)),tail.eq(tail+1)),
                      If(pop,head.eq(head+1)),
                      If(push & ~pop,self.level.eq(self.level+1)).Elif(pop & ~push,self.level.eq(self.level-1))]


class SDDataPath(LiteXModule):
    """Little-endian AHB words, MSB-first card bytes; FIFO backpressure."""
    def __init__(self):
        self.sink = stream.Endpoint([('data',8)])
        self.source = stream.Endpoint([('data',8)])
        self.rx_fifo = WordFIFO()
        self.tx_fifo = WordFIFO()
        self.rx_convert = stream.Converter(8,32,reverse=True)
        self.tx_convert = stream.Converter(32,8,reverse=True)
        self.comb += [self.sink.connect(self.rx_convert.sink),
                      self.rx_convert.source.connect(self.rx_fifo.sink),
                      self.tx_fifo.source.connect(self.tx_convert.sink),
                      self.tx_convert.source.connect(self.source)]


class FabricSD(LiteXModule):
    def __init__(self, pads, device, clock_hz, ram_bytes):
        self.off, self.wdata, self.rdata = Signal(12), Signal(32), Signal(32)
        self.selected, self.write, self.commit, self.ok = (Signal() for _ in range(4))
        self.transport = t = ResetInserter()(SDTransport(pads, device, clock_hz, ram_bytes))
        self.path = path = ResetInserter()(SDDataPath())
        self.comb += [t.core.source.connect(path.sink),path.source.connect(t.core.sink)]
        self.tx_bus = AHBInterface(data_width=32,address_width=32)
        self.rx_bus = AHBInterface(data_width=32,address_width=32)
        self.tx = AudioData(self.tx_bus,amap.SD_TX_DATA,True)
        self.rx = AudioData(self.rx_bus,amap.SD_RX_DATA,False)
        self.tx_ack,self.rx_ack,self.tx_req,self.rx_req=(Signal() for _ in range(4))
        reset = Signal()
        enabled = Signal()
        tx_hold,rx_hold=Signal(),Signal()
        self.sync += [tx_hold.eq(self.tx_ack),rx_hold.eq(self.rx_ack)]
        busy = ~t.core.fsm.ongoing('IDLE')
        self.comb += [t.reset.eq(reset),path.reset.eq(reset),
            self.tx.valid.eq(path.tx_fifo.sink.ready & ~reset),self.rx.valid.eq(path.rx_fifo.source.valid & ~reset),
            path.tx_fifo.sink.valid.eq(self.tx.transfer),path.tx_fifo.sink.data.eq(self._swap(self.tx.data)),
            self.tx.data.eq(self.tx_bus.wdata),path.rx_fifo.source.ready.eq(self.rx.transfer),
            self.rx.data.eq(self._swap(path.rx_fifo.source.data)),
            self.tx_req.eq(enabled & self.tx.valid & ~self.tx_ack & ~tx_hold),
            self.rx_req.eq(enabled & self.rx.valid & ~self.rx_ack & ~rx_hold)]
        regs = {
            0x40:t.phy.card_detect, 0x44:t.phy.clocker.divider, 0x48:t.phy.settings,
            0x4c:t.phy.init.initialize, 0x50:t.phy.cmdr.timeout, 0x54:t.phy.datar.timeout,
            0x80:t.core.cmd_argument, 0x84:t.core.cmd_command, 0x88:t.core.cmd_send,
            0x9c:t.core.cmd_event, 0xa0:t.core.data_event,
            0xa4:t.core.block_length, 0xa8:t.core.block_count,
        }
        t.core.cmd_response.finalize(32,'big')
        self.submodules += t.core.cmd_response
        self.registers = regs
        admitted = Signal()
        self.sync += admitted.eq(self.selected & self.ok & self.write)
        go = self.commit & admitted
        legal = {0:~self.write,4:~self.write,8:~self.write,0x0c:(~self.write | (self.wdata<=3)),
                 0x10:~self.write,0x20:~self.write,0x24:~self.write,0x28:~self.write,0x2c:~self.write}
        reads = {0:amap.SD_ID,4:amap.ABI_VERSION,8:amap.SD_CAP,0xc:Cat(reset,enabled),0x10:busy,0x20:clock_hz,0x24:path.tx_fifo.level,0x28:path.rx_fifo.level,0x2c:amap.SD_TX_DMA_SEL | (amap.SD_RX_DMA_SEL << 8)}
        for offset,reg in regs.items():
            writable = isinstance(reg,(CSR,CSRStorage))
            # Configuration is immutable during a transfer; disabling remains
            # possible so a timeout can drain before reset.
            allowed = ~busy if offset in (0x44,0x48,0x4c,0x50,0x54) else 1
            legal[offset] = ~self.write | (allowed if writable else 0)
            bind_csr(self,reg,offset,go,self.off,self.wdata)
            if isinstance(reg,CSRStorage):reads[offset]=reg.storage
            elif isinstance(reg,CSR):reads[offset]=reg.rd_data
            else:reads[offset]=reg.status
        for index in range(4):
            offset=0x8c+4*index
            legal[offset]=~self.write
            reads[offset]=t.core.cmd_response.status[32*(3-index):32*(4-index)]
        self.comb += [self.ok.eq(0),self.rdata.eq(0),
            Case(self.off,{offset:self.ok.eq(value) for offset,value in legal.items()}),
            Case(self.off,{offset:self.rdata.eq(value) for offset,value in reads.items()})]
        self.sync += If(go & (self.off==0x0c),reset.eq(self.wdata[0]),enabled.eq(self.wdata[1]))

    @staticmethod
    def _swap(value):
        return Cat(value[24:32],value[16:24],value[8:16],value[:8])
