"""Decode actual DAC pins; exercise ring ownership, delayed replies and stop."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Signal
from migen.fhdl.specials import Memory
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from gateware.audio import Audio


def fixture(depth=8, half=2):
    pads=SimpleNamespace(**{n:Signal() for n in ('bck','ws','din','pa_en')})
    bus=wishbone.Interface(data_width=32,address_width=32,addressing='word',mode='r')
    return Audio(pads,bus,half_period=half,fifo_depth=depth),pads,bus

def simulate(dut,processes):
    fragment=dut.get_fragment()
    # Pinned Migen simulator needs an unused dat_r on write-only RAM ports.
    for memory in fragment.specials:
        if isinstance(memory,Memory):
            for port in memory.ports:
                if port.dat_r is None:port.dat_r=Signal(memory.width)
    run_simulation(fragment,processes)

def pio_and_serial():
    dut,pads,bus=fixture();words=[];periods=[];sequence=[0x80017fff,0x1234fedc,0xa55a5aa5]
    def bench():
        assert not (yield pads.pa_en)
        for word in sequence:
            yield dut._sample.wr_data.eq(word);yield dut._sample.wr_stb.eq(1);yield
            yield dut._sample.wr_stb.eq(0);yield
        yield dut._control.storage.eq(1)
        for _ in range(128*6):yield
        assert (yield dut._played.status)==3
        assert (yield dut._underruns.status)>0
        # PIO mute consumes samples but exposes only zero to the DAC and amp.
        yield dut._control.storage.eq(5)
        yield dut._sample.wr_data.eq(0xffffffff);yield dut._sample.wr_stb.eq(1);yield
        yield dut._sample.wr_stb.eq(0)
        for _ in range(256):
            yield
            assert not (yield pads.pa_en)
        assert (yield dut._last_sample.status)==0
        yield dut._control.storage.eq(0);yield
        yield
        assert not (yield pads.pa_en)
    @passive
    def decode():
        last=0;group=[];ws_prev=0;cycle=0;rises=[]
        while True:
            bck=(yield pads.bck);ws=(yield pads.ws);din=(yield pads.din)
            if bck and not last:
                rises.append(cycle)
                if len(rises)>1:periods.append(rises[-1]-rises[-2])
                if ws!=ws_prev:
                    if len(group)==16:words.append((ws_prev,int(''.join(map(str,group)),2)))
                    group=[];ws_prev=ws
                group.append(din)
            last=bck;cycle+=1;yield
    simulate(dut,[bench(),decode()])
    packed=[(right<<16)|left for (ws,right),(next_ws,left) in zip(words,words[1:]) if ws==0 and next_ws==1]
    assert sequence[1] in packed and sequence[2] in packed,packed
    assert packed.index(sequence[1])<packed.index(sequence[2])
    assert packed[-1]==0,packed
    assert periods and set(periods)=={4},set(periods)
    print('Audio pins PASS: right WS=0 / left WS=1, MSB first, stereo order, zeros on underrun/mute')

def dma_and_stop():
    dut,pads,bus=fixture();requests=[];idle_gaps=[]
    def bench():
        yield dut._base.storage.eq(0x40810000);yield dut._capacity.storage.eq(3)
        yield dut._producer.storage.eq(3);yield dut._control.storage.eq(2|4)
        for _ in range(80):yield
        assert (yield dut._fetched.status)==3
        assert (yield dut._wraps.status)==1
        assert requests==[0x40810000//4+i for i in range(3)],requests
        assert (yield dut.fifo.dout)==0x13572468,'PCM word byte order changed'
        # Commit a second ring lap only after the previous slots were fetched.
        yield dut._producer.storage.eq(6)
        for _ in range(80):yield
        assert (yield dut._fetched.status)==6
        assert (yield dut._wraps.status)==2
        yield dut._control.storage.eq(3|4)
        for _ in range(320):yield
        assert (yield dut._errors.status)==0
        # Stop during a delayed request; clear FIFO immediately, drain bus.
        yield dut._producer.storage.eq(7)
        for _ in range(100):
            yield
            if (yield bus.cyc):break
        else:raise AssertionError('No DMA transaction to stop')
        address=(yield bus.adr)
        yield dut._control.storage.eq(0);yield dut._clear.wr_stb.eq(1);yield
        yield dut._clear.wr_stb.eq(0)
        for _ in range(40):
            if (yield bus.cyc):assert (yield bus.adr)==address
            assert not (yield pads.pa_en)
            yield
        assert not (yield dut._busy.status)
        assert (yield dut._level.status)==0
        assert (yield dut._fetched.status)==0,'Stale response entered new generation'
        # A producer must never claim more than capacity free slots.
        yield dut._producer.storage.eq(4);yield dut._control.storage.eq(2)
        for _ in range(5):yield
        assert (yield dut._errors.status)==2
        yield dut._control.storage.eq(0);yield dut._clear.wr_stb.eq(1);yield
        yield dut._clear.wr_stb.eq(0);yield
        yield dut._base.storage.eq(0x47dffffc);yield dut._capacity.storage.eq(2)
        yield dut._producer.storage.eq(1);yield dut._control.storage.eq(2)
        for _ in range(5):yield
        assert (yield dut._errors.status)==1,'Framebuffer boundary accepted'
        yield dut._control.storage.eq(0);yield dut._clear.wr_stb.eq(1);yield
        yield dut._clear.wr_stb.eq(0);yield
        yield dut._base.storage.eq(0x40820000);yield dut._capacity.storage.eq(2)
        yield dut._producer.storage.eq(1);yield dut._control.storage.eq(2)
        for _ in range(50):yield
        assert (yield dut._errors.status)==4
        assert (yield dut._fetched.status)==0 and not (yield dut._busy.status)
    @passive
    def memory():
        gap=0
        while True:
            if not (yield bus.cyc):gap+=1;yield;continue
            address=(yield bus.adr);requests.append(address);idle_gaps.append(gap);gap=0
            for _ in range(10):
                assert (yield bus.cyc) and (yield bus.adr)==address
                yield
            if address==0x40820000//4:
                yield bus.err.eq(1);yield;yield bus.err.eq(0);yield
            else:
                yield bus.dat_r.eq(0x13572468+(address&3));yield bus.ack.eq(1);yield
                yield bus.ack.eq(0);yield
    simulate(dut,[bench(),memory()])
    assert all(gap>0 for gap in idle_gaps),idle_gaps
    print('Audio DMA PASS: word order, ring wrap/ownership, bounded bus release, stop drains stale reply, range/bus errors')

def real_clock():
    dut,pads,bus=fixture(half=20);rises=[];ws_rises=[]
    def bench():
        previous=ws_previous=0
        for cycle in range(5000):
            bck=(yield pads.bck);ws=(yield pads.ws)
            if bck and not previous:rises.append(cycle)
            if ws and not ws_previous:ws_rises.append(cycle)
            previous=bck;ws_previous=ws;yield
    simulate(dut,bench())
    assert len(rises)>100 and all(b-a==40 for a,b in zip(rises,rises[1:]))
    assert len(ws_rises)>2 and all(b-a==1280 for a,b in zip(ws_rises,ws_rises[1:]))
    print('Audio clock PASS: 60MHz / 40 = 1.5MHz BCK, / 1280 = 46875Hz stereo frames')

pio_and_serial();dma_and_stop();real_clock()
