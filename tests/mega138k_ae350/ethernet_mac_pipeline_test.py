"""Exercise real MAC CDC/buffers with stalled input and byte-stream loopback."""
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Module, Signal, ResetInserter
from migen.fhdl.specials import Memory
from migen.sim import run_simulation, passive
from litex.soc.interconnect import stream
from liteeth.common import eth_phy_description
from liteeth.mac.core import LiteEthMACCore
from gateware.machines.mega138k_ae350.peripherals.ethernet_rgmii import add_mac_timing_buffers


class PHY(Module):
    dw = 8
    tx_clk_freq = 125e6
    rx_clk_freq = 125e6

    def __init__(self):
        self.sink = stream.Endpoint(eth_phy_description(8))
        self.source = stream.Endpoint(eth_phy_description(8))


dut = Module()
dut.submodules.phy = phy = PHY()
dut.submodules.mac = mac = ResetInserter(['sys','eth_tx','eth_rx'])(LiteEthMACCore(phy, 32, with_sys_datapath=True, with_store_and_forward=True, eth_mtu=128, tx_cdc_depth=8))
add_mac_timing_buffers(mac)
dut.comb += phy.sink.connect(phy.source, omit={'ready'})
frames = [bytes((i * 37 + n) & 255 for i in range(n)) for n in (1, 59, 60, 61, 98, 127)]
expected_frames=frames+[bytes(byte^0x5a for byte in frame) for frame in frames]
received = []
looped = []
faults = []


def send():
    for run in range(2):
        for frame in expected_frames[run*len(frames):(run+1)*len(frames)]:
            for i in range(0,len(frame),4):
                word=frame[i:i+4]
                if i % 7 == 0:
                    yield mac.sink.valid.eq(0)
                    yield
                    yield
                yield mac.sink.valid.eq(1)
                yield mac.sink.data.eq(int.from_bytes(word,'little'))
                yield mac.sink.be.eq((1<<len(word))-1)
                yield mac.sink.last.eq(i+4 >= len(frame))
                yield
                while not (yield mac.sink.ready):yield
            yield mac.sink.valid.eq(0)
            yield
        for _ in range(10000):
            if len(received)==len(frames)*(run+1) and len(looped)==len(frames)*(run+1):break
            yield
        else:raise AssertionError('MAC did not drain every frame')
        if run==0:
            yield mac.reset_sys.eq(1);yield mac.reset_eth_tx.eq(1);yield mac.reset_eth_rx.eq(1)
            for _ in range(6):yield
            yield mac.reset_sys.eq(0);yield mac.reset_eth_tx.eq(0);yield mac.reset_eth_rx.eq(0)
            for _ in range(6):yield


@passive
def receive():
    yield phy.sink.ready.eq(1)
    frame = bytearray()
    while True:
        if (yield phy.sink.valid):
            frame.append((yield phy.sink.data))
            if (yield phy.sink.error):
                faults.append('PHY error')
            if (yield phy.sink.last):
                received.append(bytes(frame))
                frame.clear()
        elif frame:
            faults.append('intra-frame gap')
        yield


@passive
def readback():
    yield mac.source.ready.eq(1)
    frame = bytearray()
    while True:
        if (yield mac.source.valid) and (yield mac.source.ready):
            word=(yield mac.source.data).to_bytes(4,'little');be=(yield mac.source.be)
            frame.extend(word[i] for i in range(4) if be&(1<<i))
            if (yield mac.source.error):
                faults.append('MAC RX error')
            if (yield mac.source.last):
                looped.append(bytes(frame))
                frame.clear()
        yield


fragment = dut.get_fragment()
for memory in fragment.specials:
    if isinstance(memory, Memory):
        for port in memory.ports:
            if port.dat_r is None:
                port.dat_r = Signal(memory.width)
run_simulation(fragment, {'sys': [send(), readback()], 'eth_tx': receive()},
               clocks={'sys': 20, 'eth_tx': 8, 'eth_rx': 8})
assert not faults, faults
for original, actual in zip(expected_frames, received):
    padded = original.ljust(60, b'\0')
    expected = b'\x55' * 7 + b'\xd5' + padded + zlib.crc32(padded).to_bytes(4, 'little')
    assert actual == expected, (len(original), actual.hex(), expected.hex())
assert looped == [frame.ljust(60, b'\0') for frame in expected_frames], [frame.hex() for frame in looped]
print('PASS MAC CDC: mixed-size frames, input stalls, padding/FCS, no wire underrun, RX loopback/reinitialization')
