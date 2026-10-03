"""Drive real I2S pads: one-bit delay, signed PCM24, channel and snapshot ownership."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Signal, If, ClockDomain
from migen.fhdl.specials import Memory
from migen.sim import run_simulation, passive
from gateware.microphone import Microphone
from gateware.audio_clock import AudioDDS


def check(right,stereo=False,enable_period=1,rate_sys=False):
    pads=SimpleNamespace(**{n:Signal() for n in ('data','bck','ws','lr')})
    second=SimpleNamespace(**{n:Signal() for n in ('data','bck','ws','lr')}) if stereo else None
    half=1 if rate_sys else 2 if enable_period>1 else 6
    period=2*half*enable_period
    enable=Signal(reset=1) if enable_period==1 else Signal()
    dut=Microphone(pads,half_period=half,snapshot_samples=8,second=second,clock_enable=enable)
    if rate_sys:
        dut.submodules.reference=reference=AudioDDS(60000000)
        dut.comb += enable.eq(reference.mic_tick)
    elif enable_period>1:
        count=Signal(max=enable_period)
        dut.sync += If(count==enable_period-1,count.eq(0)).Else(count.eq(count+1))
        dut.comb += enable.eq(count==enable_period-1)
    sequences=[[0x123456,0x800000,0x7fffff,0xffffff],[0x654321,0xfedcba,0x000001,0xabcdef]]
    periods=[];frames=[];cycles=[0];outputs=[]
    @passive
    def microphone():
        previous_bck=0;previous_ws=1;position=31;index=[0,0];word=0
        last_rise=None;last_frame=None
        while True:
            bck=(yield pads.bck);ws=(yield pads.ws)
            if second:
                assert (yield second.bck)==bck and (yield second.ws)==ws
            if not (yield dut.enabled):
                last_rise=None;last_frame=None
                previous_bck=bck;previous_ws=ws;cycles[0]+=1;yield
                continue
            if bck and not previous_bck:
                if last_rise is not None:periods.append(cycles[0]-last_rise)
                last_rise=cycles[0]
            if not bck and previous_bck:
                if ws!=previous_ws:
                    position=0
                    word=sequences[ws][index[ws]%4];index[ws]+=1
                    if ws==0:
                        if last_frame is not None:frames.append(cycles[0]-last_frame)
                        last_frame=cycles[0]
                else:position+=1
                # Deliberately set the delay and unused bits: including them
                # would corrupt positive/negative PCM and fail the assertions.
                value=((word>>(24-position))&1) if 1<=position<=24 else 1
                yield pads.data.eq(value)
                if second:yield second.data.eq(value if ws else 0)
            previous_bck=bck;previous_ws=ws;cycles[0]+=1;yield
    def pulse(csr):
        yield csr.wr_stb.eq(1);yield
        yield csr.wr_stb.eq(0);yield
    def capture():
        yield from pulse(dut._capture)
        for _ in range(64*period*10):
            if (yield dut._done.status):break
            yield
        else:raise AssertionError('Snapshot did not finish')
        assert (yield dut._captured.status)==8
        assert (yield dut._level.status)==8
        assert not (yield dut._overruns.status)
        words=[]
        for _ in range(8):
            words.append((yield dut._sample.status))
            if stereo:words[-1]=(words[-1],(yield dut._sample_right.status))
            yield from pulse(dut._pop)
            yield
        assert not (yield dut._level.status)
        signed=lambda w:w|0xff000000 if w&0x800000 else w
        expected=[(signed(a),signed(b)) for a,b in zip(*sequences)] if stereo else [signed(w) for w in sequences[right]]
        for offset in range(4):
            if words==[expected[(offset+i)%4] for i in range(8)]:break
        else:raise AssertionError((right,words,expected))
        outputs.extend(words)
    def bench():
        control=5 if stereo else 1|(right<<1)
        yield dut._control.storage.eq(control)
        for _ in range(64*period*2):yield
        assert (yield pads.lr)==(0 if stereo else right)
        if second:assert (yield second.lr)==1 and (yield dut._activity_right.status)
        assert (yield dut._activity.status)
        yield from capture()
        count=(yield dut._samples.status)
        # The FIFO stays empty after a completed window, while clocks keep running.
        for _ in range(64*period*2):yield
        assert not (yield dut._level.status)
        assert (yield dut._samples.status)>count
        yield from capture()
        yield dut._control.storage.eq(0)
        for _ in range(5):yield
        assert not (yield pads.bck) and not (yield pads.ws)
        assert not (yield dut._busy.status) and not (yield dut._level.status)
        assert not (yield dut._activity.status)
        # Re-enable and stop while a snapshot is pending: no stale sample survives.
        yield dut._control.storage.eq(control);yield
        yield from pulse(dut._capture)
        for _ in range(64*period*3):yield
        assert 0<(yield dut._captured.status)<8
        yield dut._control.storage.eq(0)
        for _ in range(5):yield
        assert not (yield dut._done.status) and not (yield dut._captured.status)
        assert not (yield dut._level.status)
        if second:
            assert not (yield second.bck) and not (yield second.ws)
            assert not (yield dut._activity_right.status)
    fragment=dut.get_fragment()
    for memory in fragment.specials:
        if isinstance(memory,Memory):
            for port in memory.ports:
                if port.dat_r is None:port.dat_r=Signal(memory.width)
    run_simulation(fragment,[bench(),microphone()],clocks={'sys':10})
    assert (set(periods)<={19,20} if rate_sys else set(periods)=={period}),set(periods)
    assert set(frames)=={1250 if rate_sys else 64*period},set(frames)
    assert len(outputs)==16


if __name__=='__main__':
    check(0);check(1);check(0,True);check(0,True,5);check(0,True,10,rate_sys=True)
    print('I2S microphone simulation PASS: mono/stereo signed24, independent paired channels, clocks, stop/drain')
