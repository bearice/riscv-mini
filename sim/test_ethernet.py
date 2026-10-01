"""Actual MAC slots: little-endian words, RX ownership/drop, TX tails and reset."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import ResetInserter, Signal
from migen.fhdl.specials import Memory
from migen.sim import run_simulation
from liteeth.mac.wishbone import LiteEthMACWishboneInterface
from gateware.constraints import GowinMultiReg
from migen.genlib.cdc import MultiReg

def main():
    dut=ResetInserter()(LiteEthMACWishboneInterface(32,endianness='little',eth_mtu=64,txslots_write_only=True))
    rx=dut.sram.writer;tx=dut.sram.reader
    def send(data,error=False):
        for offset in range(0,len(data),4):
            word=data[offset:offset+4]
            yield dut.sink.valid.eq(1);yield dut.sink.data.eq(int.from_bytes(word,'little'))
            yield dut.sink.be.eq((1<<len(word))-1);yield dut.sink.last.eq(offset+4>=len(data))
            yield dut.sink.error.eq(int(error and offset+4>=len(data)))
            yield
            for _ in range(30):
                if (yield dut.sink.ready):break
                yield
            else:raise AssertionError('RX stalled')
        yield dut.sink.valid.eq(0);yield dut.sink.error.eq(0)
        for _ in range(5):yield
    def release():
        yield rx.ev.pending.wr_data.eq(1);yield rx.ev.pending.wr_stb.eq(1);yield
        yield rx.ev.pending.wr_stb.eq(0)
        for _ in range(3):yield
    def bench():
        first=bytes(range(61));second=bytes(range(31))
        yield from send(first);yield from send(second)
        assert (yield rx._slot.status)==0 and (yield rx._length.status)==61
        assert (yield rx.ev.available.pending)==1
        yield from send(bytes([255])*60)
        assert (yield rx._errors.status)==1 # queue full must not overwrite either owned slot
        for offset in range(0,61,4):
            word=yield from dut.bus_rx.read(offset//4)
            assert word&((1<<min(32,(61-offset)*8))-1)==int.from_bytes(first[offset:offset+4],'little')
        yield from release()
        assert (yield rx._slot.status)==1 and (yield rx._length.status)==31
        yield from release();assert (yield rx.ev.available.pending)==0
        yield from send(bytes(range(40)),error=True)
        assert (yield rx._errors.status)==2 and not (yield rx.ev.available.pending)
        yield from send(bytes(range(65)))
        assert (yield rx._errors.status)==3 and not (yield rx.ev.available.pending)
        # Independent TX RAM: both slots and non-word-aligned tails survive backpressure.
        for index,size in enumerate((61,31)):
            data=bytes((i*37+index)&255 for i in range(size))
            for offset in range(0,size,4):
                yield from dut.bus_tx.write(index*16+offset//4,int.from_bytes(data[offset:offset+4],'little'))
            yield tx._slot.storage.eq(index);yield tx._length.storage.eq(size)
            yield tx._start.wr_stb.eq(1);yield;yield tx._start.wr_stb.eq(0)
            received=bytearray();last=False
            for cycle in range(1000):
                ready=cycle%3!=0;yield dut.source.ready.eq(ready);yield
                if (yield dut.source.valid) and ready:
                    word=(yield dut.source.data).to_bytes(4,'little');be=(yield dut.source.be)
                    received.extend(word[i] for i in range(4) if be&(1<<i))
                    if (yield dut.source.last):last=True;break
            assert last and received==data,(index,received,data)
            for _ in range(6):yield
        yield from send(first);yield dut.reset.eq(1);yield;yield;yield dut.reset.eq(0);yield;yield
        assert not (yield rx.ev.available.pending) and not (yield rx._errors.status)
        assert not (yield tx._level.status)
        yield from send(second)
        assert (yield rx._slot.status)==0 and (yield rx._length.status)==31
    fragment=dut.get_fragment()
    for memory in fragment.specials:
        if isinstance(memory,Memory):
            for port in memory.ports:
                if port.dat_r is None:port.dat_r=Signal(memory.width)
    run_simulation(fragment,bench())
    impl=GowinMultiReg.lower(MultiReg(Signal(32),Signal(32)))
    assert len(impl.regs)==2 and all(('syn_preserve',1) in reg.attr and ('syn_keep',1) in reg.attr for reg in impl.regs)
    print('Ethernet MAC slots/reset and synchronizer preservation passed')

if __name__=='__main__':main()
