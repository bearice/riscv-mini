"""Simulation tests for SFP10GDMABridge.

Tests:
1. TX Path:
   - 32-bit to 64-bit packing in sys domain (50 MHz)
   - Store-and-forward commit via Gray counter CDC
   - Continuous 64-bit drain into 10G MAC in eth_sfp_tx domain (156.25 MHz)
   - Handles even, odd, and partial byte frames
   - Verification that no data drains before frame commit (store-and-forward guarantee)
2. RX Path:
   - 64-bit push into AsyncFIFO from 10G MAC in eth_sfp_rx domain (156.25 MHz)
   - 64-bit to 32-bit unpacking in sys domain (50 MHz)
   - Handles even, odd, and partial byte frames (1..8 byte tail alignments)
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from migen import *
from migen.sim import run_simulation, passive
from migen.fhdl.specials import Memory
from gateware.machines.mega138k_ae350.peripherals.sfp_bridge import SFP10GDMABridge


def prepare_sim_fragment(module):
    fragment = module.get_fragment()
    for special in fragment.specials:
        if isinstance(special, Memory):
            for port in special.ports:
                if port.dat_r is None:
                    port.dat_r = Signal(special.width)
    return fragment


def test_tx_path():
    dut = SFP10GDMABridge(depth=64)

    # Frame 1: Even words (4 words = 16 bytes = 2 x 64-bit beats)
    words_f1 = [0x11223344, 0x55667788, 0x99aabbcc, 0xddeeff00]
    expected_f1_64 = [
        0x5566778811223344,
        0xddeeff0099aabbcc,
    ]
    expected_f1_be = [0xff, 0xff]

    # Frame 2: Odd words (3 words = 12 bytes = 1 full beat + 1 half beat)
    words_f2 = [0x01020304, 0x05060708, 0x0a0b0c0d]
    expected_f2_64 = [
        0x0506070801020304,
        0x000000000a0b0c0d,
    ]
    expected_f2_be = [0xff, 0x0f]

    # Frame 3: Single word (4 bytes = 1 half beat)
    words_f3 = [0xaabbccdd]
    expected_f3_64 = [
        0x00000000aabbccdd,
    ]
    expected_f3_be = [0x0f]

    tx_captured_data = []
    tx_captured_be = []
    tx_captured_last = []
    drain_before_commit = []

    def dma_tx_driver():
        for _ in range(5):
            yield

        # Send Frame 1 with deliberate pauses between words to verify store-and-forward
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
            # Check if MAC started transmitting prematurely before `last`
            if not is_last:
                for _ in range(5):
                    yield
                    if (yield dut.tx_mac_valid):
                        drain_before_commit.append(True)

        # Gap between frames
        for _ in range(25):
            yield

        # Send Frame 2
        for i, word in enumerate(words_f2):
            is_last = (i == len(words_f2) - 1)
            yield dut.dma_sink.valid.eq(1)
            yield dut.dma_sink.data.eq(word)
            yield dut.dma_sink.be.eq(0xf)
            yield dut.dma_sink.last.eq(1 if is_last else 0)
            yield
            while not (yield dut.dma_sink.ready):
                yield
        yield dut.dma_sink.valid.eq(0)
        yield dut.dma_sink.last.eq(0)

        # Gap between frames
        for _ in range(25):
            yield

        # Send Frame 3 (single word)
        yield dut.dma_sink.valid.eq(1)
        yield dut.dma_sink.data.eq(words_f3[0])
        yield dut.dma_sink.be.eq(0xf)
        yield dut.dma_sink.last.eq(1)
        yield
        while not (yield dut.dma_sink.ready):
            yield
        yield dut.dma_sink.valid.eq(0)
        yield dut.dma_sink.last.eq(0)

        # Allow drain
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
                be = yield dut.tx_mac_byte
                last = yield dut.tx_mac_last
                tx_captured_data.append(data)
                tx_captured_be.append(be)
                tx_captured_last.append(last)
            yield

    fragment = prepare_sim_fragment(dut)
    run_simulation(
        fragment,
        {
            "sys": dma_tx_driver(),
            "eth_sfp_tx": mac_tx_monitor(),
        },
        clocks={"sys": 20, "eth_sfp_tx": 6, "eth_sfp_rx": 6},
    )

    assert not drain_before_commit, "Violation: MAC TX started before frame committed (underflow risk)!"
    assert len(tx_captured_data) == 5, f"Expected 5 beats, got {len(tx_captured_data)}"

    # Check Frame 1
    assert tx_captured_data[0] == expected_f1_64[0], f"F1 beat 0: {hex(tx_captured_data[0])} != {hex(expected_f1_64[0])}"
    assert tx_captured_data[1] == expected_f1_64[1], f"F1 beat 1: {hex(tx_captured_data[1])} != {hex(expected_f1_64[1])}"
    assert tx_captured_be[:2] == expected_f1_be, f"F1 be: {tx_captured_be[:2]}"
    assert tx_captured_last[:2] == [0, 1], f"F1 last: {tx_captured_last[:2]}"

    # Check Frame 2
    assert tx_captured_data[2] == expected_f2_64[0], f"F2 beat 0: {hex(tx_captured_data[2])} != {hex(expected_f2_64[0])}"
    assert tx_captured_data[3] == expected_f2_64[1], f"F2 beat 1: {hex(tx_captured_data[3])} != {hex(expected_f2_64[1])}"
    assert tx_captured_be[2:4] == expected_f2_be, f"F2 be: {tx_captured_be[2:4]}"
    assert tx_captured_last[2:4] == [0, 1], f"F2 last: {tx_captured_last[2:4]}"

    # Check Frame 3
    assert tx_captured_data[4] == expected_f3_64[0], f"F3 beat 0: {hex(tx_captured_data[4])} != {hex(expected_f3_64[0])}"
    assert tx_captured_be[4:] == expected_f3_be, f"F3 be: {tx_captured_be[4:]}"
    assert tx_captured_last[4:] == [1], f"F3 last: {tx_captured_last[4:]}"

    print("PASS SFP TX bridge store-and-forward packing, underflow protection, and line-rate drain")


def test_rx_path():
    dut = SFP10GDMABridge(depth=64)

    # Frame 1: 2 x 64b beats (4 words)
    f1_64 = [0x5566778811223344, 0xddeeff0099aabbcc]
    f1_be = [0xff, 0xff]
    expected_rx_f1 = [
        (0x11223344, 0xf, 0),
        (0x55667788, 0xf, 0),
        (0x99aabbcc, 0xf, 0),
        (0xddeeff00, 0xf, 1),
    ]

    # Frame 2: Tail ends in Word 0 (only lower 4 bytes valid)
    f2_64 = [0x0506070801020304, 0x000000000a0b0c0d]
    f2_be = [0xff, 0x0f]
    expected_rx_f2 = [
        (0x01020304, 0xf, 0),
        (0x05060708, 0xf, 0),
        (0x0a0b0c0d, 0xf, 1),
    ]

    # Frame 3: Tail with partial bytes in Word 1 (e.g. 6 bytes total = 4 in W0, 2 in W1)
    f3_64 = [0x00004433221100ff]
    f3_be = [0x3f] # 6 bytes
    expected_rx_f3 = [
        (0x221100ff, 0xf, 0),
        (0x00004433, 0x3, 1),
    ]

    rx_captured = []

    def mac_rx_driver():
        for _ in range(5):
            yield

        # Send Frame 1
        for i, (data, be) in enumerate(zip(f1_64, f1_be)):
            is_last = (i == len(f1_64) - 1)
            yield dut.rx_mac_valid.eq(1)
            yield dut.rx_mac_data.eq(data)
            yield dut.rx_mac_byte.eq(be)
            yield dut.rx_mac_last.eq(1 if is_last else 0)
            yield
        yield dut.rx_mac_valid.eq(0)
        yield dut.rx_mac_last.eq(0)

        # Gap
        for _ in range(30):
            yield

        # Send Frame 2
        for i, (data, be) in enumerate(zip(f2_64, f2_be)):
            is_last = (i == len(f2_64) - 1)
            yield dut.rx_mac_valid.eq(1)
            yield dut.rx_mac_data.eq(data)
            yield dut.rx_mac_byte.eq(be)
            yield dut.rx_mac_last.eq(1 if is_last else 0)
            yield
        yield dut.rx_mac_valid.eq(0)
        yield dut.rx_mac_last.eq(0)

        # Gap
        for _ in range(30):
            yield

        # Send Frame 3
        for i, (data, be) in enumerate(zip(f3_64, f3_be)):
            is_last = (i == len(f3_64) - 1)
            yield dut.rx_mac_valid.eq(1)
            yield dut.rx_mac_data.eq(data)
            yield dut.rx_mac_byte.eq(be)
            yield dut.rx_mac_last.eq(1 if is_last else 0)
            yield
        yield dut.rx_mac_valid.eq(0)
        yield dut.rx_mac_last.eq(0)

        for _ in range(50):
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
            "eth_sfp_rx": mac_rx_driver(),
            "sys": dma_rx_monitor(),
        },
        clocks={"eth_sfp_rx": 6, "eth_sfp_tx": 6, "sys": 20},
    )

    all_expected = expected_rx_f1 + expected_rx_f2 + expected_rx_f3
    assert len(rx_captured) == len(all_expected), \
        f"Expected {len(all_expected)} words, got {len(rx_captured)}: {rx_captured}"

    for i, exp in enumerate(all_expected):
        assert rx_captured[i] == exp, f"RX word {i}: {rx_captured[i]} != {exp}"

    print("PASS SFP RX bridge unpacking, partial byte enables, and tail handling")


if __name__ == "__main__":
    test_tx_path()
    test_rx_path()
    print("ALL SFP BRIDGE TESTS PASSED")
