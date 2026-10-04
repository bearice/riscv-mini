"""Compare registered L2 paths against a fixed-latency simulated memory.

These cycles exclude the CPU, arbiter, real DDR PHY and video contention.
They explain controller latency, not hardware benchmark throughput.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Module
from migen.sim import passive, run_simulation
from litex.soc.interconnect import wishbone
from gateware.bus import WishbonePipeline
from gateware.l2 import ReadL2


def measure(mode):
    cpu = wishbone.Interface(data_width=32, address_width=32, addressing='word')
    request = wishbone.Interface(data_width=32, address_width=32, addressing='word')
    memory = wishbone.Interface(data_width=128, address_width=32, addressing='word')
    dut = Module()
    bursting = mode in ('burst', 'burst-refill')
    dut.submodules.pipeline = WishbonePipeline(cpu, request, burst_read=bursting)
    dut.submodules.cache = ReadL2(request, memory, size=256,
                                  bursting=bursting, prefetch=mode == 'prefetch',
                                  refill_bypass=mode == 'burst-refill')
    ticks = [0]
    transactions = []
    results = {'mode': mode, 'backend_wait_cycles': 12}

    @passive
    def clock():
        while True:
            ticks[0] += 1
            yield

    @passive
    def backend():
        while True:
            yield
            if not ((yield memory.cyc) and (yield memory.stb)):
                continue
            address = (yield memory.adr)
            transactions.append(address)
            for _ in range(12):
                yield
            yield memory.dat_r.eq(sum((address * 4 + i + 1) << (32 * i) for i in range(4)))
            yield memory.ack.eq(1)
            yield
            yield memory.ack.eq(0)

    def read_line():
        yield cpu.cyc.eq(1)
        yield cpu.stb.eq(1)
        yield cpu.sel.eq(15)
        for lane in range(8):
            yield cpu.adr.eq(lane)
            yield cpu.cti.eq(7 if lane == 7 else 2)
            for _ in range(200):
                yield
                if (yield cpu.ack):
                    break
            else:
                raise AssertionError('read stalled')
            assert (yield cpu.dat_r) == lane + 1
        yield cpu.cyc.eq(0)
        yield cpu.stb.eq(0)
        yield

    def checks():
        for _ in range(30):
            yield
        for name in ('cold_32byte_refill', 'hot_32byte_refill'):
            start = ticks[0]
            before = len(transactions)
            yield from read_line()
            results[name] = {'cycles': ticks[0] - start,
                             'memory_reads': len(transactions) - before}
        assert results['cold_32byte_refill']['memory_reads'] == 2
        assert results['hot_32byte_refill']['memory_reads'] == 0

    run_simulation(dut, [clock(), backend(), checks()])
    return results


if __name__ == '__main__':
    print(json.dumps([measure(mode) for mode in ('baseline', 'prefetch', 'burst', 'burst-refill')], indent=2))
