"""PHY-edge transactions with falling input samples and delayed FPGA outputs.

Quantized behavioral corner probe, not a replacement for STA/electrical tests.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module, Record, Signal
from migen.sim import run_simulation, passive
from gateware.usb import USBPHYInit

def check(offset,output_delay,input_delay):
    dut=Module();pads=Record([('dir',1),('nxt',1),('stp',1)])
    dut.submodules.link=link=USBPHYInit(pads)
    direction=Signal();nxt=Signal();data=Signal(8)
    delayed_dir=Signal();delayed_nxt=Signal();delayed_data=Signal(8)
    output=Signal(8);stp=Signal(reset=1)
    dut.sync.fall += [pads.dir.eq(delayed_dir),pads.nxt.eq(delayed_nxt),link.di.eq(delayed_data)]
    @passive
    def pad_delay():
        pending=[];incoming=[];previous=None;previous_input=None;time=0
        while True:
            value=((yield link.do),(yield pads.stp))
            if value!=previous:pending.append((time+output_delay,value));previous=value
            while pending and pending[0][0]<=time:
                _,value=pending.pop(0);yield output.eq(value[0]);yield stp.eq(value[1])
            value=((yield direction),(yield nxt),(yield data))
            if value!=previous_input:incoming.append((time+input_delay,value));previous_input=value
            while incoming and incoming[0][0]<=time:
                _,value=incoming.pop(0)
                yield delayed_dir.eq(value[0]);yield delayed_nxt.eq(value[1]);yield delayed_data.eq(value[2])
            time+=2;yield
    def phy():
        def wait_cmd(expected):
            for _ in range(200):
                if (yield output)==expected:return
                yield
            raise AssertionError(('No PHY command',expected,offset,output_delay))
        for address,value in enumerate((0x24,4,6,0)):
            yield from wait_cmd(0xc0|address)
            yield;yield nxt.eq(1);yield
            assert (yield output)==0xc0|address,'Read CMD changed before PHY accepts it'
            yield nxt.eq(0);yield direction.eq(1);yield data.eq(0xee);yield
            yield data.eq(value);yield
            yield direction.eq(0);yield data.eq(0)
            for _ in range(4):yield
        assert (yield link.phy_id)==0x00060424
        for address,value in ((4,0x45),(10,0x26),(7,9)):
            yield from wait_cmd(0x80|address)
            yield;yield nxt.eq(1);yield
            assert (yield output)==0x80|address,'Write CMD changed before PHY accepts it'
            yield
            assert (yield output)==value,('Write DATA missed the next PHY edge',address,(yield output))
            yield
            assert (yield stp) and (yield output)==0,'Write STP/Idle missed termination edge'
            yield nxt.eq(0)
            for _ in range(4):yield
        for _ in range(1100):yield
        assert (yield link.ready) and not (yield link.error)
    # 0.1ns units: period 16.6ns. FPGA rising edge is offset from PHY.
    phase=(-offset)%166;fall_phase=(phase+83)%166
    run_simulation(dut,{'tick':pad_delay(),'phy':phy()},
        clocks={'tick':2,'phy':166,'ulpi':(166,phase),'fall':(166,fall_phase)})

if __name__=='__main__':
    check(7,70,78);check(-3,30,30)
    print('ULPI edge probe PASS: command/data/STP/read turnaround at both quantized corners')
