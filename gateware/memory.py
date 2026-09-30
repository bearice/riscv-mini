"""Serialize CPU/video native transactions and hold read replies until consumed.

Video has priority while its bounded DMA FIFO can accept another word. CPU
requests proceed when the FIFO applies backpressure or the frame ends. Eight
sys cycles separate transactions; this board profile trades bandwidth for a
simple, bounded PHY turnaround. Hardware stress is required in addition to
the ownership/backpressure simulation.
"""
from migen import Signal, Mux, If, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litedram.common import LiteDRAMNativePort

class SharedNativePort(LiteXModule):
    def __init__(self, native):
        self.cpu=cpu=LiteDRAMNativePort('both',native.address_width,native.data_width)
        self.video=video=LiteDRAMNativePort('read',native.address_width,native.data_width)
        owner=Signal()
        choose=Signal()
        data=Signal(native.data_width)
        quiet=Signal(4)
        self.comb += choose.eq(video.cmd.valid)
        self.fsm=fsm=FSM(reset_state='CMD')
        fsm.act('CMD',
            native.cmd.valid.eq(cpu.cmd.valid | video.cmd.valid),
            native.cmd.addr.eq(Mux(choose,video.cmd.addr,cpu.cmd.addr)),
            native.cmd.we.eq(~choose & cpu.cmd.we),
            native.cmd.last.eq(Mux(choose,video.cmd.last,cpu.cmd.last)),
            cpu.cmd.ready.eq(native.cmd.ready & ~choose),
            video.cmd.ready.eq(native.cmd.ready & choose),
            If(native.cmd.valid & native.cmd.ready,
                NextValue(owner,choose),
                If(native.cmd.we,NextState('WRITE')).Else(NextState('READ_WAIT'))))
        fsm.act('WRITE',
            native.wdata.valid.eq(cpu.wdata.valid),
            native.wdata.data.eq(cpu.wdata.data),
            native.wdata.we.eq(cpu.wdata.we),
            cpu.wdata.ready.eq(native.wdata.ready),
            If(native.wdata.valid & native.wdata.ready,
                NextValue(quiet,7),NextState('QUIET')))
        fsm.act('READ_WAIT',
            native.rdata.ready.eq(1),
            If(native.rdata.valid,
                NextValue(data,native.rdata.data),NextState('READ_RETURN')))
        fsm.act('READ_RETURN',
            cpu.rdata.valid.eq(~owner),video.rdata.valid.eq(owner),
            cpu.rdata.data.eq(data),video.rdata.data.eq(data),
            If(Mux(owner,video.rdata.ready,cpu.rdata.ready),
                NextValue(quiet,7),NextState('QUIET')))
        fsm.act('QUIET',
            If(quiet==0,NextState('CMD')).Else(NextValue(quiet,quiet-1)))
