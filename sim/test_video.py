"""Check native LCD timing, pixel order and complete-frame DMA underrun recovery."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.sim import run_simulation
from gateware.video import LCDScan, WIDTH, HEIGHT

def check(starve=False):
    dut=LCDScan()
    counts={'active':0,'hs':0,'vs':0,'frames':0,'errors':0,'done':0}
    def feed():
        yield dut.enable.eq(1)
        index=0
        sending=False
        stalled=False
        for cycle in range(525*286*3):
            h=(yield dut.h);v=(yield dut.v)
            if h==0 and v==0:
                if cycle:
                    assert counts['active']==WIDTH*HEIGHT,counts
                    assert counts['hs']==41*286,counts
                    assert counts['vs']==10*525,counts
                    counts['frames']+=1
                    counts['active']=counts['hs']=counts['vs']=0
                sending=True;index=0
            pause=starve and not stalled and h==145 and v==34
            if pause: stalled=True
            yield dut.sink.valid.eq(sending and not pause)
            yield dut.sink.data.eq(index&65535)
            yield dut.sink.last.eq(index==WIDTH*HEIGHT-1)
            yield
            # Inputs are now settled; scanner advances to the next coordinate.
            ph=(yield dut.h);pv=(yield dut.v)
            de=(yield dut.de)
            counts['active']+=de
            counts['hs']+=not (yield dut.hsync)
            counts['vs']+=not (yield dut.vsync)
            if (yield dut.underflow): counts['errors']+=1
            if (yield dut.sink.valid) and (yield dut.sink.ready):
                if de and not starve:
                    expected=(pv-14)*WIDTH+(ph-45)
                    assert (yield dut.pixel)==expected&65535,(ph,pv,index,expected)
                if (yield dut.sink.last): counts['done']+=1;sending=False
                index+=1
        assert counts['done']>=2,counts
        assert counts['errors']==(1 if starve else 0),counts
    run_simulation(dut,feed())
    print('LCD scan PASS', 'underrun' if starve else 'normal',counts)

if __name__=='__main__':
    check();check(True)
