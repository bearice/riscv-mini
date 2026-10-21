"""AHB burst efficiency and isolation against an independent BL8 memory model."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from migen import Signal, Module, Constant, ClockDomainsRenamer
from migen.fhdl.specials import Memory
from migen.sim import run_simulation
from scripts.tests.test_ddr3_ahb import DUT, Model, ahb
from scripts.tests.test_ddr3_ahb import Mon
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3AHB, DDR3UI
from gateware.machines.mega138k_ae350.interconnect import MemInterconnect, ErrorSlave
from litex.soc.interconnect.ahb import AHBInterface


class RegisteredDUT(Module):
    def __init__(self, response_timeout=200_000, ui_timeout=1 << 18):
        self.bus = AHBInterface(data_width=64)
        ddr, error = AHBInterface(data_width=64), AHBInterface(data_width=64)
        self.ui = DDR3UI()
        self.ready = Signal(reset=1)
        self.submodules.ddr = DDR3AHB(ddr, self.ui, self.ready, base=0x2000,
            response_timeout=response_timeout, ui_timeout=ui_timeout,
            accepted_i=ddr.trans[1],
            end_burst_i=self.bus.readyout & (self.bus.trans == 0),
            lookahead=self.bus)
        self.submodules.error = ErrorSlave(error)
        self.submodules.ic = MemInterconnect(self.bus, [(0x2000, 1 << 30, ddr),
                                                       (0, 1 << 32, error)], registered=True)
        self.submodules.mon = ClockDomainsRenamer({'sys': 'ddr'})(Mon(self.ui))


def read_burst(bus, addresses, size=3, burst=3, protections=None):
    """Keep the next address presented during the preceding data phase."""
    result = []
    yield bus.write.eq(0)
    yield bus.size.eq(size)
    yield bus.burst.eq(burst)
    yield bus.prot.eq(protections[0] if protections else 3)
    yield bus.addr.eq(addresses[0])
    yield bus.trans.eq(2)
    yield
    # The first address is accepted on the next edge; subsequent addresses
    # remain stable until their acceptance at the previous completion edge.
    for index, address in enumerate(addresses):
        if index + 1 < len(addresses):
            yield bus.addr.eq(addresses[index + 1])
            if protections:
                yield bus.prot.eq(protections[index + 1])
            yield bus.trans.eq(3)
        else:
            yield bus.trans.eq(0)
        yield
        for _ in range(300):
            if (yield bus.readyout):
                result.append(((yield bus.resp), (yield bus.rdata)))
                yield
                break
            yield
        else:
            raise AssertionError('burst did not complete at %08x' % address)
    return result


class BurstTests(unittest.TestCase):
    def exercise(self, addresses, size, burst, commands, real_fifo=False, registered=False, protections=None):
        if registered:
            dut = RegisteredDUT()
        elif real_fifo:
            from migen.genlib.fifo import AsyncFIFO
            dut = DUT(fifo_cls=AsyncFIFO)
        else:
            dut = DUT()
        model = Model(dut.ui, dut.mon)
        for address in addresses:
            block = (address - 0x2000) >> 2 & ~7
            model.mem[block] = sum((0xabc00000 + block + i) << (32*i) for i in range(8))
        def process():
            results = yield from read_burst(dut.bus, addresses, size, burst, protections)
            for address, (error, value) in zip(addresses, results):
                word = ((address - 0x2000) & ~7) >> 2
                want = 0xabc00000 + word | ((0xabc00001 + word) << 32)
                self.assertEqual((error, value), (0, want))
            self.assertEqual(len(model.events), commands)
            # An independent NONSEQ must refetch; no invisible persistent cache.
            model.mem[0] = 0x1122334455667788
            error, value = yield from ahb(dut.bus, False, 0x2000, size=3)
            self.assertEqual((error, value), (0, 0x1122334455667788))
            self.assertEqual(len(model.events), commands + 1)
        fragment = dut.get_fragment()
        for memory in fragment.specials:
            if isinstance(memory, Memory):
                for port in memory.ports:
                    if port.dat_r is None:
                        port.dat_r = Signal(memory.width)
        run_simulation(fragment, {'sys': process(), 'ddr': model.run()},
                       clocks={'sys': 10, 'ddr': 14 if real_fifo else 10})

    def test_incr4_64(self):
        self.exercise([0x2000, 0x2008, 0x2010, 0x2018], 3, 3, 1)

    def test_incr8_32(self):
        self.exercise(list(range(0x2000, 0x2020, 4)), 2, 5, 1)

    def test_wrap4(self):
        self.exercise([0x2018, 0x2000, 0x2008, 0x2010], 3, 2, 1)

    def test_cross_block(self):
        self.exercise([0x2018, 0x2020, 0x2028, 0x2030], 3, 3, 2)

    def test_real_async_fifo(self):
        self.exercise([0x2000, 0x2008, 0x2010, 0x2018], 3, 3, 1, True)

    def test_discontinuous_seq_is_not_a_hit(self):
        self.exercise([0x2000, 0x2010], 3, 3, 2)

    def test_single_cannot_be_extended_by_seq(self):
        self.exercise([0x2000, 0x2008], 3, 0, 2)

    def test_fixed_length_context_expires(self):
        # The fifth SEQ after an INCR4 is malformed: it must start a fresh
        # transfer even when it aliases the previously buffered block.
        self.exercise([0x2000, 0x2008, 0x2010, 0x2018, 0x2000], 3, 3, 2)

    def test_indefinite_incr_crosses_blocks(self):
        self.exercise(list(range(0x2000, 0x2040, 8)), 3, 1, 2)

    def test_production_registered_decoder_and_cdc(self):
        self.exercise([0x2000, 0x2008, 0x2010, 0x2018], 3, 3, 1,
                      real_fifo=True, registered=True)

    def test_changed_protection_cannot_reuse_block(self):
        self.exercise([0x2000, 0x2008], 3, 3, 2, protections=[3, 1])

    def test_busy_preserves_burst_without_creating_transfers(self):
        from migen.genlib.fifo import AsyncFIFO
        for registered in (False, True):
            with self.subTest(registered=registered):
                dut = RegisteredDUT() if registered else DUT(fifo_cls=AsyncFIFO)
                model = Model(dut.ui, dut.mon)
                model.mem[0] = 0x1122334455667788 | (0x8877665544332211 << 64)
                def process():
                    bus = dut.bus
                    yield bus.addr.eq(0x2000)
                    yield bus.size.eq(3)
                    yield bus.burst.eq(3)
                    yield bus.trans.eq(2)
                    yield
                    yield bus.trans.eq(1)  # BUSY during and after data phase
                    yield
                    for _ in range(300):
                        if (yield bus.readyout):
                            self.assertEqual((yield bus.rdata), 0x1122334455667788)
                            yield
                            break
                        yield
                    else:
                        self.fail('first beat timed out')
                    for _ in range(5):
                        yield
                    yield bus.addr.eq(0x2008)
                    yield bus.trans.eq(3)
                    yield
                    yield  # observe the accepted address before deasserting
                    yield bus.trans.eq(0)
                    yield
                    for _ in range(300):
                        if (yield bus.readyout):
                            self.assertEqual(((yield bus.resp), (yield bus.rdata)),
                                             (0, 0x8877665544332211),
                                             str((model.events, (yield dut.ddr.last_bad),
                                                  (yield dut.ddr.n_req), (yield dut.ddr.n_error),
                                                  (yield dut.ddr.recovering))))
                            self.assertEqual(len(model.events), 1)
                            yield
                            return
                        yield
                    self.fail('beat after BUSY timed out')
                fragment = dut.get_fragment()
                for memory in fragment.specials:
                    if isinstance(memory, Memory):
                        for port in memory.ports:
                            if port.dat_r is None:
                                port.dat_r = Signal(memory.width)
                run_simulation(fragment, {'sys': process(), 'ddr': model.run()},
                               clocks={'sys': 10, 'ddr': 14})


def write_burst(bus, beats, size=3, burst=3, prot=0xf):
    """Drive a write burst. ``beats`` is a list of (address, wdata).

    The next address stays presented through the current data phase, and each
    beat's write data is held until that beat completes.
    """
    result = []
    addresses = [address for address, _ in beats]
    yield bus.write.eq(1)
    yield bus.size.eq(size)
    yield bus.burst.eq(burst)
    yield bus.prot.eq(prot)
    yield bus.addr.eq(addresses[0])
    yield bus.wdata.eq(beats[0][1])
    yield bus.trans.eq(2)
    yield
    for index, (address, data) in enumerate(beats):
        if index + 1 < len(beats):
            yield bus.addr.eq(beats[index + 1][0])
            yield bus.trans.eq(3)
        else:
            yield bus.trans.eq(0)
        yield
        for _ in range(400):
            if (yield bus.readyout):
                result.append((yield bus.resp))
                yield
                if index + 1 < len(beats):
                    yield bus.wdata.eq(beats[index + 1][1])
                break
            yield
        else:
            raise AssertionError('write burst did not complete at %08x' % address)
    return result


def run_dut(dut, process, model, real_fifo=False):
    fragment = dut.get_fragment()
    for memory in fragment.specials:
        if isinstance(memory, Memory):
            for port in memory.ports:
                if port.dat_r is None:
                    port.dat_r = Signal(memory.width)
    run_simulation(fragment, {'sys': process(), 'ddr': model.run()},
                   clocks={'sys': 10, 'ddr': 14 if real_fifo else 10})


class WriteAggregationTests(unittest.TestCase):
    def make(self, real_fifo=False, registered=False, response_timeout=200_000, ui_timeout=1 << 18):
        if registered:
            dut = RegisteredDUT(response_timeout=response_timeout, ui_timeout=ui_timeout)
            real_fifo = True
        elif real_fifo:
            from migen.genlib.fifo import AsyncFIFO
            dut = DUT(fifo_cls=AsyncFIFO)
        else:
            dut = DUT()
        model = Model(dut.ui, dut.mon)
        model.mem[0] = 0
        return dut, model, real_fifo

    def test_wrap4_write_is_one_command(self):
        for registered in (False, True):
            with self.subTest(registered=registered):
                dut, model, real = self.make(registered=registered)
                beats = [(0x2018, 0x1111111111111111), (0x2000, 0x2222222222222222),
                         (0x2008, 0x3333333333333333), (0x2010, 0x4444444444444444)]
                def process():
                    responses = yield from write_burst(dut.bus, beats, burst=2)
                    self.assertEqual(responses, [0, 0, 0, 0])
                    self.assertEqual(len(model.events), 1, model.events)
                    event = model.events[0]
                    self.assertEqual(event['cmd'], 0)
                    self.assertEqual(event['addr'], 0)
                    self.assertEqual(event['mask'], 0)
                    # WRAP order is not lane order: 0x2000, 0x2008, 0x2010, 0x2018.
                    self.assertEqual(event['data'],
                                     0x1111111111111111 << 192 | 0x4444444444444444 << 128
                                     | 0x3333333333333333 << 64 | 0x2222222222222222)
                    readback = yield from read_burst(dut.bus, [0x2000, 0x2008, 0x2010, 0x2018], burst=3)
                    self.assertEqual([value for _, value in readback],
                                     [0x2222222222222222, 0x3333333333333333,
                                      0x4444444444444444, 0x1111111111111111])
                    self.assertEqual(len(model.events), 2)
                run_dut(dut, process, model, real)

    def test_incr8_32bit_write_is_one_command(self):
        dut, model, real = self.make()
        beats = [(0x2000 + 4 * i, (0x1000 + i) << (32 if i & 1 else 0)) for i in range(8)]
        def process():
            responses = yield from write_burst(dut.bus, beats, size=2, burst=5)
            self.assertEqual(responses, [0] * 8)
            self.assertEqual(len(model.events), 1)
            self.assertEqual(model.events[0]['mask'], 0)
            for i in range(8):
                shift = 32 * i
                self.assertEqual((model.events[0]['data'] >> shift) & 0xffffffff, 0x1000 + i)
        run_dut(dut, process, model, real)

    def test_early_end_preserves_neighbours(self):
        dut, model, real = self.make()
        model.mem[0] = (1 << 256) - 1
        def process():
            responses = yield from write_burst(
                dut.bus, [(0x2000, 0xaaaaaaaaaaaaaaaa), (0x2008, 0xbbbbbbbbbbbbbbbb)], burst=3)
            self.assertEqual(responses, [0, 0])
            self.assertEqual(len(model.events), 1)
            self.assertEqual(model.events[0]['mask'], 0xffff0000)
            self.assertEqual(model.mem[0] & 0xffffffffffffffff, 0xaaaaaaaaaaaaaaaa)
            self.assertEqual((model.mem[0] >> 64) & 0xffffffffffffffff, 0xbbbbbbbbbbbbbbbb)
            self.assertEqual(model.mem[0] >> 128, (1 << 128) - 1)
        run_dut(dut, process, model, real)

    def test_cross_block_write_splits(self):
        dut, model, real = self.make()
        def process():
            responses = yield from write_burst(
                dut.bus, [(0x2018, 0x11), (0x2020, 0x22), (0x2028, 0x33), (0x2030, 0x44)])
            self.assertEqual(responses, [0, 0, 0, 0])
            self.assertEqual(len(model.events), 2, model.events)
            self.assertEqual([event['addr'] for event in model.events], [0, 8])
            self.assertEqual(model.events[0]['mask'], ~0xff000000 & 0xffffffff)
            self.assertEqual(model.events[1]['mask'], 0xff000000)
        run_dut(dut, process, model, real)

    def test_independent_nonseq_writes_do_not_merge(self):
        dut, model, real = self.make()
        def process():
            first, _ = yield from ahb(dut.bus, True, 0x2000, 0x1111, size=3)
            second, _ = yield from ahb(dut.bus, True, 0x2008, 0x2222, size=3)
            self.assertEqual((first, second), (0, 0))
            self.assertEqual(len(model.events), 2)
            self.assertEqual([(~event['mask']) & 0xffffffff for event in model.events],
                             [0x000000ff, 0x0000ff00])
        run_dut(dut, process, model, real)

    def test_busy_does_not_issue_until_the_line_closes(self):
        dut, model, real = self.make()
        def process():
            bus = dut.bus
            yield bus.addr.eq(0x2000)
            yield bus.size.eq(3)
            yield bus.burst.eq(3)
            yield bus.prot.eq(0xf)
            yield bus.write.eq(1)
            yield bus.wdata.eq(0xabcdef)
            yield bus.trans.eq(2)
            yield
            yield bus.trans.eq(1)
            yield
            for _ in range(300):
                if (yield bus.readyout):
                    self.assertEqual((yield bus.resp), 0)
                    yield
                    break
                yield
            else:
                self.fail('beat held by BUSY did not complete')
            for _ in range(4):
                yield
            self.assertEqual(model.events, [])
            self.assertEqual((yield dut.ddr.wbuf_valid), 1)
            yield bus.addr.eq(0x2008)
            yield bus.wdata.eq(0x1234)
            yield bus.trans.eq(3)
            yield
            yield bus.trans.eq(0)
            yield
            for _ in range(300):
                if (yield bus.readyout):
                    self.assertEqual((yield bus.resp), 0)
                    yield
                    break
                yield
            else:
                self.fail('closing beat did not complete')
            self.assertEqual(len(model.events), 1)
            self.assertEqual((~model.events[0]['mask']) & 0xffffffff, 0x0000ffff)
            self.assertEqual(model.events[0]['data'] & 0xffffffffffffffff, 0xabcdef)
            self.assertEqual((model.events[0]['data'] >> 64) & 0xffffffffffffffff, 0x1234)
        run_dut(dut, process, model, real)

    def test_idle_after_busy_flushes_the_open_line(self):
        dut, model, real = self.make()
        def process():
            bus = dut.bus
            yield bus.addr.eq(0x2000)
            yield bus.size.eq(3)
            yield bus.burst.eq(3)
            yield bus.prot.eq(0xf)
            yield bus.write.eq(1)
            yield bus.wdata.eq(0x55)
            yield bus.trans.eq(2)
            yield
            yield bus.trans.eq(1)
            yield
            for _ in range(300):
                if (yield bus.readyout):
                    self.assertEqual((yield bus.resp), 0)
                    yield
                    break
                yield
            else:
                self.fail('beat held by BUSY did not complete')
            self.assertEqual(model.events, [])
            yield bus.trans.eq(0)
            for _ in range(300):
                yield
                if len(model.events) == 1 and (yield bus.readyout):
                    break
            else:
                self.fail('open line was not flushed after IDLE')
            self.assertEqual(model.events[0]['cmd'], 0)
            self.assertEqual((~model.events[0]['mask']) & 0xffffffff, 0xff)
            self.assertEqual(model.events[0]['data'] & 0xffffffffffffffff, 0x55)
            self.assertEqual((yield dut.ddr.wbuf_valid), 0)
        run_dut(dut, process, model, real)

    def test_intermediate_beats_complete_while_the_ui_is_stalled(self):
        dut, model, real = self.make()
        def process():
            model.stall = True
            bus = dut.bus
            addresses = [0x2000, 0x2008, 0x2010, 0x2018]
            yield bus.write.eq(1)
            yield bus.size.eq(3)
            yield bus.burst.eq(3)
            yield bus.prot.eq(0xf)
            yield bus.addr.eq(addresses[0])
            yield bus.wdata.eq(1)
            yield bus.trans.eq(2)
            yield
            completed = []
            for index, address in enumerate(addresses):
                if index + 1 < len(addresses):
                    yield bus.addr.eq(addresses[index + 1])
                    yield bus.trans.eq(3)
                    yield bus.wdata.eq(index + 1)
                else:
                    yield bus.trans.eq(0)
                yield
                limit = 12 if index == len(addresses) - 1 else 40
                for cycle in range(limit):
                    if (yield bus.readyout):
                        completed.append((index, (yield bus.resp), cycle))
                        yield
                        break
                    yield
                else:
                    completed.append((index, None, None))
            self.assertEqual([item[0] for item in completed if item[1] == 0], [0, 1, 2])
            self.assertIsNone(completed[3][1])
            self.assertEqual(model.events, [])
            model.stall = False
            for _ in range(80):
                if (yield bus.readyout):
                    self.assertEqual((yield bus.resp), 0)
                    yield
                    break
                yield
            else:
                self.fail('closing beat did not complete after the stall')
            self.assertEqual(len(model.events), 1)
            self.assertEqual(model.events[0]['mask'], 0)
        run_dut(dut, process, model, real)

    def test_closing_beat_timeout_drops_the_line(self):
        dut, model, real = self.make()
        def process():
            model.stall = True
            responses = yield from write_burst(
                dut.bus, [(0x2000, 0x1111), (0x2008, 0x2222), (0x2010, 0x3333), (0x2018, 0x4444)])
            self.assertEqual(responses, [0, 0, 0, 1])
            self.assertEqual(model.events, [])
            self.assertEqual(model.mem.get(0, 0), 0)
            model.stall = False
            for _ in range(20):
                yield
            response, _ = yield from ahb(dut.bus, True, 0x2000, 0x5555, size=3)
            self.assertEqual(response, 0)
            self.assertEqual(len(model.events), 1)
            self.assertEqual(model.events[0]['data'] & 0xffffffffffffffff, 0x5555)
            self.assertEqual((~model.events[0]['mask']) & 0xffffffff, 0xff)
        run_dut(dut, process, model, real)

    def test_read_after_write_observes_committed_data(self):
        dut, model, real = self.make()
        def process():
            yield from write_burst(
                dut.bus, [(0x2000, 0x1111), (0x2008, 0x2222), (0x2010, 0x3333), (0x2018, 0x4444)])
            error, value = yield from ahb(dut.bus, False, 0x2010, size=3)
            self.assertEqual((error, value), (0, 0x3333))
            self.assertEqual([event['cmd'] for event in model.events], [0, 1])
        run_dut(dut, process, model, real)

    def test_calibration_loss_does_not_issue(self):
        dut, model, real = self.make()
        def process():
            yield dut.ready.eq(0)
            response, _ = yield from ahb(dut.bus, True, 0x2000, 0x1111, size=3)
            self.assertEqual(response, 1)
            self.assertEqual(model.events, [])
            yield dut.ready.eq(1)
            response, _ = yield from ahb(dut.bus, True, 0x2000, 0x2222, size=3)
            self.assertEqual(response, 0)
            self.assertEqual(model.events[-1]['data'] & 0xffffffffffffffff, 0x2222)
        run_dut(dut, process, model, real)

    def open_early_line(self, dut):
        """INCR write one beat, then BUSY, and wait until that beat is early-acked."""
        bus = dut.bus
        yield bus.addr.eq(0x2000)
        yield bus.size.eq(3)
        yield bus.burst.eq(1)
        yield bus.prot.eq(15)
        yield bus.write.eq(1)
        yield bus.wdata.eq(0xabcdef)
        yield bus.trans.eq(2)
        yield
        yield bus.addr.eq(0x2008)
        yield bus.trans.eq(1)
        yield
        for _ in range(100):
            if (yield bus.readyout):
                yield
                break
            yield
        else:
            self.fail('first write never completed')
        for _ in range(5):
            yield
        self.assertEqual((yield dut.ddr.wbuf_valid), 1)

    def assert_owned(self, dut, accepted_commands):
        accepted = (yield dut.ic.n_accepted)
        handled = (yield dut.ddr.n_req)
        self.assertEqual(handled, accepted, 'accepted %s handled %s' % (accepted, handled))
        self.assertEqual(accepted, accepted_commands)

    def test_busy_then_read_keeps_the_accepted_request(self):
        dut, model, real = self.make(registered=True)
        def process():
            yield from self.open_early_line(dut)
            self.assertEqual(model.events, [])
            response, value = yield from ahb(dut.bus, False, 0x2000, size=3, timeout=400)
            for _ in range(20):
                yield
            self.assertEqual((response, value), (0, 0xabcdef))
            self.assertEqual([event['cmd'] for event in model.events], [0, 1])
            self.assertEqual(model.events[0]['data'] & 0xffffffffffffffff, 0xabcdef)
            yield from self.assert_owned(dut, 2)
        run_dut(dut, process, model, real)

    def test_busy_then_independent_write_is_not_merged_or_lost(self):
        dut, model, real = self.make(registered=True)
        def process():
            yield from self.open_early_line(dut)
            response, _ = yield from ahb(dut.bus, True, 0x2008, 0x1111, size=3, timeout=400)
            self.assertEqual(response, 0)
            for _ in range(20):
                yield
            self.assertEqual([event['cmd'] for event in model.events], [0, 0])
            self.assertEqual(model.events[0]['data'] & 0xffffffffffffffff, 0xabcdef)
            self.assertEqual((model.events[1]['data'] >> 64) & 0xffffffffffffffff, 0x1111)
            self.assertEqual((~model.events[0]['mask']) & 0xffffffff, 0xff)
            self.assertEqual((~model.events[1]['mask']) & 0xffffffff, 0xff00)
            error, value = yield from ahb(dut.bus, False, 0x2000, size=3)
            self.assertEqual((error, value), (0, 0xabcdef))
            error, value = yield from ahb(dut.bus, False, 0x2008, size=3)
            self.assertEqual((error, value), (0, 0x1111))
            yield from self.assert_owned(dut, 4)
        run_dut(dut, process, model, real)

    def test_busy_then_other_block_write_is_a_second_command(self):
        dut, model, real = self.make(registered=True)
        def process():
            yield from self.open_early_line(dut)
            response, _ = yield from ahb(dut.bus, True, 0x2020, 0x2222, size=3, timeout=400)
            self.assertEqual(response, 0)
            for _ in range(20):
                yield
            self.assertEqual([event['cmd'] for event in model.events], [0, 0])
            self.assertEqual([event['addr'] for event in model.events], [0, 8])
            self.assertEqual(model.events[0]['data'] & 0xffffffffffffffff, 0xabcdef)
            self.assertEqual(model.events[1]['data'] & 0xffffffffffffffff, 0x2222)
            error, value = yield from ahb(dut.bus, False, 0x2020, size=3)
            self.assertEqual((error, value), (0, 0x2222))
            yield from self.assert_owned(dut, 3)
        run_dut(dut, process, model, real)

    def test_busy_then_read_reports_flush_timeout(self):
        dut, model, real = self.make(registered=True, response_timeout=64, ui_timeout=32)
        def process():
            yield from self.open_early_line(dut)
            model.stall = True
            response, value = yield from ahb(dut.bus, False, 0x2000, size=3, timeout=400)
            self.assertEqual((response, value), (1, 0))
            self.assertEqual(model.events, [])
            self.assertEqual(model.mem.get(0, 0), 0)
            yield from self.assert_owned(dut, 2)
            model.stall = False
            for _ in range(30):
                yield
            response, _ = yield from ahb(dut.bus, True, 0x2000, 0x5555, size=3)
            self.assertEqual(response, 0)
            self.assertEqual(model.events[-1]['data'] & 0xffffffffffffffff, 0x5555)
            yield from self.assert_owned(dut, 3)
        run_dut(dut, process, model, real)

    def test_busy_then_read_reports_calibration_loss(self):
        dut, model, real = self.make(registered=True)
        def process():
            yield from self.open_early_line(dut)
            yield dut.ready.eq(0)
            response, value = yield from ahb(dut.bus, False, 0x2000, size=3, timeout=400)
            self.assertEqual((response, value), (1, 0))
            self.assertEqual(model.events, [])
            yield from self.assert_owned(dut, 2)
            yield dut.ready.eq(1)
            for _ in range(20):
                yield
            response, _ = yield from ahb(dut.bus, True, 0x2000, 0x5555, size=3)
            self.assertEqual(response, 0)
            self.assertEqual(len(model.events), 1)
            self.assertEqual(model.events[0]['data'] & 0xffffffffffffffff, 0x5555)
            yield from self.assert_owned(dut, 3)
        run_dut(dut, process, model, real)

    def test_real_fifo_wrap4_write(self):
        dut, model, real = self.make(real_fifo=True)
        beats = [(0x2000, 0x10), (0x2008, 0x20), (0x2010, 0x30), (0x2018, 0x40)]
        def process():
            responses = yield from write_burst(dut.bus, beats)
            self.assertEqual(responses, [0, 0, 0, 0])
            self.assertEqual(len(model.events), 1)
            self.assertEqual(model.events[0]['mask'], 0)
            error, value = yield from ahb(dut.bus, False, 0x2018, size=3)
            self.assertEqual((error, value), (0, 0x40))
        run_dut(dut, process, model, real)


def _state_name(ddr):
    return ddr.fsm.decoding[(yield ddr.fsm.state)]


def _prime(model, addresses):
    for address in addresses:
        block = (address - 0x2000) >> 2 & ~7
        model.mem[block] = sum((0xabc00000 + block + i) << (32 * i) for i in range(8))


def _want(address, data_width):
    if data_width == 64:
        word = ((address - 0x2000) & ~7) >> 2
        return 0xabc00000 + word | ((0xabc00001 + word) << 32)
    return 0xabc00000 + (((address - 0x2000) & 31) >> 2)


def _block(address):
    return (address - 0x2000) >> 5


class ReadLineTests(unittest.TestCase):
    """The 32-byte line lives in the memory port and dies at the burst boundary."""

    def make(self, data_width=64, real_fifo=False, registered=False, response_timeout=64):
        if registered:
            dut = RegisteredDUT(response_timeout=response_timeout, ui_timeout=32)
        elif real_fifo:
            from migen.genlib.fifo import AsyncFIFO
            dut = DUT(fifo_cls=AsyncFIFO, data_width=data_width, response_timeout=response_timeout,
                      ui_timeout=32)
        else:
            dut = DUT(data_width=data_width, response_timeout=response_timeout, ui_timeout=32)
        return dut, Model(dut.ui, dut.mon), real_fifo or registered

    def monitored(self, dut, addresses, size, burst, busy=0):
        """Drive one read burst and record req.we plus the adapter state."""
        bus, ddr = dut.bus, dut.ddr
        width = len(bus.rdata)
        result, trace = [], []

        def snap():
            trace.append(((yield from _state_name(ddr)), (yield ddr.req.we),
                          (yield ddr.req.writable), (yield bus.readyout),
                          (yield ddr.rline_valid), (yield ddr.rline_block)))

        def step():
            yield
            yield from snap()

        yield bus.write.eq(0)
        yield bus.size.eq(size)
        yield bus.burst.eq(burst)
        yield bus.prot.eq(3)
        yield bus.addr.eq(addresses[0])
        yield bus.trans.eq(2)
        yield from step()
        for index, address in enumerate(addresses):
            nxt = addresses[index + 1] if index + 1 < len(addresses) else None
            # BUSY occupies the data phase so the next SEQ is not accepted
            # on this beat's completion edge. The gap below then resumes it.
            if busy and nxt is not None:
                yield bus.trans.eq(1)
            elif nxt is not None:
                yield bus.addr.eq(nxt)
                yield bus.trans.eq(3)
            else:
                yield bus.trans.eq(0)
            yield from step()
            for _ in range(400):
                if (yield bus.readyout):
                    result.append(((yield bus.resp), (yield bus.rdata)))
                    yield from step()
                    break
                yield from step()
            else:
                self.fail('burst did not complete at %08x' % address)
            if busy and nxt is not None:
                for _ in range(busy):
                    yield bus.trans.eq(1)
                    yield from step()
                    self.assertEqual(trace[-1][1], 0, 'req.we during BUSY')
                    self.assertEqual(trace[-1][4], 1, 'BUSY dropped the open read line')
                yield bus.addr.eq(nxt)
                yield bus.trans.eq(3)
                yield from step()
        yield bus.trans.eq(0)
        yield from step()
        yield from step()
        return result, trace, width

    def assert_line(self, dut, model, addresses, size, burst, lines, busy=0):
        _prime(model, addresses)
        width = 64 if len(dut.bus.rdata) == 64 else 32

        def process():
            before = (yield dut.ddr.n_req)
            result, trace, width_seen = yield from self.monitored(
                dut, addresses, size, burst, busy)
            self.assertEqual(width_seen, width)
            for address, (error, value) in zip(addresses, result):
                self.assertEqual((error, value), (0, _want(address, width)), hex(address))
            pushes = sum(we and writable for _state, we, writable, _ready, _valid, _block in trace)
            we_cycles = sum(we for _state, we, _writable, _ready, _valid, _block in trace)
            self.assertEqual(pushes, lines, trace)
            self.assertEqual(we_cycles, lines, trace)
            self.assertEqual(len(model.events), lines)
            self.assertEqual((yield dut.ddr.n_rline_hit), len(addresses) - lines)
            self.assertEqual((yield dut.ddr.n_req) - before, len(addresses))
            self.assertEqual((yield dut.ddr.rec_r_n), lines)
            self.assertGreater((yield dut.ddr.n_wait_cycles), 0)
            runs = []
            for state, *_rest in trace:
                if not runs or runs[-1] != state:
                    runs.append(state)
            self.assertEqual(runs.count('CAPTURE'), lines, runs)
            self.assertEqual(runs.count('WAIT'), lines, runs)
            self.assertEqual((yield dut.ddr.rline_valid), 0)
            # The block published for a hit beat is the block of that beat.
            hits = [block for state, _we, _wr, _ready, valid, block in trace
                    if state == 'DATA' and valid]
            if len(addresses) > lines:
                self.assertTrue(hits)
                self.assertIn(_block(addresses[-1]), hits)

        run_dut(dut, process, model, False)

    def test_incr4_and_wrap4_issue_one_read(self):
        cases = (
            ('incr4', [0x2000, 0x2008, 0x2010, 0x2018], 3, 3, 64),
            ('wrap4', [0x2018, 0x2000, 0x2008, 0x2010], 3, 2, 64),
            ('incr8-32', list(range(0x2000, 0x2020, 4)), 2, 5, 32),
            ('wrap8-32', [0x201c, 0x2000, 0x2004, 0x2008, 0x200c, 0x2010, 0x2014, 0x2018], 2, 4, 32),
        )
        for name, addresses, size, burst, width in cases:
            with self.subTest(name=name):
                dut, model, _real = self.make(data_width=width)
                self.assert_line(dut, model, addresses, size, burst, 1)

    def test_cross_block_updates_the_line(self):
        addresses = [0x2018, 0x2020, 0x2028, 0x2030]
        dut, model, _real = self.make()
        self.assert_line(dut, model, addresses, 3, 3, 2)

    def test_real_fifo_and_registered_decoder(self):
        addresses = [0x2000, 0x2008, 0x2010, 0x2018]
        for registered in (False, True):
            with self.subTest(registered=registered):
                dut, model, real = self.make(real_fifo=not registered, registered=registered,
                                             response_timeout=200_000)
                _prime(model, addresses)

                def process(dut=dut, model=model):
                    result, trace, _width = yield from self.monitored(dut, addresses, 3, 3)
                    for address, (error, value) in zip(addresses, result):
                        self.assertEqual((error, value), (0, _want(address, 64)))
                    pushes = sum(we and writable for _s, we, writable, *_rest in trace)
                    self.assertEqual(pushes, 1, trace)
                    self.assertEqual(len(model.events), 1)
                    self.assertEqual((yield dut.ddr.n_rline_hit), 3)
                    later = []
                    seen_wait = False
                    for state, we, *_rest in trace:
                        if state == 'WAIT':
                            seen_wait = True
                        elif seen_wait and state == 'DATA':
                            later.append(we)
                    self.assertTrue(later)
                    self.assertFalse(any(later))
                    self.assertEqual((yield dut.ddr.rline_valid), 0)

                run_dut(dut, process, model, real)

    def test_busy_keeps_the_line(self):
        for gaps in (1, 7):
            with self.subTest(gaps=gaps):
                dut, model, _real = self.make()
                addresses = [0x2000, 0x2008, 0x2010, 0x2018]
                self.assert_line(dut, model, addresses, 3, 3, 1, busy=gaps)

    def test_idle_or_new_nonseq_refetches(self):
        for kind in ('idle', 'nonseq'):
            with self.subTest(kind=kind):
                dut, model, _real = self.make()
                _prime(model, [0x2000])

                def process(dut=dut, model=model, kind=kind):
                    bus = dut.bus
                    yield from self.monitored(dut, [0x2000, 0x2008], 3, 3)
                    self.assertEqual(len(model.events), 1)
                    self.assertEqual((yield dut.ddr.rline_valid), 0)
                    model.mem[0] = 0x1122334455667788
                    if kind == 'nonseq':
                        # A second independent read of the same word, after the
                        # burst has already ended. It must not reuse the line.
                        pass
                    error, value = yield from ahb(bus, False, 0x2000, size=3)
                    self.assertEqual((error, value), (0, 0x1122334455667788))
                    self.assertEqual(len(model.events), 2)
                    for _ in range(4):
                        yield
                    self.assertEqual((yield dut.ddr.rline_valid), 0)

                run_dut(dut, process, model, False)

    def test_nonseq_in_place_of_seq_refetches(self):
        dut, model, _real = self.make()
        _prime(model, [0x2000])

        def process():
            bus = dut.bus
            yield bus.write.eq(0)
            yield bus.size.eq(3)
            yield bus.burst.eq(3)
            yield bus.prot.eq(3)
            yield bus.addr.eq(0x2000)
            yield bus.trans.eq(2)
            yield
            yield bus.addr.eq(0x2000)
            yield bus.trans.eq(2)  # same address, but a new transaction
            yield
            for _ in range(400):
                if (yield bus.readyout):
                    self.assertEqual((yield bus.rdata), _want(0x2000, 64))
                    yield
                    break
                yield
            else:
                self.fail('first beat timed out')
            model.mem[0] = 0x99aabbccddeeff00
            yield bus.trans.eq(0)
            yield
            for _ in range(400):
                if (yield bus.readyout):
                    self.assertEqual((yield bus.rdata), 0x99aabbccddeeff00)
                    yield
                    break
                yield
            else:
                self.fail('second nonseq timed out')
            self.assertEqual(len(model.events), 2)
            self.assertEqual((yield dut.ddr.n_rline_hit), 0)

        run_dut(dut, process, model, False)

    def test_write_then_read_sees_the_new_bytes(self):
        dut, model, _real = self.make(response_timeout=200_000)
        _prime(model, [0x2000])

        def process():
            yield from self.monitored(dut, [0x2000, 0x2008, 0x2010, 0x2018], 3, 3)
            self.assertEqual((yield dut.ddr.rline_valid), 0)
            responses = yield from write_burst(
                dut.bus, [(0x2000, 0x1111), (0x2008, 0x2222), (0x2010, 0x3333), (0x2018, 0x4444)])
            self.assertEqual(responses, [0, 0, 0, 0])
            self.assertEqual((yield dut.ddr.rline_valid), 0)
            result, _trace, _width = yield from self.monitored(
                dut, [0x2000, 0x2008, 0x2010, 0x2018], 3, 3)
            self.assertEqual([value for _error, value in result], [0x1111, 0x2222, 0x3333, 0x4444])
            # One read for the first burst, one write, one read for the second.
            self.assertEqual([event['cmd'] for event in model.events], [1, 0, 1])
            yield from ahb(dut.bus, True, 0x2003, 0xa5000000, size=0)
            error, value = yield from ahb(dut.bus, False, 0x2000, size=3)
            self.assertEqual(error, 0)
            self.assertEqual(value & 0xffffffff, 0xa5001111)
            self.assertEqual(len([event for event in model.events if event['cmd'] == 1]), 3)

        run_dut(dut, process, model, False)

    def test_drain_clears_the_line(self):
        dut, model, _real = self.make()
        model.mem[0] = 0x1122334455667788

        def process():
            model.stall = True
            error, _value = yield from ahb(dut.bus, False, 0x2000, size=3)
            self.assertEqual(error, 1)
            self.assertEqual((yield dut.ddr.rline_valid), 0)
            self.assertEqual((yield dut.ddr.recovering) | (yield dut.ddr.recovery_failed), 1)
            model.stall = False
            for _ in range(80):
                yield
            self.assertEqual((yield dut.ddr.rline_valid), 0)
            error, value = yield from ahb(dut.bus, False, 0x2000, size=3)
            self.assertEqual((error, value), (0, 0x1122334455667788))

        run_dut(dut, process, model, False)

    def test_calibration_loss_drops_the_open_line(self):
        dut, model, _real = self.make(response_timeout=200_000)
        _prime(model, [0x2000])

        def process():
            bus = dut.bus
            yield bus.write.eq(0)
            yield bus.size.eq(3)
            yield bus.burst.eq(3)
            yield bus.prot.eq(3)
            yield bus.addr.eq(0x2000)
            yield bus.trans.eq(2)
            yield
            yield bus.trans.eq(1)
            yield
            for _ in range(400):
                if (yield bus.readyout):
                    yield
                    break
                yield
            else:
                self.fail('priming beat timed out')
            for _ in range(4):
                yield
            self.assertEqual((yield dut.ddr.rline_valid), 1)
            self.assertEqual(len(model.events), 1)
            yield dut.ready.eq(0)
            yield
            yield
            self.assertEqual((yield dut.ddr.rline_valid), 0)
            yield dut.ready.eq(1)
            model.mem[0] = 0x0102030405060708 << 64
            yield bus.addr.eq(0x2008)
            yield bus.trans.eq(3)
            yield
            yield bus.trans.eq(0)
            yield
            for _ in range(400):
                if (yield bus.readyout):
                    self.assertEqual((yield bus.rdata), 0x0102030405060708)
                    yield
                    break
                yield
            else:
                self.fail('beat after calibration loss timed out')
            self.assertEqual(len(model.events), 2)
            self.assertEqual((yield dut.ddr.n_rline_hit), 0)

        run_dut(dut, process, model, False)


if __name__ == '__main__':
    unittest.main()
