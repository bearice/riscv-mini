"""Shared rational DDS: exact average sample rate, sys-quantized pin edges."""
from math import gcd
from migen import Signal, If
from litex.gen import LiteXModule


class AudioDDS(LiteXModule):
    def __init__(self, clock_hz, sample_rate=48000):
        clock_hz, sample_rate = int(clock_hz), int(sample_rate)
        edge_hz = sample_rate * 128
        if sample_rate <= 0 or clock_hz < 4 * edge_hz:
            raise ValueError('Audio DDS needs at least four sys cycles per microphone edge')
        divisor = gcd(clock_hz, edge_hz)
        self.step = edge_hz // divisor
        self.modulus = clock_hz // divisor
        phase = Signal(max=self.modulus)
        alternate = Signal()
        self.mic_tick = Signal()
        self.dac_tick = Signal()
        self.comb += [self.mic_tick.eq(phase >= self.modulus-self.step),
                      self.dac_tick.eq(self.mic_tick & alternate)]
        self.sync += If(self.mic_tick,
            phase.eq(phase-(self.modulus-self.step)), alternate.eq(~alternate)
        ).Else(phase.eq(phase+self.step))
