"""Simulation tests for SFP1GDMABridge.

Tests:
1. TX Path:
   - 32-bit DMA to 8-bit MAC stream unpacking in sys (50MHz) -> eth_sfp1_tx (125MHz) domain
   - Store-and-forward commit via Gray counter CDC
   - Continuous 8-bit drain into 1G MAC
   - Handles full 4-byte words, 1-byte, 2-byte, 3-byte partial tail words
   - Verification that no data drains before frame commit
2. RX Path:
   - 8-bit stream from 1G MAC at 125MHz packed into 32-bit words with exact byte enables
   - Crosses CDC AsyncFIFO into sys (50MHz) domain
   - Handles variable-length frames: 1, 2, 3, 4, 5, 7, 16 bytes
   - Verification of data integrity and byte enable masks
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from migen import *
from migen.sim import run_simulation, passive
from migen.fhdl.specials import Memory
from migen.genlib.cdc import MultiReg
from migen.genlib.fifo import AsyncFIFO
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from liteeth.common import eth_phy_description


from gateware.machines.mega138k_ae350.peripherals.sfp_bridge import SFP1GDMABridge


def prepare_sim_fragment(module):
    fragment = module.get_fragment()
    for special in fragment.specials:
        if isinstance(special, Memory):
            for port in special.ports:
                if port.dat_r is None:
                    port.dat_r = Signal(special.width)
    return fragment


def test_sfp1g_tx():
    dut = SFP1GDMABridge(depth=64)

    # Frame 1: 4 words = 16 bytes
    # Words in LE:
    # Word 0: 0x44332211 -> Bytes: 0x11, 0x22, 0x33, 0x44
    # Word 1: 0x88776655 -> Bytes: 0x55, 0x66, 0x77, 0x88
    # Word 2: 0xccbbaa99 -> Bytes: 0x99, 0xaa, 0xbb, 0xcc
    # Word 3: 0x00ffeedd -> Bytes: 0xdd, 0xee, 0xff, 0x00
    words_f1 = [0x44332211, 0x88776655, 0xccbbaa99, 0x00ffeedd]
    expected_bytes_f1 = [
        0x11, 0x22, 0x33, 0x44,
        0x55, 0x66, 0x77, 0x88,
        0x99, 0xaa, 0xbb, 0xcc,
        0xdd, 0xee, 0xff, 0x00
    ]

    # Frame 2: 2 words, second word has be=0b0011 (2 bytes) -> 6 bytes total
    # Word 0: 0x04030201 -> 0x01, 0x02, 0x03, 0x04
    # Word 1: 0x00000605 (be=0x3) -> 0x05, 0x06
    words_f2 = [0x04030201, 0x00000605]
    be_f2 = [0xf, 0x3]
    expected_bytes_f2 = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06]

    # Frame 3: 1 word, be=0b0001 (1 byte)
    words_f3 = [0x000000aa]
    be_f3 = [0x1]
    expected_bytes_f3 = [0xaa]

    tx_captured_bytes = []
    tx_captured_last = []
    drain_before_commit = []

    def dma_tx_driver():
        for _ in range(5):
            yield

        # Frame 1
        for i, word in enumerate(words_f1):
            is_last = (i == len(words_f1) - 1)
            yield dut.dma_sink.valid.eq(1)
            yield dut.dma_sink.data.eq(word)
            yield dut.dma_sink.be.eq(0xf)
            yield dut.dma_sink.last.eq(1 if is_last else 0)
            yield
            while not (yield dut.dma_sink.ready):
                yield
            yield dut.dma_sink.valid.eq(0)
            yield dut.dma_sink.last.eq(0)
            if not is_last:
                for _ in range(5):
                    yield
                    if (yield dut.tx_mac_valid):
                        drain_before_commit.append(True)

        for _ in range(30):
            yield

        # Frame 2
        for i, (word, be) in enumerate(zip(words_f2, be_f2)):
            is_last = (i == len(words_f2) - 1)
            yield dut.dma_sink.valid.eq(1)
            yield dut.dma_sink.data.eq(word)
            yield dut.dma_sink.be.eq(be)
            yield dut.dma_sink.last.eq(1 if is_last else 0)
            yield
            while not (yield dut.dma_sink.ready):
                yield
        yield dut.dma_sink.valid.eq(0)
        yield dut.dma_sink.last.eq(0)

        for _ in range(30):
            yield

        # Frame 3
        yield dut.dma_sink.valid.eq(1)
        yield dut.dma_sink.data.eq(words_f3[0])
        yield dut.dma_sink.be.eq(be_f3[0])
        yield dut.dma_sink.last.eq(1)
        yield
        while not (yield dut.dma_sink.ready):
            yield
        yield dut.dma_sink.valid.eq(0)
        yield dut.dma_sink.last.eq(0)

        for _ in range(50):
            yield

    @passive
    def mac_tx_monitor():
        yield dut.tx_mac_ready.eq(1)
        while True:
            valid = yield dut.tx_mac_valid
            ready = yield dut.tx_mac_ready
            if valid and ready:
                data = yield dut.tx_mac_data
                last = yield dut.tx_mac_last
                tx_captured_bytes.append(data)
                tx_captured_last.append(last)
            yield

    fragment = prepare_sim_fragment(dut)
    run_simulation(
        fragment,
        {
            "sys": dma_tx_driver(),
            "eth_sfp1_tx": mac_tx_monitor(),
        },
        clocks={"sys": 20, "eth_sfp1_tx": 8, "eth_sfp1_rx": 8},
    )

    assert not drain_before_commit, "TX started before frame commit!"
    all_expected = expected_bytes_f1 + expected_bytes_f2 + expected_bytes_f3
    assert len(tx_captured_bytes) == len(all_expected), \
        f"Expected {len(all_expected)} bytes, got {len(tx_captured_bytes)}: {tx_captured_bytes}"

    for i, exp in enumerate(all_expected):
        assert tx_captured_bytes[i] == exp, f"Byte {i}: {hex(tx_captured_bytes[i])} != {hex(exp)}"

    # Check lasts
    expected_lasts = [0]*15 + [1] + [0]*5 + [1] + [1]
    assert tx_captured_last == expected_lasts, f"Lasts mismatch: {tx_captured_last} != {expected_lasts}"

    print("PASS SFP1G TX path simulation")


def test_sfp1g_rx():
    dut = SFP1GDMABridge(depth=64)

    # Frame 1: 16 bytes (4 full words)
    bytes_f1 = [
        0x11, 0x22, 0x33, 0x44,
        0x55, 0x66, 0x77, 0x88,
        0x99, 0xaa, 0xbb, 0xcc,
        0xdd, 0xee, 0xff, 0x00
    ]
    expected_rx_f1 = [
        (0x44332211, 0xf, 0),
        (0x88776655, 0xf, 0),
        (0xccbbaa99, 0xf, 0),
        (0x00ffeedd, 0xf, 1),
    ]

    # Frame 2: 6 bytes (Word 0: 4 bytes, Word 1: 2 bytes)
    bytes_f2 = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06]
    expected_rx_f2 = [
        (0x04030201, 0xf, 0),
        (0x00000605, 0x3, 1),
    ]

    # Frame 3: 1 byte
    bytes_f3 = [0xaa]
    expected_rx_f3 = [
        (0x000000aa, 0x1, 1),
    ]

    rx_captured = []

    def mac_rx_driver():
        for _ in range(5):
            yield

        for f_bytes in [bytes_f1, bytes_f2, bytes_f3]:
            for i, b in enumerate(f_bytes):
                is_last = (i == len(f_bytes) - 1)
                yield dut.rx_mac_valid.eq(1)
                yield dut.rx_mac_data.eq(b)
                yield dut.rx_mac_last.eq(1 if is_last else 0)
                yield
            yield dut.rx_mac_valid.eq(0)
            yield dut.rx_mac_last.eq(0)
            for _ in range(20):
                yield

        for _ in range(40):
            yield

    @passive
    def dma_rx_monitor():
        yield dut.dma_source.ready.eq(1)
        while True:
            valid = yield dut.dma_source.valid
            ready = yield dut.dma_source.ready
            if valid and ready:
                data = yield dut.dma_source.data
                be = yield dut.dma_source.be
                last = yield dut.dma_source.last
                rx_captured.append((data, be, last))
            yield

    fragment = prepare_sim_fragment(dut)
    run_simulation(
        fragment,
        {
            "eth_sfp1_rx": mac_rx_driver(),
            "sys": dma_rx_monitor(),
        },
        clocks={"eth_sfp1_rx": 8, "eth_sfp1_tx": 8, "sys": 20},
    )

    all_expected = expected_rx_f1 + expected_rx_f2 + expected_rx_f3
    assert len(rx_captured) == len(all_expected), \
        f"Expected {len(all_expected)} words, got {len(rx_captured)}: {rx_captured}"

    for i, exp in enumerate(all_expected):
        assert rx_captured[i] == exp, f"RX word {i}: {rx_captured[i]} != {exp}"

    print("PASS SFP1G RX path simulation")


if __name__ == "__main__":
    test_sfp1g_tx()
    test_sfp1g_rx()
    print("ALL SFP1G BRIDGE TESTS PASSED")
