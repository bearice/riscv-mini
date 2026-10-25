"""Regression: PHY/Core CSR fields must be owned by the APB adapter."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import Module,Signal
from migen.sim import run_simulation
from litex.soc.interconnect.csr import CSRStorage,CSRStatus,CSRField
from gateware.machines.mega138k_ae350.peripherals.sd import bind_csr
m=Module();go,off,data=Signal(),Signal(12),Signal(32)
command=CSRStorage(fields=[CSRField('response',size=2),CSRField('index',offset=8,size=6)],name='command')
event=CSRStatus(fields=[CSRField('done'),CSRField('error')],name='event')
bind_csr(m,command,0x84,go,off,data);bind_csr(m,event,0x9c,go,off,data)
def bench():
    yield event.fields.done.eq(1)
    yield
    assert (yield event.status)==1
    yield [off.eq(0x84),data.eq((51<<8)|1),go.eq(1)]
    yield
    yield go.eq(0)
    yield
    assert (yield command.fields.response)==1
    assert (yield command.fields.index)==51
    yield event.fields.error.eq(1)
    yield
    assert (yield event.status)==3
run_simulation(m,bench())
print('PASS SD command field writes and event aggregation through owned LiteX CSR modules')
