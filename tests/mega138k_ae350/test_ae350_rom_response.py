"""Regression for stale ROM responses seen through the live AE350 CPU JTAG."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Signal
from migen.sim import run_simulation
from litex.soc.interconnect.ahb import AHBInterface
from gateware.machines.mega138k_ae350.cpu import AE350ROM


def main():
    bus = AHBInterface(data_width=32, address_width=32)
    status = Signal(32, reset=0x203)
    words = [0x30401073, 0x00800293, 0x3002b073, 0x80000197]
    dut = AE350ROM(bus, 8192, init=words, status=status)
    errors = []

    def tb():
        for _ in range(3):
            yield
        for off, expected in [(0, words[0]), (4, words[1]), (0, words[0]),
                              (12, words[3]), (8, words[2]), (1, words[0]),
                              (0x1ffc, 0x203), (4, words[1])]:
            yield bus.addr.eq(0x80000000 + off)
            yield bus.trans.eq(2)
            yield
            yield
            for _ in range(16):
                if (yield bus.readyout):
                    break
                yield
            else:
                raise AssertionError('ROM response timeout')
            actual = yield bus.rdata
            if actual != expected:
                errors.append((hex(off), hex(expected), hex(actual)))
            yield bus.trans.eq(0)
            for _ in range(4):
                yield
    run_simulation(dut, tb())
    assert not errors, errors
    print('PASS ROM nonsequential words, byte offsets and status response')


if __name__ == '__main__':
    main()
