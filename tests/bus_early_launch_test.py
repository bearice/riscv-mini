"""Early launch keeps replies registered and suppresses a cancelled reply."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module,Signal,If
from migen.sim import run_simulation
from litex.soc.interconnect import wishbone
from gateware.bus import WishbonePipeline


def main():
    u=wishbone.Interface();d=wishbone.Interface()
    dut=Module();dut.submodules.pipe=WishbonePipeline(u,d,burst_read=True,early_launch=True)
    requests=Signal(8)
    dut.comb += [d.ack.eq(d.cyc & d.stb),d.dat_r.eq(0x12345678)]
    dut.sync += If(d.cyc & d.stb & d.ack,requests.eq(requests+1))
    def checks():
        yield u.cyc.eq(1);yield u.stb.eq(1);yield u.adr.eq(32);yield
        assert not (yield u.ack),'reply not registered'
        yield
        assert (yield u.ack)
        assert (yield u.dat_r)==0x12345678
        yield u.cyc.eq(0);yield u.stb.eq(0);yield
        assert (yield requests)==1,'zero-latency target issued twice'
        yield;yield u.cyc.eq(1);yield u.stb.eq(1);yield
        # Drop CYC as the registered reply becomes visible, e.g. switch slaves.
        yield u.cyc.eq(0);yield u.stb.eq(0);yield
        assert not (yield u.ack),'cancelled RAM ACK escaped into decoder OR'
        print('Early launch PASS: registered reply, exactly-once immediate ACK and cancelled reply suppression')
    run_simulation(dut,checks())


if __name__=='__main__':main()
