"""Actual command PHY, registered pads and a card with delayed CMD output."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation, passive
from litex.build.io import SDROutput, SDRTristate
from litex.gen import LiteXModule
from litesdcard.phy import _sdpads_layout,SDPHYClocker,SDPHYCMDW,SDPHYCMDR
from gateware.machines.mega138k_ae350.peripherals.sd_phy import RegisteredSDPHYIO

def crc7(data):
    crc=0
    for value in data:
        for bit in range(7,-1,-1):
            feedback=((crc>>6)&1)^((value>>bit)&1)
            crc=((crc<<1)&127)^(9 if feedback else 0)
    return crc*2+1

def check(divider,delay):
    pads=Record([('clk',1),('cmd',1),('data',4),('cd',1)])
    dut=LiteXModule()
    layout=_sdpads_layout(4)
    wire=Record(layout)
    dut.clocker=SDPHYClocker()
    dut.cmdw=SDPHYCMDW(layout)
    dut.cmdr=SDPHYCMDR(layout,100000000,.01,dut.cmdw)
    dut.io=RegisteredSDPHYIO(dut.clocker,wire,pads)
    dut.comb += [wire.cmd.o.eq(dut.cmdw.pads_out.cmd.o | dut.cmdr.pads_out.cmd.o),
        wire.cmd.oe.eq(dut.cmdw.pads_out.cmd.oe | dut.cmdr.pads_out.cmd.oe),
        dut.clocker.clk_en.eq(dut.cmdw.pads_out.clk | dut.cmdr.pads_out.clk)]
    for engine in (dut.cmdw,dut.cmdr):
        dut.comb += [engine.pads_out.ready.eq(dut.clocker.ce),
            engine.pads_in.valid.eq(wire.data_i_ce),engine.pads_in.cmd.i.eq(wire.cmd.i),
            engine.pads_in.data.i.eq(wire.data.i)]
    card_cmd,card_data=Signal(reset=1),Signal(4,reset=15)
    ownership={}
    class Output:
        @staticmethod
        def lower(dr):
            m=Module();m.sync += dr.o.eq(dr.i);return m
    class Tristate:
        @staticmethod
        def lower(dr):
            m=Module();value=Signal.like(dr.o);enable=Signal.like(dr.oe)
            m.sync += [value.eq(dr.o),enable.eq(dr.oe)]
            source=card_cmd if len(dr.io)==1 else card_data
            if len(dr.io)==1:ownership['cmd']=enable
            m.comb += dr.io.eq(Mux(enable,value,source))
            if dr.i is not None:m.sync += dr.i.eq(dr.io)
            return m
    response=[13,0,0,9,0];response.append(crc7(response))
    bits=[(b>>i)&1 for b in response for i in range(7,-1,-1)]
    observed=[]
    @passive
    def card():
        previous=0;position=0;pending=None
        while True:
            clock=yield pads.clk
            host=yield ownership['cmd']
            if clock and not previous and host:observed.append((yield pads.cmd))
            if previous and not clock and not host and len(observed)>=48 and position<len(bits):
                pending=[delay,bits[position]];position+=1
            if pending is not None:
                if pending[0]==0:
                    yield card_cmd.eq(pending[1]);pending=None
                else:pending[0]-=1
            previous=clock
            yield
    def bench():
        # Bind only CSR field aggregation; this test concerns wire timing.
        dut.clocker.divider.storage.reset=divider
        yield dut.clocker.divider.storage.eq(divider)
        yield dut.cmdr.timeout.storage.eq(1000000)
        yield dut.cmdr.sink.cmd_type.eq(1)
        yield dut.cmdr.sink.length.eq(6)
        command=[0x40|13,0,0,0,0];command.append(crc7(command))
        for i,byte in enumerate(command):
            yield [dut.cmdw.sink.valid.eq(1),dut.cmdw.sink.data.eq(byte),
                   dut.cmdw.sink.last.eq(i==5),dut.cmdw.sink.cmd_type.eq(1)]
            yield
            for _ in range(10000):
                if (yield dut.cmdw.sink.ready):break
                yield
            else:raise AssertionError('command wire stalled')
            yield
        yield dut.cmdw.sink.valid.eq(0)
        yield dut.cmdr.sink.valid.eq(1)
        received=[]
        yield dut.cmdr.source.ready.eq(1)
        for _ in range(100000):
            yield
            if (yield dut.cmdr.source.valid):
                assert (yield dut.cmdr.source.status)==0
                received.append((yield dut.cmdr.source.data))
                if (yield dut.cmdr.source.last):break
        assert received==response,(divider,delay,received,response)
        assert observed[:48]==[(b>>i)&1 for b in command for i in range(7,-1,-1)]
    print('PHY timing case',divider,delay*2,flush=True)
    run_simulation(dut,{'sys':bench(),'card':card()},clocks={'sys':10,'card':2},
                   special_overrides={SDROutput:Output,SDRTristate:Tristate})

for divider in (4,10,250):
    for delay in (0,3,7):check(divider,delay)
print('PASS registered SD PHY command/response at 25/10/0.4MHz with 0/6/14ns card output delays')
