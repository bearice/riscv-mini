"""Native monitor memory protocol: byte lanes, bursts, waits and bounded errors."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen.sim import run_simulation
from litex.soc.interconnect.ahb import AHBInterface
from gateware.machines.mega138k_ae350.cpu import AHBMemory, AE350ROM


class NativeMemoryTest(unittest.TestCase):
    def test_ae350_rom_byte_addresses_and_bounds(self):
        # The hard ROM port has HADDR but no HSIZE. The CPU selects the byte
        # from a complete returned word, including HADDR[1:0] != 0.
        bus = AHBInterface(data_width=32, address_width=32)
        words = [0x50415254, 0x636d2050] + [0] * 2045 + [0x44332211]
        dut = AE350ROM(bus, 8192, init=words)
        def bench():
            yield bus.size.eq(2)
            def transfer(address, expected, error=0, writing=False):
                yield bus.addr.eq(address)
                yield bus.write.eq(writing)
                yield bus.trans.eq(2)
                yield
                yield bus.trans.eq(0)
                yield
                self.assertEqual((yield bus.readyout), 0)
                yield  # registered address -> synchronous memory -> response register
                self.assertEqual((yield bus.readyout), 0)
                self.assertEqual((yield bus.resp), error, hex(address))
                yield
                self.assertEqual((yield bus.readyout), 1)
                self.assertEqual((yield bus.resp), error, hex(address))
                if not error:
                    self.assertEqual((yield bus.rdata), expected, hex(address))
                yield
            for address in range(0x80000000, 0x80000008):
                yield from transfer(address, words[(address - 0x80000000) // 4])
            for address in range(0x80001ffc, 0x80002000):
                yield from transfer(address, 0x44332211)
            for address in (0x7fffffff, 0x80002000, 0x80002001):
                yield from transfer(address, 0, error=1)
            yield from transfer(0x80000001, 0, error=1, writing=True)
            yield from transfer(0x80000001, words[0])
        run_simulation(dut, bench())

    def test_lanes_read_after_write_and_errors(self):
        bus = AHBInterface(data_width=64, address_width=32)
        dut = AHBMemory(bus, 8192)
        def bench():
            yield bus.sel.eq(1)
            def transfer(address, size=3, data=None, error=0):
                yield bus.addr.eq(address)
                yield bus.size.eq(size)
                yield bus.trans.eq(2)
                yield bus.write.eq(data is not None)
                yield
                yield bus.trans.eq(0)
                if data is not None:
                    yield bus.wdata.eq(data)
                yield
                self.assertEqual((yield bus.readyout), 0)
                self.assertEqual((yield bus.resp), error)
                yield
                self.assertEqual((yield bus.readyout), 1)
                self.assertEqual((yield bus.resp), error)
                value = (yield bus.rdata)
                yield
                return value
            yield from transfer(0, data=0x0123456789abcdef)
            self.assertEqual((yield from transfer(0)), 0x0123456789abcdef)
            yield from transfer(3, 0, data=0xaa000000)
            yield from transfer(4, 2, data=0xfedcba9800000000)
            self.assertEqual((yield from transfer(0)), 0xfedcba98aaabcdef)
            yield from transfer(8192, data=0, error=1)
            yield from transfer(1, 2, data=0, error=1)
            yield from transfer(0, 4, data=0, error=1)
            self.assertEqual((yield from transfer(0)), 0xfedcba98aaabcdef)
        run_simulation(dut, bench())

    def test_back_to_back_rom_and_write_rejection(self):
        bus = AHBInterface(data_width=32, address_width=32)
        dut = AHBMemory(bus, 8192, base=0x80000000, init=[0x12345678, 0xabcdef01], read_only=True)
        def bench():
            yield bus.size.eq(2)
            yield bus.addr.eq(0x80000000)
            yield bus.trans.eq(2)
            yield
            yield
            self.assertEqual((yield bus.readyout), 0)
            # Hold the next SEQ address during the wait and response cycles.
            yield bus.addr.eq(0x80000004)
            yield bus.trans.eq(3)
            yield
            self.assertEqual((yield bus.readyout), 1)
            self.assertEqual((yield bus.rdata), 0x12345678)
            yield
            yield bus.trans.eq(0)
            self.assertEqual((yield bus.readyout), 0)
            yield
            self.assertEqual((yield bus.readyout), 1)
            self.assertEqual((yield bus.rdata), 0xabcdef01)
            yield
            yield bus.addr.eq(0x80000000)
            yield bus.trans.eq(2)
            yield bus.write.eq(1)
            yield
            yield bus.trans.eq(0)
            yield
            self.assertEqual((yield bus.resp), 1)
            self.assertEqual((yield bus.readyout), 0)
            yield
            self.assertEqual((yield bus.resp), 1)
            self.assertEqual((yield bus.readyout), 1)
        run_simulation(dut, bench())


if __name__ == '__main__':
    unittest.main()
