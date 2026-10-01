"""Actual ULPI FSM: read turnaround/NXT=0, preemption, final STP, serial IO."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Record
from migen.sim import run_simulation
from gateware.usb import USBPHYInit

def main():
    pads=Record([('dir',1),('nxt',1),('stp',1)])
    dut=USBPHYInit(pads)
    def bench():
        def wait_command(expected):
            for _ in range(100):
                if (yield dut.do)==expected and (yield dut.oe)==255:return
                yield
            raise AssertionError(f'No ULPI command {expected:02x}')
        for address,value in enumerate((0x24,4,6,0)):
            yield from wait_command(0xc0|address)
            yield pads.nxt.eq(1);yield
            yield pads.nxt.eq(0);yield pads.dir.eq(1);yield dut.di.eq(0xee);yield
            # Register reads have NXT=0. The first DIR cycle is turnaround.
            yield dut.di.eq(value);yield
            yield;yield pads.dir.eq(0)
            for _ in range(4):yield
        assert (yield dut.phy_id)==0x00060424
        for address,value in ((4,0x45),(0x0a,0x26),(7,9)):
            yield from wait_command(0x80|address)
            if address==4:
                # A PHY RXCMD can preempt before a write's data is accepted.
                yield pads.dir.eq(1);yield;yield
                yield pads.dir.eq(0);yield
                yield from wait_command(0x80|address)
            yield pads.nxt.eq(1);yield
            yield pads.nxt.eq(0);yield
            assert (yield dut.do)==value
            if address==0x0a:
                # Also retry when CMD was accepted, but DATA was preempted.
                yield pads.dir.eq(1);yield;yield
                yield pads.dir.eq(0);yield
                yield from wait_command(0x80|address)
                yield pads.nxt.eq(1);yield
                yield pads.nxt.eq(0);yield
                assert (yield dut.do)==value
            yield pads.nxt.eq(1);yield;yield
            yield pads.nxt.eq(0)
            for _ in range(5):
                if (yield pads.stp):break
                yield
            else:raise AssertionError('Missing STP on register write')
            yield
        for _ in range(1040):yield
        assert (yield dut.ready) and not (yield dut.error)
        assert (yield dut.oe)==7 and not (yield pads.stp)
        yield dut.dp_oe.eq(1);yield dut.dp_o.eq(1);yield
        assert (yield dut.do)&7==3
        yield dut.dp_o.eq(0);yield dut.dm_o.eq(1);yield
        assert (yield dut.do)&7==1
        yield dut.dm_o.eq(0);yield
        assert (yield dut.do)&7==5
    run_simulation(dut,{'ulpi':bench()},clocks={'ulpi':10})
    print('USB PHY PASS: ID reads, turnaround, write retry/STP and serial outputs')
if __name__=='__main__':main()
