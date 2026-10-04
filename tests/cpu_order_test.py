"""MMIO doorbell stays blocked while accepted posted DDR data is pending."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module, Signal
from migen.sim import run_simulation
from litex.soc.interconnect import wishbone
from gateware.cpu_order import CPUOrderBridge


def main():
    fabric=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    idle=Signal()
    dut=CPUOrderBridge(fabric,idle)
    cpu=dut.cpu
    def checks():
        yield cpu.adr.eq(0xf0000000>>2);yield cpu.cyc.eq(1);yield cpu.stb.eq(1);yield cpu.we.eq(1)
        yield fabric.ack.eq(0)
        for _ in range(8):
            yield
            assert (yield dut.drain)
            assert (yield fabric.cyc) and not (yield fabric.stb) and not (yield cpu.ack)
        yield idle.eq(1);yield;yield
        assert (yield fabric.stb)
        yield idle.eq(0)
        for _ in range(4):
            yield
            assert (yield fabric.cyc) and (yield fabric.stb), 'admitted MMIO was retracted'
        yield fabric.ack.eq(1);yield
        assert (yield cpu.ack)
        yield cpu.cyc.eq(0);yield;yield
        assert not (yield cpu.ack)
        yield idle.eq(0);yield cpu.adr.eq(0x40000000>>2);yield cpu.cyc.eq(1);yield;yield
        assert (yield fabric.cyc) and not (yield dut.drain)
        print('CPU MMIO ordering PASS: drain before doorbell, admitted transaction retained, normal DDR requests proceed')
    run_simulation(dut,checks())


if __name__=='__main__':main()
