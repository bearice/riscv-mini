"""APB ownership survives independent USB AXI channel backpressure."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Module, Signal
from migen.sim import run_simulation
from litex.soc.interconnect.axi import AXILiteInterface
from gateware.machines.mega138k_ae350.peripherals.usb import USBRegisterBridge

class USBBridgeTests(unittest.TestCase):
    def test_local_diagnostics_without_native_response(self):
        dut=Module();bus=AXILiteInterface(data_width=32,address_width=32,clock_domain='usb')
        dut.submodules.bridge=b=USBRegisterBridge(bus)
        def cpu():
            yield b.raw_status.eq(9)
            for off,write,data,error in ((0x100,0,0xae350c01,0),(0x104,0,9,0),(0x108,0,60000000,0),(0x10c,0,0,1),(0x104,1,9,1)):
                yield b.off.eq(off);yield b.write.eq(write);yield b.selected.eq(1);yield b.commit.eq(1)
                for _ in range(10):
                    self.assertEqual((yield bus.ar.valid),0)
                    self.assertEqual((yield bus.aw.valid),0)
                    if (yield b.complete):break
                    yield
                else:self.fail('local diagnostic timeout')
                self.assertEqual((yield b.rdata),data);self.assertEqual((yield b.error),error)
                yield b.selected.eq(0);yield b.commit.eq(0);yield;yield
        from migen.fhdl.specials import Memory
        fragment=dut.get_fragment()
        for memory in fragment.specials:
            if isinstance(memory,Memory):
                for port in memory.ports:
                    if port.dat_r is None:port.dat_r=Signal(memory.width)
        run_simulation(fragment,{'sys':cpu()},clocks={'sys':20,'usb':22})

    def test_split_write_and_read_across_clocks(self):
        dut=Module();bus=AXILiteInterface(data_width=32,address_width=32,clock_domain='usb')
        dut.submodules.bridge=b=USBRegisterBridge(bus)
        observed=[]
        def cpu():
            yield b.off.eq(0x14);yield b.wdata.eq(0x12345678)
            yield b.selected.eq(1);yield b.commit.eq(1);yield b.write.eq(1)
            for _ in range(200):
                if (yield b.complete): break
                yield
            else: self.fail('write timeout')
            self.assertEqual((yield b.error),0)
            yield b.selected.eq(0);yield b.commit.eq(0);yield
            yield b.off.eq(4);yield b.write.eq(0);yield b.selected.eq(1);yield b.commit.eq(1)
            for _ in range(200):
                if (yield b.complete): break
                yield
            else: self.fail('read timeout')
            self.assertEqual((yield b.rdata),0xcafebabe)
            yield b.selected.eq(0);yield b.commit.eq(0);yield
        def device():
            yield bus.aw.ready.eq(1)
            for _ in range(120):
                if (yield bus.aw.valid):
                    observed.append(('addr',(yield bus.aw.addr)));break
                yield
            yield;yield bus.aw.ready.eq(0)
            for _ in range(7):yield
            yield bus.w.ready.eq(1)
            for _ in range(120):
                if (yield bus.w.valid):
                    observed.append(('data',(yield bus.w.data)));break
                yield
            yield;yield bus.w.ready.eq(0);yield bus.b.valid.eq(1)
            while not (yield bus.b.ready):yield
            yield;yield bus.b.valid.eq(0);yield bus.ar.ready.eq(1)
            while not (yield bus.ar.valid):yield
            observed.append(('read',(yield bus.ar.addr)))
            yield;yield bus.ar.ready.eq(0);yield bus.r.data.eq(0xcafebabe);yield bus.r.valid.eq(1)
            while not (yield bus.r.ready):yield
            yield;yield bus.r.valid.eq(0)
        from migen.fhdl.specials import Memory
        fragment=dut.get_fragment()
        for memory in fragment.specials:
            if isinstance(memory,Memory):
                for port in memory.ports:
                    if port.dat_r is None: port.dat_r=Signal(memory.width)
        run_simulation(fragment,{'sys':cpu(),'usb':device()},clocks={'sys':20,'usb':22})
        self.assertEqual(observed,[('addr',0x14),('data',0x12345678),('read',4)])
if __name__=='__main__':unittest.main()
