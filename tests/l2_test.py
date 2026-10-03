"""L2 against a delayed wide memory: lanes, collisions, byte writes and aborts."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import ResetInserter
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from gateware.l2 import ReadL2

def main():
    master=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    slave=wishbone.Interface(data_width=128,address_width=32,addressing='word')
    dut=ResetInserter()(ReadL2(master,slave,size=256,bypass_base=0x1000))
    memory={i:sum(((i*4+j)^0xabc00000)<<(32*j) for j in range(4)) for i in range(300)}
    transactions=[]
    error_address=299

    @passive
    def backend():
        while True:
            yield
            if not ((yield slave.cyc) and (yield slave.stb)):continue
            address=(yield slave.adr);write=(yield slave.we)
            data=(yield slave.dat_w);select=(yield slave.sel)
            transactions.append((address,write,select))
            for _ in range(3):
                yield
                assert (yield slave.adr)==address
                assert (yield slave.cyc), 'Downstream operation abandoned prematurely'
            if address==error_address:yield slave.err.eq(1)
            else:
                if write:
                    previous=memory.get(address,0)
                    for b in range(16):
                        if select & (1<<b):
                            previous=(previous & ~(255<<(b*8))) | (data & (255<<(b*8)))
                    memory[address]=previous
                yield slave.dat_r.eq(memory.get(address,0))
                yield slave.ack.eq(1)
            yield
            yield slave.ack.eq(0);yield slave.err.eq(0)

    def access(address,write=False,data=0,select=15,expect_error=False):
        yield master.adr.eq(address);yield master.we.eq(write)
        yield master.dat_w.eq(data);yield master.sel.eq(select)
        yield master.cyc.eq(1);yield master.stb.eq(1)
        for _ in range(100):
            yield
            if (yield master.ack) or (yield master.err):break
        else:raise AssertionError('Wishbone stalled')
        assert bool((yield master.err))==expect_error
        result=(yield master.dat_r)
        yield master.cyc.eq(0);yield master.stb.eq(0)
        yield
        return result

    def checks():
        for _ in range(20):yield
        start=len(transactions)
        for word in range(4):assert (yield from access(word))==word^0xabc00000
        assert len(transactions)-start==1,'Four lanes must share one DDR fill'
        # Same index, different tag: both conflict and refill correctly.
        assert (yield from access(64))==64^0xabc00000
        assert (yield from access(0))==0xabc00000
        # Every selected byte survives a partial write and invalidates the line.
        yield from access(1,True,0x11223344,5)
        assert (memory[0]>>32)&0xffffffff==0xab220044
        assert (yield from access(1))==0xab220044
        for lane in range(4):
            yield from access(lane,True,0x12345678+lane)
            assert (yield from access(lane))==0x12345678+lane
        # Disable/re-enable: no stale tags after uncached overwrites of data RAM.
        yield dut._enable.storage.eq(0)
        assert (yield from access(64))==64^0xabc00000
        yield dut._enable.storage.eq(1)
        assert (yield from access(0))==0x12345678
        before=len(transactions)
        assert (yield from access(1024))==1024^0xabc00000
        assert (yield from access(1024))==1024^0xabc00000
        assert len(transactions)-before==2,'Framebuffer bypass must never hit'
        yield from access(error_address*4,expect_error=True)
        # Cancel a miss after launch; drain it without replying to a new owner.
        yield master.adr.eq(128);yield master.cyc.eq(1);yield master.stb.eq(1)
        for _ in range(5):yield
        yield master.cyc.eq(0);yield master.stb.eq(0)
        for _ in range(15):
            yield
            assert not (yield master.ack) and not (yield master.err)
        assert (yield from access(128))==128^0xabc00000
        # Reset invalidates all tags even though BSRAM contents persist.
        yield dut.reset.eq(1);yield;yield dut.reset.eq(0)
        for _ in range(20):yield
        memory[32]=0x98765432
        before=len(transactions)
        assert (yield from access(128))==0x98765432
        assert len(transactions)-before==1
        assert (yield from access(128))==0x98765432
        stats=(yield dut._stats.status)
        assert stats&0xffff and stats>>16
        print('L2 PASS: lane reuse, collision, partial writes, bypass, errors, abort;',len(transactions),'DDR transactions')

    run_simulation(dut,[checks(),backend()])

if __name__=='__main__':main()
