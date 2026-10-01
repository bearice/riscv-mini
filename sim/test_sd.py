"""Real SDCore command CRC/error visibility and SD clock rates, with a PHY seam."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Signal, Module, ResetInserter
from migen.fhdl.specials import Memory
from migen.sim import run_simulation
from litex.soc.interconnect import stream
from litesdcard.phy import SDPHYClocker
from litesdcard.core import SDCore as UpstreamCore
from gateware.vendor.sdcore import SDCore

def phy_seam():
    return SimpleNamespace(
        cmdw=SimpleNamespace(sink=stream.Endpoint([('data',8),('cmd_type',2)])),
        cmdr=SimpleNamespace(sink=stream.Endpoint([('cmd_type',2),('data_type',2),('length',8)]),source=stream.Endpoint([('data',8),('status',3)])),
        datar=SimpleNamespace(sink=stream.Endpoint([('block_length',10)]),source=stream.Endpoint([('data',8),('status',3),('drop',1)])),
        dataw=SimpleNamespace(sink=stream.Endpoint([('data',8),('last_block',1)]),source=stream.Endpoint([('status',3)])))

def crc7(data):
    crc=0
    for byte in data:
        crc^=byte
        for _ in range(8):crc=((crc<<1)^(0x12 if crc&128 else 0))&255
    return crc|1

def core_check(core_cls):
    phy=phy_seam();core=core_cls(phy);events=[]
    core.cmd_event.finalize(32,"big");core.submodules.event_csr=core.cmd_event
    core.cmd_command.finalize(32,"big");core.submodules.command_csr=core.cmd_command
    def bench():
        yield phy.cmdw.sink.ready.eq(1)
        for _ in range(4):yield
        for bad in (False,True):
            yield core.cmd_argument.storage.eq(0x1aa)
            yield core.cmd_command.storage.eq((8<<8)|5)
            yield core.cmd_send.wr_stb.eq(1);yield
            yield core.cmd_send.wr_stb.eq(0)
            for _ in range(100):
                if (yield phy.cmdr.sink.valid):break
                yield
            else:raise AssertionError('Command transmission did not reach response phase')
            data=[8,0,0,1,0xaa];data.append(crc7(data)^(2 if bad else 0))
            yield phy.cmdr.source.status.eq(0)
            for i,byte in enumerate(data):
                yield phy.cmdr.source.data.eq(byte);yield phy.cmdr.source.last.eq(i==5)
                yield phy.cmdr.source.valid.eq(1);yield
            yield phy.cmdr.source.valid.eq(0)
            for _ in range(6):yield
            event=(yield core.cmd_event.status);events.append(event)
            assert event&1,'Command failed to complete'
            if not bad:assert event==1,('Good response rejected',event)
            else:assert event&8 and event&2,('Bad CRC not visible to software',event)
        # A PHY timeout must also become a completed/error event.
        yield core.cmd_send.wr_stb.eq(1);yield;yield core.cmd_send.wr_stb.eq(0)
        for _ in range(100):
            if (yield phy.cmdr.sink.valid):break
            yield
        yield phy.cmdr.source.status.eq(1);yield phy.cmdr.source.valid.eq(1);yield
        yield phy.cmdr.source.valid.eq(0)
        for _ in range(6):yield
        assert (yield core.cmd_event.status)&5==5
    fragment=core.get_fragment()
    # Pinned Migen MemoryToArray predates write-only ports. Supply an unused
    # read signal on those ports for simulation, without changing RTL logic.
    for memory in fragment.specials:
        if isinstance(memory,Memory):
            for port in memory.ports:
                if port.dat_r is None:port.dat_r=Signal(memory.width)
    run_simulation(fragment,bench())
    return events

def buffered_reads():
    """Actual core plus descriptor FIFO: response overlap, backpressure, abort."""
    phy=phy_seam();dut=Module()
    dut.submodules.requests=requests=ResetInserter()(stream.SyncFIFO([('block_length',10)],depth=2,buffered=True))
    proxy=SimpleNamespace(cmdw=phy.cmdw,cmdr=phy.cmdr,dataw=phy.dataw,
        datar=SimpleNamespace(sink=requests.sink,source=phy.datar.source))
    dut.submodules.core=core=SDCore(proxy)
    core.data_event.finalize(32,'big');dut.submodules.data_event_csr=core.data_event
    dut.comb += [requests.source.connect(phy.datar.sink),requests.reset.eq(core.fsm.ongoing('IDLE'))]
    for name in ('cmd_command','block_count','block_length'):
        csr=getattr(core,name);csr.finalize(32,'big');setattr(dut.submodules,name+'_csr',csr)
    observed=[]
    def command():
        yield core.cmd_send.wr_stb.eq(1);yield;yield core.cmd_send.wr_stb.eq(0)
        for _ in range(100):
            if (yield phy.cmdr.sink.valid):return
            yield
        raise AssertionError('Read command stalled')
    def bench():
        yield phy.cmdw.sink.ready.eq(1);yield core.source.ready.eq(1)
        yield core.cmd_command.storage.eq((18<<8)|1|(1<<5))
        yield core.block_count.storage.eq(3);yield core.block_length.storage.eq(512)
        for _ in range(5):yield
        # Fill pending read requests before the response, then cancel them.
        yield from command()
        for _ in range(15):yield
        assert (yield phy.datar.sink.valid)
        yield phy.cmdr.source.valid.eq(1);yield phy.cmdr.source.status.eq(1);yield
        yield phy.cmdr.source.valid.eq(0)
        for _ in range(8):yield
        assert not (yield phy.datar.sink.valid),'Aborted command left a read descriptor'
        yield phy.cmdr.source.status.eq(0)
        yield from command()
        for _ in range(10):yield
        # Read can already be armed while CMD response is still in flight.
        assert (yield phy.datar.sink.valid)
        for i in range(6):
            yield phy.cmdr.source.valid.eq(1);yield phy.cmdr.source.last.eq(i==5);yield
        yield phy.cmdr.source.valid.eq(0)
        for block in range(3):
            for _ in range(100):
                if (yield phy.datar.sink.valid):break
                yield
            else:raise AssertionError('Missing block descriptor')
            descriptor=((yield phy.datar.sink.block_length),(yield phy.datar.sink.last))
            observed.append(descriptor)
            for _ in range(13):
                assert (yield phy.datar.sink.valid)
                assert ((yield phy.datar.sink.block_length),(yield phy.datar.sink.last))==descriptor
                yield
            yield phy.datar.sink.ready.eq(1);yield;yield phy.datar.sink.ready.eq(0)
            yield phy.datar.source.valid.eq(1);yield phy.datar.source.last.eq(1)
            yield phy.datar.source.drop.eq(1);yield phy.datar.source.status.eq(0);yield
            yield phy.datar.source.valid.eq(0)
        for _ in range(20):yield
        assert not (yield phy.datar.sink.valid),'Core queued extra blocks after count exhausted'
        assert (yield core.data_event.status)&1,'Read command failed to complete'
    fragment=dut.get_fragment()
    for memory in fragment.specials:
        if isinstance(memory,Memory):
            for port in memory.ports:
                if port.dat_r is None:port.dat_r=Signal(memory.width)
    run_simulation(fragment,bench())
    assert observed==[(512,0),(512,0),(512,1)],observed

if '--upstream' in sys.argv:
    core_check(UpstreamCore)
else:
    assert core_check(SDCore)==[1,11]
    print('SDCore PASS: valid CRC accepted, corrupt CRC rejected/visible, timeout completes')
    buffered_reads()
    print('SD descriptors PASS: early arming, held payload, exactly three blocks, abort flush')
    for divider,period in ((150,150),(4,4),(8,8)):
        clocker=SDPHYClocker()
        def clock_bench():
            yield clocker.divider.storage.eq(divider)
            for _ in range(1024):yield
            previous=(yield clocker.clk);rises=[]
            for cycle in range(period*8):
                current=(yield clocker.clk)
                if current and not previous:rises.append(cycle)
                previous=current;yield
            assert len(rises)>=6 and all(b-a==period for a,b in zip(rises,rises[1:])),rises
        run_simulation(clocker,clock_bench())
    print('SD clocker PASS: 60 MHz / 150 = 400 kHz; / 4 = 15 MHz; / 8 = 7.5 MHz')
