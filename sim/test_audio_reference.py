"""DDS edge counts, common phase and sample rate across system frequencies."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation
from gateware.audio_clock import AudioDDS

for clock_hz in (48000000,60000000,120000000):
    reference=AudioDDS(clock_hz); ticks=[[],[]]
    def bench():
        for cycle in range(4*clock_hz//48000):
            if (yield reference.dac_tick):ticks[0].append(cycle)
            if (yield reference.mic_tick):ticks[1].append(cycle)
            yield
    run_simulation(reference,bench())
    for events,expected,stride in zip(ticks,(256,512),(64,128)):
        assert len(events)==expected,(clock_hz,len(events))
        assert all(events[i+stride]-events[i]==clock_hz//48000 for i in range(len(events)-stride))
        ideal=clock_hz/(48000*stride)
        assert all(abs((b-a)-ideal)<1 for a,b in zip(events,events[1:]))
    assert ticks[0]==ticks[1][1::2], 'DAC and microphones lost their common phase'
    print(f'Audio DDS PASS: sys {clock_hz}, exact average 48kHz, common phase, edge error <1 sys cycle')
