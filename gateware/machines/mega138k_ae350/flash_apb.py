"""Owned APB transactions for the SDK's Flash Register R/W interface."""
from migen import *
from litex.gen import LiteXModule


class FlashRegisterAdapter(LiteXModule):
    def __init__(self, port):
        self.off,self.wdata,self.rdata = Signal(12),Signal(32),Signal(32)
        self.selected,self.write,self.commit,self.complete,self.error = [Signal() for _ in range(5)]
        self.ok = Signal(reset=1)
        off,data,result = Signal(12),Signal(32),Signal(32)
        write,error = Signal(),Signal()
        offsets=(0,0x10,0x14,0x20,0x24,0x28,0x2c,0x30,0x34,0x38,0x3c,0x40,0x50,0x60,0x64,0x7c)
        hit = Cat(*[self.off == value for value in offsets]) != 0
        readonly = Cat(*[self.off == value for value in (0,0x34,0x64,0x7c)]) != 0
        valid = hit & (~self.write | ~readonly)
        self.comb += [port.addr.eq(0xf8000000 | off),port.wdata.eq(data),port.write.eq(write),
                      self.rdata.eq(result),self.error.eq(error),self.ok.eq(1)]
        self.fsm = f = FSM(reset_state='IDLE')
        f.act('IDLE',If(self.selected & self.commit,
            NextValue(off,self.off),NextValue(data,self.wdata),NextValue(write,self.write),NextValue(error,~valid),
            If(valid,NextState('SETUP')).Else(NextValue(result,0),NextState('RESPONSE'))))
        f.act('SETUP',port.select.eq(1),NextState('ACCESS'))
        f.act('ACCESS',port.select.eq(1),port.enable.eq(1),
              If(port.ready,NextValue(result,port.rdata),NextState('RESPONSE')))
        f.act('RESPONSE',self.complete.eq(1),If(self.selected & self.commit,NextState('IDLE')))
