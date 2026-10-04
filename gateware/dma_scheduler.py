"""CPU/video/DMA share the existing native port instead of a wider crossbar."""
from migen import Signal, Array, Mux, If, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStorage
from litedram.common import LiteDRAMNativePort

class DMAMemoryScheduler(LiteXModule):
    def __init__(self,native,enabled=1):
        self._spacing=CSRStorage(4,reset=0,name='spacing')
        self.cpu=cpu=LiteDRAMNativePort('both',native.address_width,128)
        self.video=video=LiteDRAMNativePort('read',native.address_width,128)
        self.dma=dma=LiteDRAMNativePort('both',native.address_width,128)
        ports=[cpu,video,dma]
        owner=Signal(2);prefer_dma=Signal();choose=Signal(2)
        data=Signal(128);quiet=Signal(4)
        self.comb += choose.eq(Mux(video.cmd.valid,1,
            Mux(dma.cmd.valid & (~cpu.cmd.valid | prefer_dma),2,0)))
        self.fsm=fsm=FSM(reset_state='SELECT')
        pause=[If(self._spacing.storage==0,NextState('SELECT')).Else(
            NextValue(quiet,self._spacing.storage-1),NextState('QUIET'))]
        fsm.act('SELECT',If(enabled & (cpu.cmd.valid|video.cmd.valid|dma.cmd.valid),
            NextValue(owner,choose),NextState('COMMAND')))
        fsm.act('COMMAND',
            native.cmd.valid.eq(enabled & Array([p.cmd.valid for p in ports])[owner]),
            native.cmd.addr.eq(Array([p.cmd.addr for p in ports])[owner]),
            native.cmd.we.eq(Array([p.cmd.we for p in ports])[owner]),
            native.cmd.last.eq(Array([p.cmd.last for p in ports])[owner]),
            *[p.cmd.ready.eq(enabled & native.cmd.ready & (owner==i)) for i,p in enumerate(ports)],
            If(~Array([p.cmd.valid for p in ports])[owner],NextState('SELECT'))
            .Elif(native.cmd.valid & native.cmd.ready,
                If(owner!=1,NextValue(prefer_dma,owner==0)),
                If(native.cmd.we,NextState('WRITE')).Else(NextState('READ_WAIT'))))
        fsm.act('WRITE',native.wdata.valid.eq(Mux(owner==2,dma.wdata.valid,cpu.wdata.valid)),
            native.wdata.data.eq(Mux(owner==2,dma.wdata.data,cpu.wdata.data)),
            native.wdata.we.eq(Mux(owner==2,dma.wdata.we,cpu.wdata.we)),
            cpu.wdata.ready.eq(native.wdata.ready & (owner==0)),
            dma.wdata.ready.eq(native.wdata.ready & (owner==2)),
            If(native.wdata.valid & native.wdata.ready,*pause))
        fsm.act('READ_WAIT',native.rdata.ready.eq(1),
            If(native.rdata.valid,NextValue(data,native.rdata.data),NextState('READ_RETURN')))
        self.comb += [p.rdata.data.eq(data) for p in ports]
        fsm.act('READ_RETURN',*[p.rdata.valid.eq(owner==i) for i,p in enumerate(ports)],
            If(Array([p.rdata.ready for p in ports])[owner],*pause))
        fsm.act('QUIET',If(quiet==0,NextState('SELECT')).Else(NextValue(quiet,quiet-1)))
