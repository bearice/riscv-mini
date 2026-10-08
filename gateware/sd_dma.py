"""Bounded SD DDR DMA: up to eight sectors, one retained bus request.

No loop mode, 64-bit addressing or unbounded length counters. Disable drains
an outstanding bus request before returning idle. Stream byte order matches
LiteX little-endian DMA used by the existing SD byte converters.
"""
from gateware.memory_map import RAM_BASE, RAM_END
from migen import Signal, If, FSM, NextState, NextValue
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStorage, CSRStatus

class SDTransfer(LiteXModule):
    def __init__(self, bus, write, endianness='little', with_csr=True):
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
        enable=self._enable.storage
        valid_config=(self._base.storage[:2]==0)&(self._base.storage>=RAM_BASE)&(self._base.storage<RAM_END)&(self._length.storage[:2]==0)&(self._length.storage>0)&(self._length.storage<=4096)
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
        fsm.act('LOAD',
            If(~enable,NextState('IDLE')).Else(self.sink.ready.eq(1),
                If(self.sink.valid,NextValue(word,swap(self.sink.data)),NextState('BUS'))))
        fsm.act('BUS',bus.cyc.eq(1),bus.stb.eq(1),
            If(bus.ack|bus.err,
                If(bus.err,NextValue(self._error.status,1),NextState('DONE'))
                .Elif(~enable,NextState('IDLE'))
                .Else(
                    NextValue(offset,offset+1),
                    *([NextValue(word,bus.dat_r),NextState('DRAIN')] if not write else [
                        If(remaining==1,NextState('DONE')).Else(NextValue(remaining,remaining-1),NextValue(address,address+1),NextState('LOAD'))]))))
        fsm.act('DRAIN',self.source.valid.eq(enable),self.source.last.eq(remaining==1),
            If(~enable,NextState('IDLE')).Elif(self.source.ready,
                If(remaining==1,NextState('DONE')).Else(NextValue(remaining,remaining-1),NextValue(address,address+1),NextState('BUS'))))
        fsm.act('DONE',NextValue(self._done.status,1),If(~enable,NextState('IDLE')))

class SDReader(SDTransfer):
    def __init__(self,bus,endianness='little',with_csr=True):
        super().__init__(bus,False,endianness,with_csr)

class SDWriter(SDTransfer):
    def __init__(self,bus,endianness='little',with_csr=True):
        super().__init__(bus,True,endianness,with_csr)
