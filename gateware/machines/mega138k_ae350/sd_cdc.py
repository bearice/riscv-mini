"""Retained APB/AHB SD transactions across the sys/mem clock domains."""
from migen import *
from migen.genlib.cdc import MultiReg, GrayCounter
from migen.genlib.fifo import _FIFOInterface
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.ahb import AHBInterface


class RegisteredFlagAsyncFIFO(Module, _FIFOInterface):
    """Migen AsyncFIFO with registered readable/writable flags.

    Each flag compares the next local pointer with the synchronized remote
    pointer of the previous cycle. The remote pointer only advances, so a
    flag can only be conservatively stale. The read address and both
    pointer increments then never pass through the pointer comparators.

    With ``prefetch_head`` the output register loads either the current or
    the following entry, both read at registered pointers, so the reader's
    pop decode only selects between them and never reaches a storage address.
    """
    prefetch_head = False

    def __init__(self, width, depth):
        _FIFOInterface.__init__(self, width, depth)
        depth_bits = log2_int(depth, True)
        produce = ClockDomainsRenamer("write")(GrayCounter(depth_bits+1))
        consume = ClockDomainsRenamer("read")(GrayCounter(depth_bits+1))
        self.submodules += produce, consume
        self.comb += [produce.ce.eq(self.writable & self.we),
                      consume.ce.eq(self.readable & self.re)]
        produce_rdomain = Signal(depth_bits+1)
        produce.q.attr.add("no_retiming")
        self.specials += MultiReg(produce.q, produce_rdomain, "read")
        consume_wdomain = Signal(depth_bits+1)
        consume.q.attr.add("no_retiming")
        self.specials += MultiReg(consume.q, consume_wdomain, "write")
        self.writable.reset = Constant(1)
        p = produce.q_next
        full = (p[-1] != consume_wdomain[-1]) & (p[-2] != consume_wdomain[-2])
        if depth_bits > 1:
            full = full & (p[:-2] == consume_wdomain[:-2])
        self.sync.write += self.writable.eq(~full)
        self.sync.read += self.readable.eq(consume.q_next != produce_rdomain)
        storage = Memory(self.width, depth)
        self.specials += storage
        wrport = storage.get_port(write_capable=True, read_capable=False, clock_domain="write")
        self.specials += wrport
        self.comb += [wrport.adr.eq(produce.q_binary[:-1]), wrport.dat_w.eq(self.din),
                      wrport.we.eq(produce.ce)]
        if self.prefetch_head:
            current = storage.get_port(async_read=True)
            following = storage.get_port(async_read=True)
            following_adr = Signal(depth_bits)
            head = Signal(self.width, reset_less=True)
            self.specials += current, following
            self.comb += [current.adr.eq(consume.q_binary[:-1]),
                          following.adr.eq(following_adr), self.dout.eq(head)]
            self.sync.read += [following_adr.eq(consume.q_next_binary[:-1] + 1),
                               head.eq(Mux(consume.ce, following.dat_r, current.dat_r))]
        else:
            rdport = storage.get_port(clock_domain="read")
            self.specials += rdport
            self.comb += [rdport.adr.eq(consume.q_next_binary[:-1]), self.dout.eq(rdport.dat_r)]


class PrefetchHeadFIFO(RegisteredFlagAsyncFIFO):
    prefetch_head = True


def crossing(layout, source, target, fifo=RegisteredFlagAsyncFIFO):
    return ClockDomainsRenamer({'write':source,'read':target})(
        stream._FIFOWrapper(fifo, layout, 4))


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
            # Native REQ must remain withdrawn until its low level has
            # crossed back into sys. A single mem-cycle ACK can release
            # native REQ before the slower sys sampler ever observes it low.
            self.specials += MultiReg(held,nack,'mem')
            self.sync += If(ack,held.eq(1)).Elif(~synchronized,held.eq(0))
            self.comb += request.eq(synchronized & ~held & ~ack)
        self.requests=crossing([('off',12),('data',32),('write',1)],'sys','mem')
        # The APB select/commit decode pops the response.
        self.responses=crossing([('data',32),('error',1)],'mem','sys',PrefetchHeadFIFO)
        req,resp=self.requests,self.responses
        # The fabric holds select, commit and the operands until completion,
        # so the request FIFO's write enable can be a register of its own
        # instead of the APB page decode.
        pending,send=Signal(),Signal()
        self.comb += [self.ok.eq(1),self.complete.eq(resp.source.valid),self.error.eq(resp.source.error),
            self.rdata.eq(resp.source.data),req.sink.off.eq(self.off),req.sink.data.eq(self.wdata),
            req.sink.write.eq(self.write),req.sink.valid.eq(send),
            resp.source.ready.eq(self.selected & self.commit)]
        self.sync += [
            If(send & req.sink.ready,send.eq(0),pending.eq(1))
            .Elif(self.selected & self.commit & ~pending & ~send,send.eq(1)),
            If(resp.source.valid & resp.source.ready,pending.eq(0))]
        fsm=FSM(reset_state='IDLE');self.backend=ClockDomainsRenamer('mem')(fsm)
        off,data,result=Signal(12),Signal(32),Signal(32)
        write,error=Signal(),Signal()
        self.comb += [native.off.eq(off),native.wdata.eq(data),native.write.eq(write),
                      resp.sink.data.eq(result),resp.sink.error.eq(error)]
        fsm.act('IDLE',req.source.ready.eq(1),If(req.source.valid,
            NextValue(off,req.source.off),NextValue(data,req.source.data),NextValue(write,req.source.write),NextState('DECODE')))
        # The native read mux settles into free-running registers during
        # DECODE; SAMPLE captures them, keeping the mux off the enables.
        rdata_q,ok_q=Signal(32),Signal()
        self.sync.mem += [rdata_q.eq(native.rdata),ok_q.eq(native.ok)]
        fsm.act('DECODE',native.selected.eq(1),NextState('SAMPLE'))
        fsm.act('SAMPLE',native.selected.eq(1),NextValue(result,rdata_q),NextValue(error,~ok_q),NextState('COMMIT'))
        # A dedicated valid register drives the response FIFO's write enable
        # rather than a decode of the binary FSM state.
        resp_valid=Signal()
        self.comb += resp.sink.valid.eq(resp_valid)
        fsm.act('COMMIT',native.selected.eq(1),native.commit.eq(1),NextValue(resp_valid,1),NextState('RESP'))
        fsm.act('RESP',If(resp.sink.ready,NextValue(resp_valid,0),NextState('IDLE')))
