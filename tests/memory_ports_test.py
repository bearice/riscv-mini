"""Per-port stalls/cancellation and shared-cache visibility with real adapters."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Module
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.memory import MemoryPort, PortBuffer, SharedMemoryController


def backend(port, memory, traffic):
    @passive
    def serve():
        pending = None
        delay = 0
        cycle = 0
        while True:
            if (yield port.cmd.valid) and (yield port.cmd.ready):
                assert pending is None
                pending = ((yield port.cmd.addr), (yield port.cmd.we))
                traffic.append(pending)
                delay = 7
            if pending is not None:
                if delay:
                    delay -= 1
                elif pending[1]:
                    if (yield port.wdata.valid) and (yield port.wdata.ready):
                        address = pending[0] * 16
                        data, mask = (yield port.wdata.data), (yield port.wdata.we)
                        for i in range(16):
                            if mask >> i & 1: memory[address + i] = data >> (i * 8) & 255
                        pending = None
                elif (yield port.rdata.valid) and (yield port.rdata.ready):
                    pending = None
            yield port.cmd.ready.eq(pending is None and cycle % 3 != 0)
            yield port.wdata.ready.eq(pending is not None and pending[1] and delay == 0 and cycle % 4 != 0)
            returning = pending is not None and not pending[1] and delay == 0
            yield port.rdata.valid.eq(returning)
            if returning:
                address = pending[0] * 16
                yield port.rdata.data.eq(sum(memory.get(address + i, 0) << (8 * i) for i in range(16)))
            cycle += 1
            yield
    return serve()


def command(port, address, write=False, count=None):
    yield port.cmd.addr.eq(address)
    yield port.cmd.we.eq(write)
    yield port.cmd.count.eq(count or port.words_per_burst)
    yield port.cmd.valid.eq(1)
    yield
    for _ in range(2000):
        if (yield port.cmd.ready): break
        yield
    else: raise AssertionError('command stalled')
    yield port.cmd.valid.eq(0)
    yield


def send(port, value, mask=None):
    yield port.wdata.data.eq(value)
    yield port.wdata.we.eq((1 << (port.data_width // 8)) - 1 if mask is None else mask)
    yield port.wdata.valid.eq(1)
    yield
    for _ in range(2000):
        if (yield port.wdata.ready): break
        yield
    else: raise AssertionError('write stalled')
    yield port.wdata.valid.eq(0)
    yield


def receive(port):
    for _ in range(2000):
        if (yield port.rdata.valid): break
        yield
    else: raise AssertionError('read stalled')
    value, last = (yield port.rdata.data), (yield port.rdata.last)
    yield port.rdata.ready.eq(1)
    yield
    yield port.rdata.ready.eq(0)
    yield
    return value, last


def cpu(bus, address, value=None):
    yield bus.adr.eq(address >> 2)
    yield bus.dat_w.eq(value or 0)
    yield bus.sel.eq(15)
    yield bus.we.eq(value is not None)
    yield bus.cyc.eq(1)
    yield bus.stb.eq(1)
    yield
    for _ in range(3000):
        if (yield bus.ack): break
        yield
    else: raise AssertionError('CPU blocked by unrelated port')
    result = (yield bus.dat_r)
    yield bus.cyc.eq(0)
    yield bus.stb.eq(0)
    yield
    return result


def shared(eth=False):
    raw = LiteDRAMNativePort('both', 23, 128)
    wb = wishbone.Interface(data_width=32, address_width=32, addressing='word')
    dut = SharedMemoryController(wb, raw, eth=eth)
    memory = {i: (i * 19 + 7) & 255 for i in range(4096)}
    traffic = []
    def check():
        video, reader, writer = dut.video, dut.sd_read, dut.sd_write
        if eth: reader,writer=dut.eth_read,dut.eth_write
        for _ in range(300): yield  # L2 boot-tag initialization.
        yield from command(video, 0)
        for _ in range(100): yield
        assert (yield video.rdata.valid), (traffic, (yield dut.video_buffer.fsm.state), (yield dut.l2.fsm.state), (yield raw.cmd.valid), (yield raw.wdata.valid), (yield raw.rdata.valid))
        held = (yield video.rdata.data)
        assert (yield from cpu(wb, 0x40000100)) == int.from_bytes(bytes(memory[0x100+i] for i in range(4)), 'little')
        assert (yield video.rdata.data) == held, 'LCD response changed under stall'
        yield from command(reader, 1)
        for _ in range(100): yield
        assert (yield reader.rdata.valid), 'SD read blocked by LCD output stall'
        for i in range(4):
            value, last = yield from receive(reader)
            assert value == int.from_bytes(bytes(memory[16+4*i+j] for j in range(4)), 'little')
            assert last == (i == 3)
        for i in range(8):
            value, last = yield from receive(video)
            assert value == int.from_bytes(bytes(memory[2*i+j] for j in range(2)), 'little')
            assert last == (i == 7)
        # A partially collected SD write must not lock L2 or any other port.
        yield from command(writer, 32, True, 3)
        yield from send(writer, 0x11223344)
        if eth:
            yield from command(dut.sd_write, 40, True, 2)
            yield from send(dut.sd_write, 0x01020304)
        before = len(traffic)
        assert (yield from cpu(wb, 0x40000100)) != 0
        assert len(traffic) == before, 'partial SD write issued to DDR'
        yield from command(video, 2)
        for _ in range(100): yield
        assert (yield video.rdata.valid), 'partial SD write blocked LCD'
        yield video.cancel.eq(1)
        yield
        yield video.cancel.eq(0)
        # Flush waits for the pre-existing partial write, but lets it finish.
        yield dut.l2._flush.re.eq(1)
        yield
        yield dut.l2._flush.re.eq(0)
        for _ in range(10): yield
        assert (yield dut.l2._busy.status), 'flush skipped pending port write'
        yield from send(writer, 0x55667788, 5)
        yield from send(writer, 0x99aabbcc)
        for _ in range(2000):
            if (yield writer.done.valid): break
            yield
        else: raise AssertionError('write commit stalled during flush')
        assert bytes(memory[512+i] for i in range(4)) == bytes.fromhex('44332211')
        assert memory[516] == 0x88 and memory[518] == 0x66
        assert memory[517] == (517*19+7)&255 and memory[519] == (519*19+7)&255
        assert bytes(memory[520+i] for i in range(4)) == bytes.fromhex('ccbbaa99')
        assert memory[524] == (524*19+7)&255, 'tail write exceeded mask'
        yield writer.done.ready.eq(1)
        yield
        yield writer.done.ready.eq(0)
        if eth:
            for _ in range(10): yield
            assert (yield dut.l2._busy.status), 'flush skipped pending SD write after ETH commit'
            yield from send(dut.sd_write, 0x05060708)
            yield dut.sd_write.done.ready.eq(1)
            for _ in range(2000):
                if (yield dut.sd_write.done.valid): break
                yield
            else: raise AssertionError('SD/ETH shared arbiter stalled')
            yield
            yield dut.sd_write.done.ready.eq(0)
            assert bytes(memory[640+i] for i in range(8)) == bytes.fromhex('0403020108070605')
        for _ in range(10000):
            if not (yield dut.l2._busy.status): break
            yield
        else: raise AssertionError('flush failed to finish after commit')
        # Dirty CPU writes are visible to new streaming requests, not stale buffers.
        yield from cpu(wb, 0x40000300, 0xdeadbeef)
        yield from command(reader, 48, count=1)
        value, last = yield from receive(reader)
        assert value == 0xdeadbeef and last
        yield from command(writer, 48, True, 1)
        yield from send(writer, 0x12345678)
        yield writer.done.ready.eq(1)
        for _ in range(2000):
            if (yield writer.done.valid): break
            yield
        else: raise AssertionError('dirty-line merge stalled')
        yield
        yield writer.done.ready.eq(0)
        assert (yield from cpu(wb, 0x40000300)) == 0x12345678
    run_simulation(dut, [check(), backend(raw, memory, traffic)])


def cancelled(width, write):
    port = MemoryPort('write' if write else 'read', data_width=width)
    native = LiteDRAMNativePort('both', 23, 128)
    dut = PortBuffer(port, native)
    memory, traffic = {}, []
    def check():
        yield from command(port, 3, write)
        if write:
            # Cancel while collection is incomplete: there must be no DDR traffic.
            yield from send(port, 0x1234)
            yield port.cancel.eq(1)
            for _ in range(20): yield
            assert not traffic and not (yield dut.busy)
            yield port.cancel.eq(0)
            yield from command(port, 4, True)
            for i in range(port.words_per_burst): yield from send(port, i+1)
        for _ in range(200):
            if traffic: break
            yield
        else: raise AssertionError('no backend command')
        yield port.cancel.eq(1)
        for _ in range(100): yield
        assert not (yield dut.busy), 'cancel did not drain accepted backend work'
        assert not (yield port.rdata.valid) and not (yield port.done.valid)
        yield port.cancel.eq(0)
        yield from command(port, 5, write, 1)
        if write:
            yield from send(port, 0xabcd)
            yield port.done.ready.eq(1)
            for _ in range(200):
                if (yield port.done.valid): break
                yield
            else: raise AssertionError('restart write stalled')
        else:
            value, last = yield from receive(port)
            assert value == 0 and last
    run_simulation(dut, [check(), backend(native, memory, traffic)])


def cancelled_offer(write):
    port = MemoryPort('write' if write else 'read')
    native = LiteDRAMNativePort('both', 23, 128)
    dut = PortBuffer(port, native)
    def check():
        yield from command(port, 7, write, 1)
        if write: yield from send(port, 0x12345678)
        for _ in range(30):
            if (yield native.cmd.valid): break
            yield
        else: raise AssertionError('command not offered')
        yield port.cancel.eq(1)
        for _ in range(20):
            yield
            assert (yield native.cmd.valid), 'cancel withdrew an arbiter-owned offer'
            assert (yield native.cmd.addr) == 7
        yield native.cmd.ready.eq(1)
        yield
        yield native.cmd.ready.eq(0)
        yield
        if write:
            assert (yield native.wdata.valid)
            assert (yield native.wdata.data) & 0xffffffff == 0x12345678
            yield native.wdata.ready.eq(1)
        else:
            yield native.rdata.valid.eq(1)
        for _ in range(5): yield
        assert not (yield dut.busy)
        assert not (yield port.done.valid) and not (yield port.rdata.valid)
    run_simulation(dut, check())


if __name__ == '__main__':
    shared()
    shared(eth=True)
    for write in (False, True): cancelled_offer(write)
    for width in (16, 32):
        for write in (False, True): cancelled(width, write)
    print('Buffered memory ports PASS: independent stalls, masks, commit/flush, dirty visibility, cancel/restart')
