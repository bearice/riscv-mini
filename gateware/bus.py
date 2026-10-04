"""Register classical Wishbone requests/replies to bound combinational paths."""
from migen import FSM, NextState, NextValue, If, Signal
from litex.gen import LiteXModule

class WishbonePipeline(LiteXModule):
    def __init__(self,upstream,downstream,burst_read=False,burst_write=False,early_launch=False):
        self.fsm=fsm=FSM(reset_state='IDLE')
        error=Signal();abandoned=Signal()
        if not burst_read:
            request=[NextValue(getattr(downstream,n),getattr(upstream,n)) for n in ('adr','dat_w','sel','we')]
            fsm.act('IDLE',NextValue(abandoned,0),If(upstream.cyc & upstream.stb,*request,NextState('WAIT')))
            fsm.act('WAIT',downstream.cyc.eq(1),downstream.stb.eq(1),
                If(~upstream.cyc,NextValue(abandoned,1)),
                If(downstream.ack | downstream.err,
                    NextValue(upstream.dat_r,downstream.dat_r),
                    NextValue(error,downstream.err),NextState('REPLY')))
            fsm.act('REPLY',upstream.ack.eq(~error & ~abandoned),upstream.err.eq(error & ~abandoned),NextState('IDLE'))
            return
        reply_data=Signal.like(upstream.dat_r);burst_open=Signal()
        fields=('adr','dat_w','sel','we','cti','bte')
        latched={n:Signal.like(getattr(downstream,n)) for n in fields}
        from migen import Mux
        self.comb += [getattr(downstream,n).eq(Mux(fsm.ongoing('IDLE'),getattr(upstream,n),latched[n]) if early_launch else latched[n]) for n in fields]
        self.comb += upstream.dat_r.eq(reply_data)
        request=[NextValue(latched[n],getattr(upstream,n)) for n in fields]
        fsm.act('IDLE',NextValue(abandoned,0),downstream.cyc.eq(burst_open & upstream.cyc),
            If(~upstream.cyc,NextValue(burst_open,0)),
            If(upstream.cyc & upstream.stb,*request,
                *([downstream.cyc.eq(1),downstream.stb.eq(1)] if early_launch else []),
                If(downstream.ack | downstream.err,
                    NextValue(reply_data,downstream.dat_r),NextValue(error,downstream.err),
                    NextValue(burst_open,(burst_write | ~upstream.we) & (upstream.cti==2)),
                    NextState('REPLY')).Else(NextState('WAIT')) if early_launch else NextState('WAIT')))
        fsm.act('WAIT',downstream.cyc.eq(1),downstream.stb.eq(1),
            If(~upstream.cyc,NextValue(abandoned,1)),
            If(downstream.ack | downstream.err,
                NextValue(reply_data,downstream.dat_r),
                NextValue(burst_open,(burst_write | ~latched['we']) & (latched['cti']==2) & ~abandoned & upstream.cyc),
                NextValue(error,downstream.err),NextState('REPLY')))
        # The original master retains ownership until its registered reply.
        live=upstream.cyc if early_launch else 1
        fsm.act('REPLY',downstream.cyc.eq(burst_open & live),upstream.ack.eq(~error & ~abandoned & live),
            upstream.err.eq(error & ~abandoned & live),
            NextState('IDLE'))
