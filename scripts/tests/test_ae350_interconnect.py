"""Joint decode + SRAM + DDR adapter test with a protocol-correct AHB master.

Covers the interleavings a CPU executing from DDR produces: a DDR read in
flight while the next address hits SRAM, SRAM completing into DDR, alternating
fetch/data traffic across the two slaves, back-to-back slave switching, and
error completion followed by a switch.

Run: python scripts/tests/test_ae350_interconnect.py     (exit 0 = pass)
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts' / 'tests'))

from migen import *
from migen.sim import run_simulation, passive
from litex.soc.interconnect.ahb import AHBInterface
from litex.gen import LiteXModule
from gateware.machines.ddr3_ahb import DDR3AHB, DDR3UI
from gateware.machines.ae350_interconnect import MemInterconnect, ErrorSlave
from test_ddr3_ahb import StubFIFO, Mon, Model


class IndependentCounter(LiteXModule):
    """Counts address-phase handshakes on the master and on each slave."""
    def __init__(self, master, sram, ddr, err):
        self.master = Signal(32)
        self.sram = Signal(32)
        self.ddr = Signal(32)
        self.err = Signal(32)
        self.wrong_sram = Signal(32)
        self.wrong_ddr = Signal(32)
        self.sync += [
            If(master.trans[1] & master.readyout, self.master.eq(self.master + 1)),
            If(sram.trans[1] & sram.readyout,
               self.sram.eq(self.sram + 1),
               If(~((sram.addr >= 0) & (sram.addr < 8192)), self.wrong_sram.eq(self.wrong_sram + 1)),
            ),
            If(ddr.trans[1] & ddr.readyout,
               self.ddr.eq(self.ddr + 1),
               If(~(ddr.addr >= 0x2000), self.wrong_ddr.eq(self.wrong_ddr + 1)),
            ),
            If(err.trans[1] & err.readyout, self.err.eq(self.err + 1)),
        ]


class DUT(LiteXModule):
    def __init__(self):
        self.master = AHBInterface(data_width=64, address_width=32)
        self.sram_bus = AHBInterface(data_width=64, address_width=32)
        self.ddr_bus = AHBInterface(data_width=64, address_width=32)
        self.err_bus = AHBInterface(data_width=64, address_width=32)
        self.ui = DDR3UI()
        self.ready = Signal(reset=1)
        self.sram_mem = {}          # byte-word dict owned by the SRAM model
        self.ddr = DDR3AHB(self.ddr_bus, self.ui, ready_i=self.ready, base=0x2000,
                           size=1 << 30, response_timeout=64, ui_timeout=32,
                           fifo_cls=StubFIFO, accepted_i=self.ddr_bus.trans[1])
        self.err = ErrorSlave(self.err_bus)
        self.ic = MemInterconnect(self.master, [
            (0x0, 8192, self.sram_bus),
            (0x2000, 1 << 30, self.ddr_bus),
            (0, 1 << 32, self.err_bus),
        ])
        self.mon = ClockDomainsRenamer({"sys": "ddr"})(Mon(self.ui))
        self.seen = IndependentCounter(self.master, self.sram_bus, self.ddr_bus, self.err_bus)

    def accept_count(self):
        return self.ic.n_accepted


def xact(bus, write, addr, data=0, size=2, timeout=400):
    """One AHB-Lite transfer: one acceptance edge, then the data phase."""
    yield bus.addr.eq(addr)
    yield bus.size.eq(size)
    yield bus.write.eq(1 if write else 0)
    yield bus.wdata.eq(data)
    yield bus.sel.eq(0xff)
    yield bus.trans.eq(2)
    yield                      # address phase: HTRANS high for exactly one cycle
    yield bus.trans.eq(0)      # deassert for the data phase (no second accept)
    yield                      # first data-phase cycle
    guard = 0
    while not (yield bus.readyout):
        yield
        guard += 1
        if guard > timeout:
            raise RuntimeError('data phase timeout at %08x' % addr)
    resp, rdata = (yield bus.resp), (yield bus.rdata)
    yield
    return resp, rdata


@passive
def sram_model(bus, mem):
    """Combinational SRAM model: one-cycle response, byte-addressable words."""
    yield bus.readyout.eq(1)
    yield bus.rdata.eq(0)
    yield bus.resp.eq(0)
    while True:
        if (yield bus.sel) and ((yield bus.trans) >> 1) & 1:
            addr = (yield bus.addr) & 0x1fff
            if (yield bus.write):
                size = (yield bus.size)
                lanes = (1 << (1 << size)) - 1
                data = (yield bus.wdata)
                for lane in range(8):
                    if lanes >> (lane + (addr & 7)) & 1:
                        mem[(addr & ~7) + lane] = (data >> (8 * lane)) & 0xff
            else:
                word = 0
                for lane in range(8):
                    word |= mem.get((addr & ~7) + lane, 0) << (8 * lane)
                yield bus.rdata.eq(word)
        yield


def main():
    dut = DUT()
    model = Model(dut.ui, dut.mon)
    fails = []

    def check(name, cond, detail=''):
        print(('PASS ' if cond else 'FAIL ') + name + ((' ' + str(detail)) if detail != '' else ''))
        if not cond:
            fails.append(name)

    def tb():
        m = dut.master
        # ---- both slaves, distinct data, and each acceptance counted once
        r, _ = yield from xact(m, True, 0x1000, 0x1111222233334444, size=3)
        check('sram write accepted', r == 0, r)
        r, d = yield from xact(m, False, 0x1000, size=3)
        check('sram read back', d == 0x1111222233334444, hex(d))
        r, _ = yield from xact(m, True, 0x2000, 0xaabbccdd00112233)
        check('ddr write accepted', r == 0, r)
        r, d = yield from xact(m, False, 0x2000)
        check('ddr read back', (d & 0xffffffff) == 0x00112233, hex(d))

        # ---- DDR read in flight, next address hits SRAM (fetch/data split)
        n0 = (yield dut.ic.n_accepted)
        r, _ = yield from xact(m, True, 0x1010, 0xdeadbeefdeadbeef, size=3)
        r2, d2 = yield from xact(m, False, 0x2000)
        check('ddr read after sram write', (d2 & 0xffffffff) == 0x00112233, hex(d2))
        r3, d3 = yield from xact(m, False, 0x1010, size=3)
        check('sram read after ddr read', d3 == 0xdeadbeefdeadbeef, hex(d3))
        check('accept counter advanced by three', (yield dut.ic.n_accepted) == n0 + 3,
              ((yield dut.ic.n_accepted), n0))

        # ---- alternating DDR (code fetch) and SRAM data traffic
        for i in range(6):
            r, d = yield from xact(m, False, 0x2000 + 8 * i)      # DDR fetch-like
            if r != 0:
                check('alternating ddr %d' % i, False, r)
                break
            r, _ = yield from xact(m, True, 0x1100 + 8 * i, 0x55aa0000 + i, size=2)
            if r != 0:
                check('alternating sram %d' % i, False, r)
                break
        else:
            check('alternating ddr/sram traffic', True)
        for i in range(6):
            r, d = yield from xact(m, False, 0x1100 + 8 * i)
            if (d & 0xffffffff) != 0x55aa0000 + i:
                check('alternating sram read %d' % i, False, hex(d))
                break
        else:
            check('alternating sram data intact', True)

        # ---- back-to-back slave switching: the SRAM write address is
        # presented while the DDR read is in its data phase, so it must be
        # accepted in the DDR completion cycle.
        yield m.addr.eq(0x2000); yield m.size.eq(2); yield m.write.eq(0)
        yield m.sel.eq(0xff); yield m.trans.eq(2)
        yield                    # address phase
        yield                    # first data-phase cycle
        yield m.addr.eq(0x1200); yield m.write.eq(1); yield m.wdata.eq(0x12345678)
        guard = 0
        while not (yield m.readyout):
            yield
            guard += 1
            if guard > 400:
                raise RuntimeError('switch: ddr completion timeout')
        yield m.trans.eq(0)
        yield
        guard = 0
        while not (yield m.readyout):
            yield
            guard += 1
            if guard > 400:
                raise RuntimeError('switch: sram completion timeout')
        check('switch completion: sram bytes captured',
              [dut.sram_mem.get(0x1200 + i, 0) for i in range(4)] == [0x78, 0x56, 0x34, 0x12],
              [hex(dut.sram_mem.get(0x1200 + i, 0)) for i in range(4)])
        r, d = yield from xact(m, False, 0x1200, size=3)
        check('switch completion: sram store landed', (d & 0xffffffff) == 0x12345678, hex(d))

        # ---- unmapped address: error, then a clean SRAM access
        r, _ = yield from xact(m, False, 0x80000000)
        check('unmapped address errors', r == 1, r)
        r, d = yield from xact(m, False, 0x1200, size=3)
        check('recovered after unmapped error', r == 0 and (d & 0xffffffff) == 0x12345678, hex(d))

        seen = dut.seen
        # ---- independent accounting: the slave-side monitors count their own
        # address-phase handshakes, so this is not the interconnect's counter
        # compared with itself.
        check('every accepted address reached exactly one slave',
              (yield seen.master) == (yield dut.ic.n_accepted)
              == (yield seen.sram) + (yield seen.ddr) + (yield seen.err),
              ((yield seen.master), (yield dut.ic.n_accepted),
               (yield seen.sram), (yield seen.ddr), (yield seen.err)))
        check('no slave saw an address it did not own',
              (yield seen.wrong_sram) == 0 and (yield seen.wrong_ddr) == 0,
              ((yield seen.wrong_sram), (yield seen.wrong_ddr)))
    run_simulation(dut, {'sys': [sram_model(dut.sram_bus, dut.sram_mem), model.run(), tb()]},
                   clocks={'sys': 10, 'ddr': 10})
    print('RESULT', 'PASS' if not fails else 'FAIL: ' + ','.join(fails))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
