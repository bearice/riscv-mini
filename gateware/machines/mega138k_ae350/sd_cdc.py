"""Retained APB/AHB SD transactions across the sys/mem clock domains."""
from migen import *
from migen.genlib.cdc import MultiReg, PulseSynchronizer
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.ahb import AHBInterface


def crossing(layout, source, target):
    return ClockDomainsRenamer({'write':source,'read':target})(stream.AsyncFIFO(layout,depth=4))


class AHBClockCrossing(LiteXModule):
    """One owned beat; cancellation cannot discard an accepted transaction."""
    def __init__(self, slave, master):
        self.requests=crossing([('addr',32),('data',32),('write',1),('size',3)],'sys','mem')
        self.responses=crossing([('data',32),('error',1)],'mem','sys')
        req,resp=self.requests,self.responses
        addr,data=Signal(32),Signal(32)
        write,size=Signal(),Signal(3)
        result,error=Signal(32),Signal()
        self.comb += [req.sink.addr.eq(addr),req.sink.write.eq(write),req.sink.size.eq(size),
                      req.sink.data.eq(slave.wdata),slave.rdata.eq(result)]
        self.front=f=FSM(reset_state='IDLE')
        f.act('IDLE',slave.readyout.eq(1),If(slave.trans[1],
            NextValue(addr,slave.addr),NextValue(write,slave.write),NextValue(size,slave.size),NextState('SEND')))
        f.act('SEND',req.sink.valid.eq(1),If(req.sink.ready,NextState('WAIT')))
        f.act('WAIT',resp.source.ready.eq(1),If(resp.source.valid,
            NextValue(result,resp.source.data),If(resp.source.error,NextState('ERROR1')).Else(NextState('DONE'))))
        capture=[NextValue(addr,slave.addr),NextValue(write,slave.write),NextValue(size,slave.size),NextState('SEND')]
        f.act('DONE',slave.readyout.eq(1),If(slave.trans[1],*capture).Else(NextState('IDLE')))
        f.act('ERROR1',slave.resp.eq(1),NextState('ERROR2'))
        f.act('ERROR2',slave.readyout.eq(1),slave.resp.eq(1),If(slave.trans[1],*capture).Else(NextState('IDLE')))
        back=FSM(reset_state='IDLE');self.back=ClockDomainsRenamer('mem')(back)
        maddr,mdata,mresult=Signal(32),Signal(32),Signal(32)
        mwrite,msize,merror=Signal(),Signal(3),Signal()
        self.comb += [master.addr.eq(maddr),master.wdata.eq(mdata),master.write.eq(mwrite),
                      master.size.eq(msize),master.sel.eq(1),resp.sink.data.eq(mresult),resp.sink.error.eq(merror)]
        back.act('IDLE',req.source.ready.eq(1),If(req.source.valid,
            NextValue(maddr,req.source.addr),NextValue(mdata,req.source.data),NextValue(mwrite,req.source.write),
            NextValue(msize,req.source.size),NextState('ADDRESS')))
        back.act('ADDRESS',master.trans.eq(2),If(master.readyout,NextState('DATA')))
        back.act('DATA',If(master.readyout,NextValue(mresult,master.rdata),NextValue(merror,master.resp),NextState('RESP')))
        back.act('RESP',resp.sink.valid.eq(1),If(resp.sink.ready,NextState('IDLE')))


class SDClockCrossing(LiteXModule):
    """APB completion waits for the actual native-register operation.

    REQ is blocked immediately in sys on ACK, and held blocked until the
    synchronized native REQ falls; CDC latency cannot duplicate a request.
    """
    def __init__(self,native):
        self.off,self.wdata,self.rdata=Signal(12),Signal(32),Signal(32)
        self.selected,self.write,self.commit,self.ok=(Signal() for _ in range(4))
        self.complete,self.error=Signal(),Signal()
        self.tx_bus=AHBInterface(data_width=32,address_width=32)
        self.rx_bus=AHBInterface(data_width=32,address_width=32)
        self.tx=AHBClockCrossing(self.tx_bus,native.tx_bus)
        self.rx=AHBClockCrossing(self.rx_bus,native.rx_bus)
        self.tx_req,self.rx_req,self.tx_ack,self.rx_ack=(Signal() for _ in range(4))
        for raw,request,ack,nack in ((native.tx_req,self.tx_req,self.tx_ack,native.tx_ack),
                                     (native.rx_req,self.rx_req,self.rx_ack,native.rx_ack)):
            synchronized=Signal();held=Signal()
            self.specials += MultiReg(raw,synchronized,'sys')
            event=PulseSynchronizer('sys','mem')
            self.submodules += event
            previous=Signal()
            self.sync += previous.eq(ack)
            self.comb += [event.i.eq(ack & ~previous),nack.eq(event.o)]
            self.sync += If(ack,held.eq(1)).Elif(~synchronized,held.eq(0))
            self.comb += request.eq(synchronized & ~held & ~ack)
        self.requests=crossing([('off',12),('data',32),('write',1)],'sys','mem')
        self.responses=crossing([('data',32),('error',1)],'mem','sys')
        req,resp=self.requests,self.responses
        pending=Signal()
        self.comb += [self.ok.eq(1),self.complete.eq(resp.source.valid),self.error.eq(resp.source.error),
            self.rdata.eq(resp.source.data),req.sink.off.eq(self.off),req.sink.data.eq(self.wdata),
            req.sink.write.eq(self.write),req.sink.valid.eq(self.selected & self.commit & ~pending),
            resp.source.ready.eq(self.selected & self.commit)]
        self.sync += If(req.sink.valid & req.sink.ready,pending.eq(1)).Elif(resp.source.valid & resp.source.ready,pending.eq(0))
        fsm=FSM(reset_state='IDLE');self.backend=ClockDomainsRenamer('mem')(fsm)
        off,data,result=Signal(12),Signal(32),Signal(32)
        write,error=Signal(),Signal()
        self.comb += [native.off.eq(off),native.wdata.eq(data),native.write.eq(write),
                      resp.sink.data.eq(result),resp.sink.error.eq(error)]
        fsm.act('IDLE',req.source.ready.eq(1),If(req.source.valid,
            NextValue(off,req.source.off),NextValue(data,req.source.data),NextValue(write,req.source.write),NextState('DECODE')))
        fsm.act('DECODE',native.selected.eq(1),NextValue(result,native.rdata),NextValue(error,~native.ok),NextState('COMMIT'))
        fsm.act('COMMIT',native.selected.eq(1),native.commit.eq(1),NextState('RESP'))
        fsm.act('RESP',resp.sink.valid.eq(1),If(resp.sink.ready,NextState('IDLE')))
