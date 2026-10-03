import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation
from litex.soc.interconnect import wishbone
from gateware.sd_dma import SDReader, SDWriter

def check(write,length,abort=False):
    bus=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    dut=(SDWriter if write else SDReader)(bus)
    def bench():
        yield dut._base.storage.eq(0x40001000)
        yield dut._length.storage.eq(length)
        yield dut._enable.storage.eq(1)
        yield dut.sink.valid.eq(1)
        yield dut.sink.data.eq(0x12345678)
        beats=0;drained=0;cycles=0;pending=None;aborted=False
        while cycles<30000:
            yield bus.ack.eq(cycles%5==4)
            yield bus.dat_r.eq(0x12345678)
            yield dut.source.ready.eq(cycles%3==2)
            yield
            if (yield bus.cyc):
                adr=(yield bus.adr)
                if pending is None:pending=adr
                assert pending==adr,'request address changed under backpressure'
                if abort and not aborted:
                    yield dut._enable.storage.eq(0);aborted=True
                if (yield bus.ack):
                    assert adr==0x10000400+beats,(adr,beats)
                    beats+=1;pending=None
            if (yield dut.source.valid) and (yield dut.source.ready):
                assert (yield dut.source.data)==0x78563412
                drained+=1
            if abort and aborted and not (yield bus.cyc):break
            if (yield dut._done.status):break
            cycles+=1
        assert cycles<30000
        if abort:assert beats==1,'disable cancelled outstanding transfer'
        elif length==0 or length%4 or length>4096:
            assert (yield dut._error.status) and beats==0
        else:
            assert not (yield dut._error.status)
            assert beats==length//4,(beats,length)
            if not write:assert drained==length//4,(drained,length)
    run_simulation(dut,bench())
for write in [False,True]:
    for length in [0,2,8,512,4096,4100]:check(write,length)
    check(write,512,True)
print('PASS bounded SD DMA: SCR/sector/8-sector, invalid lengths, stalls, drain on disable')
