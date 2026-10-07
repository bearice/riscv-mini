"""Byte-accurate DMA checks with independent command/data backpressure."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.native_dma import NativeSDTransfer, NativeDMAArbiter
from gateware.memory import MemoryPort, PortBuffer

def backend(port, memory, counts):
    @passive
    def serve():
        yield port.cmd.ready.eq(1)
        while True:
            while not (yield port.cmd.valid):yield
            address=(yield port.cmd.addr)*16;write=(yield port.cmd.we)
            yield port.cmd.ready.eq(0)
            for _ in range(address%7+3):yield
            counts.append((address,write))
            if write:
                yield port.wdata.ready.eq(1);yield
                while not (yield port.wdata.valid):yield
                data=(yield port.wdata.data);mask=(yield port.wdata.we)
                for i in range(16):
                    if mask>>i&1:memory[address+i]=data>>(i*8)&255
                yield port.wdata.ready.eq(0)
            else:
                yield port.rdata.data.eq(sum(memory.get(address+i,0)<<(8*i) for i in range(16)))
                yield port.rdata.valid.eq(1);yield
                while not (yield port.rdata.ready):yield
                yield port.rdata.valid.eq(0)
            yield port.cmd.ready.eq(1);yield
    return serve()

def sd(write,length,abort=False):
    p=LiteDRAMNativePort('both',23,128);client=MemoryPort('write' if write else 'read')
    dut=Module();dut.submodules.transfer=transfer=NativeSDTransfer(client,write)
    dut.submodules.buffer=PortBuffer(client,p)
    data=bytes((i*37+11)&255 for i in range(length));memory={0x1000+i:v for i,v in enumerate(data)} if not write else {}
    counts=[]
    def check():
        yield transfer._base.storage.eq(0x40001000);yield transfer._length.storage.eq(length);yield transfer._enable.storage.eq(1);yield
        if abort:
            if write:
                for i in range(4):
                    yield transfer.sink.data.eq(i);yield transfer.sink.valid.eq(1);yield
                    while not (yield transfer.sink.ready):yield
                    yield transfer.sink.valid.eq(0);yield
            while not counts:yield
            yield transfer._enable.storage.eq(0)
            for _ in range(80):yield
            assert not (yield transfer._busy.status)
            return
        for offset in range(0,length,4):
            word=int.from_bytes(data[offset:offset+4],'big')
            if write:
                yield transfer.sink.data.eq(word);yield transfer.sink.valid.eq(1);yield
                while not (yield transfer.sink.ready):yield
                yield transfer.sink.valid.eq(0);yield
            else:
                while not (yield transfer.source.valid):yield
                assert (yield transfer.source.data)==word,(write,length,offset,hex((yield transfer.source.data)),hex(word))
                yield transfer.source.ready.eq(1);yield
                yield transfer.source.ready.eq(0)
                for _ in range(offset%3+1):yield
        while not (yield transfer._done.status):yield
        assert not (yield transfer._error.status)
        assert len(counts)==(length+15)//16,(length,counts)
        assert bytes(memory.get(0x1000+i,0) for i in range(length))==data
        if write:assert 0x1000+length not in memory,'partial final beat wrote outside buffer'
    def timeout():
        for _ in range(100000):
            if (yield transfer._done.status) or (abort and counts and not (yield transfer._busy.status)):return
            yield
        raise AssertionError('SD stalled')
    run_simulation(dut,[check(),backend(p,memory,counts),timeout()])

def arbitration():
    p=LiteDRAMNativePort('both',23,128);dut=NativeDMAArbiter(p,3);memory={i:i&255 for i in range(4096)};counts=[];done=[]
    def client(i):
        c=dut.ports[i]
        for n in range(12):
            address=i*64+n
            write=i==2 and n%2==0
            yield c.cmd.we.eq(write);yield c.cmd.addr.eq(address);yield c.cmd.valid.eq(1);yield
            while not (yield c.cmd.ready):yield
            yield c.cmd.valid.eq(0)
            if write:
                for _ in range(4):yield
                yield c.wdata.data.eq(address);yield c.wdata.we.eq(0xffff);yield c.wdata.valid.eq(1);yield
                while not (yield c.wdata.ready):yield
                yield c.wdata.valid.eq(0);yield
                continue
            while not (yield c.rdata.valid):yield
            expected=sum(((address*16+b)&255)<<(8*b) for b in range(16))
            assert (yield c.rdata.data)==expected
            for _ in range(i*3+1):yield
            assert (yield c.rdata.data)==expected,'response changed under backpressure'
            yield c.rdata.ready.eq(1);yield
            yield c.rdata.ready.eq(0);yield
        done.append(i)
    def timeout():
        for _ in range(5000):
            if len(done)==3:return
            yield
        raise AssertionError('DMA arbitration starvation')
    run_simulation(dut,[client(0),client(1),client(2),backend(p,memory,counts),timeout()])
    assert len(counts)==36
    for n in range(0,12,2):
        address=128+n
        assert sum(memory.get(address*16+b,0)<<(8*b) for b in range(16))==address

if __name__=='__main__':
    for write in (False,True):
        for length in (4,8,12,16,64,512,4096):sd(write,length)
        sd(write,64,True)
    arbitration()
    print('Native DMA PASS: SD packed/partial/stop, delayed DDR, ownership/fairness')
