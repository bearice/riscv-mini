"""AHB <-> DDR3AHB protocol test with interface stubs (no Memory, no CDC).

The stubs replace the asynchronous FIFOs with a 1-entry bypass so the AHB front
end, the DDR user-interface engine and their data layout can be exercised in a
single clock domain.  A separate CDC test is required before release; this test
covers: directions, all transfer sizes and lanes, window bounds, address
translation, back-to-back transactions, error response and recovery.

Run: python scripts/tests/test_ddr3_ahb.py   (exit code 0 = pass)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from migen import *
from migen.sim import run_simulation, passive
from litex.soc.interconnect.ahb import AHBInterface
from litex.gen import LiteXModule
from gateware.machines.ddr3_ahb import DDR3AHB, DDR3UI


class StubFIFO(LiteXModule):
    """Registered 1-entry queue: keeps valid/ready semantics without Memory."""
    def __init__(self, width, depth=1):
        self.din = Signal(width)
        self.we = Signal()
        self.writable = Signal(reset=1)
        self.dout = Signal(width)
        self.re = Signal()
        self.readable = Signal()
        echo = Signal(width)
        full = Signal()
        self.comb += [self.writable.eq(~full), self.readable.eq(full), self.dout.eq(echo)]
        self.sync += [
            If(self.re & full,
                full.eq(0),
                If(self.we, echo.eq(self.din), full.eq(1)),
            ).Elif(self.we & ~full,
                echo.eq(self.din),
                full.eq(1),
            ),
        ]


class Mon(LiteXModule):
    """Latches the user-interface handshake in the ddr domain (sampling helper)."""
    def __init__(self, ui):
        self.n = Signal(16)
        self.cmd = Signal(3)
        self.addr = Signal(29)
        self.mask = Signal(32)
        self.data = Signal(256)
        self.sync += If(ui.cmd_en & ui.cmd_ready,
                        self.n.eq(self.n + 1),
                        self.cmd.eq(ui.cmd),
                        self.addr.eq(ui.addr),
                        self.mask.eq(ui.wr_data_mask),
                        self.data.eq(ui.wr_data))


class DUT(LiteXModule):
    def __init__(self, base=0x2000, size=1 << 30, fifo_cls=StubFIFO, data_width=64,
                 response_timeout=64, ui_timeout=32):
        self.bus = AHBInterface(data_width=data_width, address_width=32)
        self.ui = DDR3UI()
        self.ready = Signal(reset=1)
        self.ddr = DDR3AHB(self.bus, self.ui, ready_i=self.ready, base=base, size=size,
                           response_timeout=response_timeout, ui_timeout=ui_timeout,
                           fifo_cls=fifo_cls)
        self.mon = ClockDomainsRenamer({"sys": "ddr"})(Mon(self.ui))


class Model:
    """DDR user-interface model driven by the ddr-domain monitor."""
    def __init__(self, ui, mon):
        self.ui = ui
        self.mon = mon
        self.mem = {}
        self.events = []
        self.stall = False
        self.bp = False
        self.delay_read = 0
        self.toggle = 0

    @passive
    def run(self):
        ui, mon = self.ui, self.mon
        yield ui.cmd_ready.eq(1)
        yield ui.rd_data_valid.eq(0)
        yield ui.wr_data_rdy.eq(1)
        seen = 0
        while True:
            yield
            if self.stall:
                yield ui.cmd_ready.eq(0)
                continue
            if self.bp:
                self.toggle ^= 1
                yield ui.cmd_ready.eq(self.toggle)
                if not self.toggle:
                    continue
            else:
                yield ui.cmd_ready.eq(1)
            n = (yield mon.n)
            if n != seen:
                seen = n
                cmd = yield mon.cmd
                addr = yield mon.addr
                mask = yield mon.mask
                data = yield mon.data
                self.events.append(dict(cmd=cmd, addr=addr, mask=mask, data=data))
                if cmd == 0:
                    # BL8: the command address selects the 32-byte block; the
                    # four low address bits select the word inside that block.
                    block = addr & ~7
                    line = self.mem.get(block, 0)
                    for byte in range(32):
                        if not (mask >> byte) & 1:
                            line = (line & ~(0xff << (8 * byte))) | (((data >> (8 * byte)) & 0xff) << (8 * byte))
                    self.mem[block] = line
                else:
                    yield ui.cmd_ready.eq(0)
                    delay = 3 + self.delay_read
                    self.delay_read = 0
                    for _ in range(delay):
                        yield
                    yield ui.rd_data.eq(self.mem.get(addr & ~7, 0))
                    yield ui.rd_data_valid.eq(1)
                    yield
                    yield ui.rd_data_valid.eq(0)
                    yield ui.cmd_ready.eq(1)


def ahb(bus, write, addr, data=0, size=2, timeout=400):
    """One AHB-Lite transaction with explicit address/data phases."""
    # Recovery may hold HREADY low even between transfers. An address must
    # remain presented until its acceptance edge, not disappear after a tick.
    for _ in range(timeout):
        if (yield bus.readyout):
            break
        yield
    else:
        raise RuntimeError('AHB address acceptance timeout')
    yield bus.addr.eq(addr)
    yield bus.size.eq(size)
    yield bus.write.eq(1 if write else 0)
    yield bus.wdata.eq(data)
    yield bus.sel.eq(0xff)
    yield bus.trans.eq(2)
    yield                      # address phase visible to the slave
    yield bus.trans.eq(0)
    yield                      # slave latches the address phase here
    for _ in range(timeout):
        if (yield bus.readyout):
            resp, rdata = (yield bus.resp), (yield bus.rdata)
            yield            # master advances after the completion edge
            return resp, rdata
        yield
    raise RuntimeError('AHB data phase timeout at %08x' % addr)


def ahb_pipelined(bus, xacts, timeout=400):
    """True AHB pipelining: the next address is held through the completion.

    The slave raises HREADY for two cycles (completion + hold) and accepts the
    next address in the hold cycle, which is what an AHB master may rely on.
    """
    results = []
    n = len(xacts)
    write0, addr0, data0, size0 = xacts[0]
    yield bus.addr.eq(addr0)
    yield bus.size.eq(size0)
    yield bus.write.eq(1 if write0 else 0)
    yield bus.wdata.eq(data0)
    yield bus.sel.eq(0xff)
    yield bus.trans.eq(2)
    yield
    if n > 1:
        write1, addr1, _, size1 = xacts[1]
        yield bus.addr.eq(addr1)
        yield bus.size.eq(size1)
        yield bus.write.eq(1 if write1 else 0)
        yield bus.trans.eq(2)      # held until the slave accepts it
    for _ in range(timeout):
        if (yield bus.readyout):
            results.append(((yield bus.resp), (yield bus.rdata)))
            yield                  # master advances after the completion edge
            break
        yield
    else:
        raise RuntimeError('pipelined AHB timeout at %08x' % addr0)
    for index in range(1, n):
        write, addr, data, size = xacts[index]
        yield bus.trans.eq(0)
        yield bus.wdata.eq(data)
        for _ in range(timeout):
            if (yield bus.readyout):
                results.append(((yield bus.resp), (yield bus.rdata)))
                yield
                break
            yield
        else:
            raise RuntimeError('pipelined AHB timeout at %08x' % addr)
    return results


def main():
    dut = DUT()
    model = Model(dut.ui, dut.mon)
    fails = []

    def check(name, cond, detail=''):
        print(('PASS ' if cond else 'FAIL ') + name + ((' ' + str(detail)) if detail != '' else ''))
        if not cond:
            fails.append(name)

    def tb():
        bus = dut.bus
        # single word write at the window base
        r, _ = yield from ahb(bus, True, 0x2000, 0x1122334455667788)
        check('word write accepted', r == 0)
        check('write cmd is write', model.events[0]['cmd'] == 0, model.events[0]['cmd'])
        check('write addr translated to 0', model.events[0]['addr'] == 0, model.events[0]['addr'])
        check('word write enables 4 lanes', (~model.events[0]['mask'] & 0xff) == 0x0f,
              hex(model.events[0]['mask']))
        check('write data in lane 0', (model.events[0]['data'] & 0xffffffffffffffff) == 0x1122334455667788,
              hex(model.events[0]['data']))
        r, d = yield from ahb(bus, False, 0x2000)
        check('word read accepted', r == 0)
        check('word read data (32-bit lanes only)', d == 0x0000000055667788, hex(d))
        # upper half of the 64-bit beat -> byte lanes 8..15
        r, _ = yield from ahb(bus, True, 0x2008, 0x99aabbccddeeff00)
        check('offset-8 enables lanes 8..11', (~model.events[-1]['mask'] & 0xff00) == (0x0f << 8),
              hex(model.events[-1]['mask']))
        check('offset-8 data in lane 1',
              (model.events[-1]['data'] >> 64) & 0xffffffffffffffff == 0x99aabbccddeeff00,
              hex(model.events[-1]['data']))
        r, d = yield from ahb(bus, False, 0x2008)
        check('offset-8 read (upper lane of beat)', d == 0x00000000ddeeff00, hex(d))
        # next 32-byte block
        r, _ = yield from ahb(bus, True, 0x2020, 0x0123456789abcdef)
        check('block+1 write addr', model.events[-1]['addr'] == 8, model.events[-1]['addr'])
        r, d = yield from ahb(bus, False, 0x2020)
        check('block+1 read', d == 0x0000000089abcdef, hex(d))
        # byte and halfword writes: only the addressed lanes may change
        r, _ = yield from ahb(bus, True, 0x2001, 0x5a00, size=0)
        check('byte write mask', (~model.events[-1]['mask'] & 0xff) == 0x02, hex(model.events[-1]['mask']))
        r, d = yield from ahb(bus, False, 0x2000)
        check('byte write preserved neighbours', d == 0x55665a88, hex(d))
        r, _ = yield from ahb(bus, True, 0x2002, 0xbeef0000, size=1)
        check('half write enables lanes 2..3', (~model.events[-1]['mask'] & 0xff) == 0x0c,
              hex(model.events[-1]['mask']))
        r, d = yield from ahb(bus, False, 0x2000)
        check('half write preserved neighbours', d == 0xbeef5a88, hex(d))
        # back-to-back transactions (no idle cycle in between)
        r1, _ = yield from ahb(bus, True, 0x2040, 0x1111222233334444)
        r2, _ = yield from ahb(bus, True, 0x2048, 0x5555666677778888)
        check('back-to-back writes', r1 == 0 and r2 == 0, (r1, r2))
        check('consecutive words map to one block',
              [e['addr'] for e in [x for x in model.events if x['cmd'] == 0][-2:]] == [0x10, 0x10],
              [hex(e['addr']) for e in [x for x in model.events if x['cmd'] == 0][-2:]])
        # 64-bit transfer writes all eight byte lanes of the beat
        r, _ = yield from ahb(bus, True, 0x2060, 0x0f1e2d3c4b5a6978, size=3)
        check('size-3 enables 8 lanes', (~model.events[-1]['mask'] & 0xff) == 0xff,
              hex(model.events[-1]['mask']))
        r, d = yield from ahb(bus, False, 0x2060, size=3)
        check('size-3 round-trip', d == 0x0f1e2d3c4b5a6978, hex(d))
        # LANE/ADDRESS MATRIX: same block, different CPU offsets, distinct data
        for (off, val, lane_bytes) in ((0x2000, 0xaaaa0000, 0x0000000f),
                                       (0x2004, 0xbbbb0000 << 32, 0x000000f0),
                                       (0x2008, 0xcccc0000, 0x00000f00),
                                       (0x200c, 0xdddd0000 << 32, 0x0000f000)):
            r, _ = yield from ahb(bus, True, off, val)
            check('matrix write %08x mask' % off, (~model.events[-1]['mask'] & 0xffff) == lane_bytes,
                  hex(model.events[-1]['mask']))
            check('matrix write %08x block' % off, model.events[-1]['addr'] == 0, model.events[-1]['addr'])
        r, d = yield from ahb(bus, False, 0x2000, size=3)
        check('matrix read beat 0 carries lanes 0/1', d == 0xbbbb0000aaaa0000, hex(d))
        # the AHB beat is 64-bit: the CPU selects the 32-bit word inside it
        for off, beat in ((0x2000, 0xbbbb0000aaaa0000), (0x2004, 0xbbbb0000aaaa0000),
                          (0x2008, 0xdddd0000cccc0000), (0x200c, 0xdddd0000cccc0000)):
            r, d = yield from ahb(bus, False, off)
            check('matrix read beat %08x' % off, d == beat, hex(d))

        # BACK-TO-BACK: next address held through the completion (proven shape)
        bus = dut.bus
        yield bus.addr.eq(0x20a0); yield bus.size.eq(2); yield bus.write.eq(1)
        yield bus.wdata.eq(0x0102030405060708); yield bus.sel.eq(0xff); yield bus.trans.eq(2)
        yield
        yield bus.addr.eq(0x20a8); yield bus.wdata.eq(0x1112131415161718)
        yield
        done = False
        for _ in range(80):
            if (yield bus.readyout):
                yield          # master advances on the cycle after it samples HREADY
                done = True
                break
            yield
        check('back-to-back first completion', done)
        yield bus.trans.eq(0)
        done2 = False
        for _ in range(80):
            if (yield bus.readyout):
                yield
                done2 = True
                break
            yield
        check('back-to-back second completion', done2)
        for _ in range(40):
            if len([e for e in model.events if e['cmd'] == 0]) >= 2:
                break
            yield
        writes = [e for e in model.events if e['cmd'] == 0]
        check('back-to-back issued two commands',
              [e['addr'] for e in writes[-2:]] == [0x28, 0x28],
              [hex(e['addr']) for e in writes[-2:]])
        check('back-to-back masks address different lanes',
              [(~e['mask']) & 0xffff for e in writes[-2:]] == [0x000f, 0x0f00],
              [(hex(e['mask']), hex((~e['mask']) & 0xffff)) for e in writes[-2:]])

        # DIRECTED LATE RESPONSE: A is accepted by the DDR side, its data is
        # delayed past every fabric timeout, then B is issued.  B must never
        # accept A's data.
        yield from ahb(bus, True, 0x20c0, 0xaaaaaaaa)     # prime the block
        reads_before = len([e for e in model.events if e['cmd'] == 1])
        model.delay_read = 45                              # A's read data late
        r, d = yield from ahb(bus, False, 0x20c0)
        check('A read command was accepted before the delay',
              len([e for e in model.events if e['cmd'] == 1]) == reads_before + 1,
              (reads_before, len([e for e in model.events if e['cmd'] == 1])))
        check('A reports the timeout error', r == 1, (r, hex(d)))
        check('A did not return stale data', (d & 0xffffffff) != 0xaaaaaaaa, hex(d))
        r, _ = yield from ahb(bus, True, 0x20c8, 0xbbbbbbbb)   # B, issued right after
        check('B write accepted after A recovery', r == 0, r)
        r, d = yield from ahb(bus, False, 0x20c8)
        check('B read returns B data', (d & 0xffffffff) == 0xbbbbbbbb, hex(d))

        # BACKPRESSURE: the DDR side accepts commands only on alternate cycles
        model.bp = True
        r, _ = yield from ahb(bus, True, 0x20b0, 0x0c0ffee0)
        check('write under backpressure accepted', r == 0, r)
        r, d = yield from ahb(bus, False, 0x20b0)
        check('read under backpressure correct', (d & 0xffffffff) == 0x0c0ffee0, hex(d))
        model.bp = False

        # CONTINUOUS READS / WRITES / ALTERNATING (no gaps, standard AHB)
        for i in range(4):
            r, _ = yield from ahb(bus, True, 0x20d0 + 8 * i, 0x10000000 + i)
            check('continuous write %d' % i, r == 0, r)
        for i in range(4):
            r, d = yield from ahb(bus, False, 0x20d0 + 8 * i)
            check('continuous read %d' % i, (d & 0xffffffff) == 0x10000000 + i, hex(d))
        for i in range(3):
            r, _ = yield from ahb(bus, True, 0x20f0 + 8 * i, 0x20000000 + i)
            r2, d = yield from ahb(bus, False, 0x20f0 + 8 * i)
            check('alternating rw %d' % i, r == 0 and r2 == 0 and (d & 0xffffffff) == 0x20000000 + i,
                  (r, r2, hex(d)))

        # bounds
        r, _ = yield from ahb(bus, False, 0x1000)
        check('below-base rejected', r == 1)
        r, d = yield from ahb(bus, False, 0x2000)
        check('recovered after below-base', r == 0)
        r, _ = yield from ahb(bus, False, 0x2002)
        check('unaligned rejected', r == 1)
        r, d = yield from ahb(bus, False, 0x2000)
        check('recovered after unaligned', r == 0)
        yield dut.ready.eq(0)
        r, _ = yield from ahb(bus, False, 0x2000)
        check('not-ready rejected', r == 1)
        yield dut.ready.eq(1)
        r, d = yield from ahb(bus, False, 0x2000)
        check('recovered after not-ready', r == 0)

    run_simulation(dut, {'sys': tb(), 'ddr': model.run()}, clocks={'sys': 10, 'ddr': 10})
    print('RESULT', 'PASS' if not fails else 'FAIL: ' + ','.join(fails))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
