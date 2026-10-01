"""Register classical Wishbone requests/replies to bound combinational paths."""
from migen import FSM, NextState, NextValue, If, Signal
from litex.gen import LiteXModule

class WishbonePipeline(LiteXModule):
    def __init__(self,upstream,downstream):
        self.fsm=fsm=FSM(reset_state='IDLE')
        error=Signal();abandoned=Signal()
        request=[NextValue(getattr(downstream,n),getattr(upstream,n)) for n in ('adr','dat_w','sel','we')]
        fsm.act('IDLE',NextValue(abandoned,0),If(upstream.cyc & upstream.stb,*request,NextState('WAIT')))
        fsm.act('WAIT',downstream.cyc.eq(1),downstream.stb.eq(1),
            If(~upstream.cyc,NextValue(abandoned,1)),
            If(downstream.ack | downstream.err,
                NextValue(upstream.dat_r,downstream.dat_r),
                NextValue(error,downstream.err),NextState('REPLY')))
        # The original master retains ownership until its registered reply.
        fsm.act('REPLY',upstream.ack.eq(~error & ~abandoned),
            upstream.err.eq(error & ~abandoned),
            NextState('IDLE'))
