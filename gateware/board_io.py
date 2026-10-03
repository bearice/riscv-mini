"""Dock IO in schematic order; all logic runs on sys clock enables."""
from gateware.csr_layout import packed_status
from migen import Signal, If, Cat, Mux
from migen.genlib.cdc import MultiReg
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStorage, CSRStatus
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourceLevel

class BoardIO(LiteXModule):
    def __init__(self,leds,buttons_n,switches,debounce_cycles=300000):
        self._leds=CSRStorage(6,name='leds')
        packed_status(self, 'board_io')
        self._clear=CSR(8,name='clear')
        raw=Signal(4);stable=Signal(4);switch_sync=Signal(4)
        self.specials += [MultiReg(~buttons_n,raw),MultiReg(switches,switch_sync)]
        changes=Signal(4)
        for i in range(4):
            count=Signal(max=debounce_cycles)
            self.comb += changes[i].eq((raw[i]!=stable[i]) & (count==debounce_cycles-1))
            self.sync += If(raw[i]==stable[i],count.eq(0)).Elif(count==debounce_cycles-1,
                stable[i].eq(raw[i]),count.eq(0)).Else(count.eq(count+1))
        clear=Signal(8)
        self.comb += [leds.eq(~self._leds.storage),self._buttons.status.eq(stable),
            self._switches.status.eq(switch_sync),clear.eq(Mux(self._clear.wr_stb,self._clear.wr_data,0))]
        self.sync += [self._pressed.status.eq((self._pressed.status & ~clear[:4]) | (changes & raw)),
                      self._released.status.eq((self._released.status & ~clear[4:]) | (changes & ~raw))]
        self.ev=EventManager();self.ev.change=EventSourceLevel();self.ev.finalize()
        self.comb += self.ev.change.trigger.eq((self._pressed.status | self._released.status)!=0)

class WS2812(LiteXModule):
    def __init__(self,pad,clock_hz=60000000):
        self._color=CSRStorage(24,name='color')  # CPU: 0xRRGGBB
        self._send=CSR(name='send')
        self._busy=CSRStatus(name='busy')
        self._completed=CSRStatus(32,name='completed')
        period=round(clock_hz*1.25e-6);zero_high=round(clock_hz*.40e-6);one_high=round(clock_hz*.80e-6)
        latch=round(clock_hz*300e-6)
        count=Signal(max=latch);shift=Signal(24);left=Signal(5)
        busy=Signal(reset=1);sending=Signal();initial=Signal(reset=1)
        self.comb += [self._busy.status.eq(busy),
                     pad.eq(busy & sending & ((shift[23] & (count<one_high)) | (~shift[23] & (count<zero_high))))]
        self.sync += If(busy,
            If(sending,
                If(count==period-1,count.eq(0),
                    If(left==0,sending.eq(0)).Else(left.eq(left-1),shift.eq(shift<<1)))
                .Else(count.eq(count+1)))
            .Else(If(count==latch-1,busy.eq(0),count.eq(0),initial.eq(0),
                     If(~initial,self._completed.status.eq(self._completed.status+1)))
                  .Else(count.eq(count+1))))
        self.sync += If(self._send.wr_stb & ~busy,busy.eq(1),sending.eq(1),count.eq(0),left.eq(23),
                        shift.eq(Cat(self._color.storage[:8],self._color.storage[16:24],self._color.storage[8:16])))
