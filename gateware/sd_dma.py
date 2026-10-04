"""Bounded SD DDR DMA: up to eight sectors, one retained bus request.

No loop mode, 64-bit addressing or unbounded length counters. Disable drains
an outstanding bus request before returning idle. Stream byte order matches
LiteX little-endian DMA used by the existing SD byte converters.
"""
from migen import Signal, If, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStorage, CSRStatus

class SDTransfer(LiteXModule):
    def __init__(self, bus, write, endianness='little', with_csr=True, burst_write=False):
        burst_write=burst_write and write
        self._base=CSRStorage(32, name='base')
        self._length=CSRStorage(13, name='length')
        self._enable=CSRStorage(name='enable')
        self._done=CSRStatus(name='done')
        self._error=CSRStatus(name='error')
        self._offset=CSRStatus(11, name='offset')
        self.sink=stream.Endpoint([('data',32)])
        self.source=stream.Endpoint([('data',32)])
        address=Signal(bus.adr_width); remaining=Signal(11); offset=Signal(11)
        word=Signal(32)
        burst_open=Signal()
        starved=Signal(8)
        enable=self._enable.storage
        valid_config=(self._base.storage[:2]==0)&(self._base.storage>=0x40000000)&(self._base.storage<0x48000000)&(self._length.storage[:2]==0)&(self._length.storage>0)&(self._length.storage<=4096)
        self.comb += [bus.adr.eq(address),bus.we.eq(write),bus.sel.eq(15),
            bus.dat_w.eq(word),self._offset.status.eq(offset)]
        # Original DMA swaps byte order before/after its stream converter.
        def swap(value):
            from migen import Cat
            return Cat(value[24:32],value[16:24],value[8:16],value[:8]) if endianness=='little' else value
        self.comb += self.source.data.eq(swap(word))
        self.fsm=fsm=FSM(reset_state='IDLE')
        fsm.act('IDLE',NextValue(offset,0),NextValue(self._done.status,0),NextValue(self._error.status,0),
            If(enable,
                If(valid_config,NextValue(address,self._base.storage[2:]),
                    NextValue(remaining,self._length.storage[2:]),NextState('LOAD' if write else 'BUS'))
                .Else(NextValue(self._error.status,1),NextState('DONE'))))
        on_input=If(self.sink.valid,NextValue(starved,0),NextValue(word,swap(self.sink.data)),NextState('BUS'))
        if burst_write:
            on_input=on_input.Elif(burst_open,
                If(starved==255,NextState('FLUSH')).Else(NextValue(starved,starved+1)))
        fsm.act('LOAD',
            *([bus.cyc.eq(burst_open)] if burst_write else []),
            If(~enable,NextState('FLUSH' if burst_write else 'IDLE')).Else(self.sink.ready.eq(1),
                on_input))
        fsm.act('BUS',bus.cyc.eq(1),bus.stb.eq(1),
            *([bus.cti.eq(7),If((remaining!=1)&(address[:2]!=3),bus.cti.eq(2)),
               If(bus.ack,NextValue(burst_open,(remaining!=1)&(address[:2]!=3)))] if burst_write else []),
            If(bus.ack|bus.err,
                If(bus.err,NextValue(self._error.status,1),NextState('DONE'))
                .Elif(~enable,NextState('FLUSH' if burst_write else 'IDLE'))
                .Else(
                    NextValue(offset,offset+1),
                    *([NextValue(word,bus.dat_r),NextState('DRAIN')] if not write else [
                        If(remaining==1,NextState('DONE')).Else(NextValue(remaining,remaining-1),NextValue(address,address+1),NextState('LOAD'))]))))
        fsm.act('DRAIN',self.source.valid.eq(enable),self.source.last.eq(remaining==1),
            If(~enable,NextState('IDLE')).Elif(self.source.ready,
                If(remaining==1,NextState('DONE')).Else(NextValue(remaining,remaining-1),NextValue(address,address+1),NextState('BUS'))))
        fsm.act('DONE',NextValue(self._done.status,1),If(~enable,NextState('IDLE')))
        if burst_write:
            fsm.act('FLUSH',
                If(burst_open,
                    bus.cyc.eq(1),bus.stb.eq(1),bus.cti.eq(7),bus.sel.eq(0),
                    If(bus.ack|bus.err,NextValue(burst_open,0),NextValue(starved,0),NextState('LOAD'))
                ).Else(NextState('IDLE')))

class SDReader(SDTransfer):
    def __init__(self,bus,endianness='little',with_csr=True):
        super().__init__(bus,False,endianness,with_csr)

class SDWriter(SDTransfer):
    def __init__(self,bus,endianness='little',with_csr=True,burst_write=False):
        super().__init__(bus,True,endianness,with_csr,burst_write)
