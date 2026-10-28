"""Optional Core FIFO buffering preserves byte order, backpressure and CRC7 errors."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import *
from migen.sim import run_simulation
from migen.fhdl.specials import Memory
from litex.soc.interconnect import stream
from gateware.vendor.litesdcard.sdcore import SDCore
from gateware.machines.mega138k_ae350.peripherals.sd import bind_csr

def crc7(data):
 c=0
 for value in data:
  for bit in range(7,-1,-1):
   feedback=((c>>6)&1)^((value>>bit)&1)
   c=((c<<1)&127)^(9 if feedback else 0)
 return c*2+1
assert crc7([0x40,0,0,0,0])==0x95

def check(buffered,corrupt=False,elastic=False,stream_status=0,blocks=1):
 p=SimpleNamespace(cmdw=SimpleNamespace(sink=stream.Endpoint([('data',8),('cmd_type',2)])),
  cmdr=SimpleNamespace(sink=stream.Endpoint([('cmd_type',2),('data_type',2),('length',8)]),source=stream.Endpoint([('data',8),('status',3)])),
  dataw=SimpleNamespace(sink=stream.Endpoint([('data',8),('last_block',1)]),source=stream.Endpoint([('status',3)])),
  datar=SimpleNamespace(sink=stream.Endpoint([('block_length',10)]),source=stream.Endpoint([('data',8),('status',3),('drop',1)])))
 m=Module();receiver=p
 if elastic:
  m.submodules.receive_pipe=pipe=ResetInserter()(stream.Buffer(p.datar.source.description,pipe_valid=True,pipe_ready=True))
  m.comb += p.datar.source.connect(pipe.sink)
  receiver=SimpleNamespace(**vars(p))
  receiver.datar=SimpleNamespace(sink=p.datar.sink,source=pipe.source)
 m.submodules.core=core=SDCore(receiver,buffered_data=buffered)
 if elastic:m.comb += pipe.reset.eq(core.fsm.ongoing('IDLE'))
 off,data,go=Signal(12),Signal(32),Signal()
 for n,reg in enumerate((core.cmd_argument,core.cmd_command,core.cmd_send,core.cmd_event,core.data_event,core.block_length,core.block_count)):
  bind_csr(m,reg,n*4,go,off,data)
 def write(offset,value):
  yield [off.eq(offset),data.eq(value),go.eq(1)]
  yield
  yield go.eq(0)
  yield
 def send(endpoint,values):
  for n,value in enumerate(values):
   yield [endpoint.valid.eq(1),endpoint.data.eq(value),endpoint.last.eq(n==len(values)-1)]
   yield
   for _ in range(100):
    if (yield endpoint.ready):break
    yield
   else:raise AssertionError('PHY source stalled')
  yield endpoint.valid.eq(0)
  yield
 def bench():
  yield [p.cmdw.sink.ready.eq(1),p.datar.sink.ready.eq(1)]
  yield from write(20,32);yield from write(24,blocks)
  yield from write(4,(17<<8)|1|4|32);yield from write(8,1)
  for _ in range(100):
   yield
   if (yield p.cmdr.sink.valid):break
  else:raise AssertionError('no command response request')
  response=[17,0,0,9,0];response.append(crc7(response)^(2 if corrupt else 0))
  yield from send(p.cmdr.source,response)
  if corrupt:
   for _ in range(20):yield
   assert (yield core.cmd_event.status)&10==10
   return
  yield p.datar.source.status.eq(stream_status)
  if stream_status==1:
   yield from send(p.datar.source,[0])
   for _ in range(30):yield
   assert (yield core.data_event.status)&4
   assert not (yield core.source.valid)
   return
  values=[(i*7+3)&255 for i in range(32*blocks)]
  for block in range(blocks):yield from send(p.datar.source,values[block*32:(block+1)*32])
  for value in values:
   yield core.source.ready.eq(0)
   for _ in range(100):
    yield
    if (yield core.source.valid):break
   else:raise AssertionError('missing read byte')
   assert (yield core.source.data)==value
   for _ in range(3):
    yield
    assert (yield core.source.data)==value
   yield core.source.ready.eq(1)
   yield
   yield core.source.ready.eq(0)
   yield
  for _ in range(10):yield
  assert (yield core.cmd_event.status)&15==1
  assert (yield core.data_event.status)&15==(9 if stream_status==5 else 1)
 fragment=m.get_fragment()
 for memory in fragment.specials:
  if isinstance(memory,Memory):
   for port in memory.ports:
    if port.dat_r is None:port.dat_r=Signal(memory.width)
 run_simulation(fragment,bench())
for buffered in (False,True):
 for elastic in (False,True):
  check(buffered,elastic=elastic)
  check(buffered,True,elastic=elastic)
  check(buffered,elastic=elastic,stream_status=5)
  check(buffered,elastic=elastic,stream_status=1)
  check(buffered,elastic=elastic,blocks=2)
print('PASS SDCore buffered/default FIFO modes preserve read bytes, stalls and command CRC errors')
