"""DMA ownership, DDR byte masks, ring wrap and delayed memory responses."""
import struct
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import Signal, Module
from migen.fhdl.specials import Memory
from migen.sim import run_simulation, passive
from gateware.machines.mega138k_ae350.peripherals.ethernet_dma import EthernetDMA
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI
from gateware.machines.mega138k_ae350.video_memory import DDRMemoryArbiter

BASE=0x100000
ram=bytearray(b'\xa5'*0x10000)
dut=EthernetDMA(ram_bytes=BASE+len(ram),min_address=BASE)
top=Module();top.submodules.dma=dut
ui=DDR3UI();cpu=DDR3UI();video=DDR3UI()
rename=__import__('migen').ClockDomainsRenamer
top.submodules.arbiter=arbiter=rename('ddr')(DDRMemoryArbiter(ui,cpu,video,dut.memory.ui))
top.comb += arbiter.network_pending.eq(dut.memory.pending)
sent=[];current=bytearray();writes=[];responses={'cpu':0,'video':0}
def put(addr,data):ram[addr-BASE:addr-BASE+len(data)]=data
def get(addr,n):return bytes(ram[addr-BASE:addr-BASE+n])
def desc(addr,buf,n):put(addr,struct.pack('<8I',buf,1536,n,0x80000000,0xabcdef01,0,0,0))

@passive
def memory():
    cycle=0;pending=None;previous=False
    while True:
        ready=cycle%5!=1;wready=cycle%7!=2
        yield ui.rd_data_valid.eq(0)
        if pending:
            delay,addr=pending
            if delay==0:
                yield ui.rd_data.eq(int.from_bytes(get(addr,32),'little'))
                yield ui.rd_data_valid.eq(1);pending=None
            else:pending=(delay-1,addr)
        if (yield ui.cmd_en) and (yield ui.cmd_ready):
            assert (yield ui.cmd) in (0,1)
            addr=(yield ui.addr)*4
            assert addr%32==0 and BASE<=addr<=BASE+len(ram)-32,hex(addr)
            if (yield ui.cmd)==0:
                assert (yield ui.wr_data_en) and (yield ui.wr_data_rdy)
                data=(yield ui.wr_data).to_bytes(32,'little');mask=(yield ui.wr_data_mask)
                old=bytearray(get(addr,32))
                for i in range(32):
                    if not (mask>>i)&1:old[i]=data[i]
                put(addr,old);writes.append((addr,mask))
            else:
                assert pending is None,'multiple outstanding untagged reads'
                pending=(3,addr)
        yield ui.cmd_ready.eq(ready);yield ui.wr_data_rdy.eq(wready)
        cycle+=1
        yield

@passive
def capture():
    cycle=0
    while True:
        if (yield dut.sink.valid) and (yield dut.sink.ready):
            word=(yield dut.sink.data).to_bytes(4,'little');be=(yield dut.sink.be)
            current.extend(word[i] for i in range(4) if be&(1<<i))
            if (yield dut.sink.last):sent.append(bytes(current));current.clear()
        yield dut.sink.ready.eq(cycle%5!=0)
        cycle+=1
        yield

@passive
def cpu_traffic():
    while True:
        yield cpu.addr.eq((BASE+0x800)//4);yield cpu.cmd.eq(1)
        yield cpu.cmd_en.eq(1);yield arbiter.cpu_pending.eq(1)
        yield
        while not (yield cpu.cmd_ready):yield
        yield cpu.cmd_en.eq(0);yield arbiter.cpu_pending.eq(0)
        yield
        while not (yield cpu.rd_data_valid):yield
        assert (yield cpu.rd_data)==int.from_bytes(b'\xa5'*32,'little'),'read owner mixed'
        responses['cpu']+=1
        for _ in range(20):yield


@passive
def video_traffic():
    while True:
        yield video.addr.eq((BASE+0xc00)//4);yield video.cmd.eq(1)
        yield video.cmd_en.eq(1);yield arbiter.video_pending.eq(1)
        yield
        while not (yield video.cmd_ready):yield
        yield video.cmd_en.eq(0);yield arbiter.video_pending.eq(0)
        yield
        while not (yield video.rd_data_valid):yield
        assert (yield video.rd_data)==int.from_bytes(b'\x3c'*32,'little'),'video read owner mixed'
        responses['video']+=1
        for _ in range(11):yield


def wr(off,value):
    yield dut.off.eq(off);yield dut.wdata.eq(value);yield dut.write.eq(1);yield dut.selected.eq(1)
    yield;yield
    assert (yield dut.ok),(hex(off),hex(value))
    yield dut.commit.eq(1);yield
    yield dut.commit.eq(0);yield dut.selected.eq(0);yield

def rd(off):
    yield dut.off.eq(off);yield dut.write.eq(0);yield dut.selected.eq(1);yield;yield
    value=(yield dut.rdata);yield dut.selected.eq(0);return value

def wait(off,value):
    for _ in range(4000):
        if (yield from rd(off))==value:return
        yield
    raise AssertionError(('DMA stalled',hex(off),value,'state',(yield dut.fsm.state),'backend',(yield dut.memory.backend.state),'fault',(yield from rd(0xac)),'desc',get(BASE,32).hex()))

def bench():
    yield from wr(0x88,BASE);yield from wr(0x8c,BASE+0x100);yield from wr(0x90,1)
    yield from wr(0xa8,7);yield from wr(0x80,1)
    for seq,n in enumerate((14,31,32,33,95)):
        slot=seq&1;buf=BASE+0x1000+slot*0x800
        payload=bytes((i*37+seq)&255 for i in range(n));put(buf,payload)
        desc(BASE+slot*32,buf,n)
        yield from wr(0x94,seq+1);yield from wait(0x98,seq+1)
        assert sent[-1]==payload,(seq,sent[-1].hex(),payload.hex())
        words=struct.unpack('<8I',get(BASE+slot*32,32))
        assert words[2]==n and words[3]==0x40000000 and words[4]==0xabcdef01,words
        assert (yield dut.irq)
        yield from wr(0xa4,7)
        assert not (yield dut.irq),'IRQ W1C did not clear'
    for seq,n in enumerate((60,98,1101)):
        slot=seq&1;buf=BASE+0x4000+slot*0x800;put(buf,b'\xa5'*1536)
        desc(BASE+0x100+slot*32,buf,0)
        payload=bytes((i*71+seq)&255 for i in range(n))
        for i in range(0,n,4):
            word=payload[i:i+4]
            yield dut.source.valid.eq(1);yield dut.source.data.eq(int.from_bytes(word,'little'))
            yield dut.source.be.eq((1<<len(word))-1)
            yield dut.source.last.eq(i+4>=n);yield
        yield dut.source.valid.eq(0);yield dut.source.last.eq(0);yield
        yield from wait(0x9c,seq+1)
        assert get(buf,n)==payload,('RX data',seq)
        assert get(buf+n,1536-n)==b'\xa5'*(1536-n),'RX tail clobbered'
        words=struct.unpack('<8I',get(BASE+0x100+slot*32,32))
        assert words[2]==n and words[3]==0x40000000,words
        yield from wr(0xa0,seq+1)
    # An invalid buffer must complete with ERROR, never issue a DDR access.
    desc(BASE+32,0xffffffe0,60)
    yield from wr(0x94,6);yield from wait(0x98,6)
    assert struct.unpack('<8I',get(BASE+32,32))[3]==0x60000000
    assert len(sent)==5
    # A stop drains the accepted descriptor but must not start the queued one.
    desc(BASE,BASE+0x1000,33);desc(BASE+32,BASE+0x1800,33)
    yield from wr(0x94,8)
    for _ in range(100):
        if (yield from rd(0x84))&1:break
        yield
    else:raise AssertionError('TX did not start')
    yield from wr(0x80,0)
    yield from wait(0x98,7)
    for _ in range(100):yield
    assert (yield from rd(0x98))==7
    assert struct.unpack('<8I',get(BASE+32,32))[3]==0x80000000,'queued ownership lost'
    assert len(sent)==6
    for _ in range(20):yield
    assert (yield from rd(0x84))==0
    assert (yield from rd(0x40))==0 and (yield from rd(0x44))==0
    assert responses['cpu'] and responses['video']
    assert not (yield arbiter.unexpected_response)
    # A PHY reset may abort a frame being discarded while DMA is disabled.
    # Such a frame owns no descriptor/buffer and must not wedge reinitialization.
    yield dut.source.valid.eq(1);yield dut.source.be.eq(15);yield dut.source.last.eq(0)
    for _ in range(3):yield
    yield dut.source.valid.eq(0);yield
    assert (yield from rd(0x84))==0
    yield from wr(0x80,2)
    assert (yield from rd(0x84))==0

put(BASE+0xc00,b'\x3c'*32)
fragment=top.get_fragment()
for memory_block in fragment.specials:
    if isinstance(memory_block,Memory):
        for port in memory_block.ports:
            if port.dat_r is None:port.dat_r=Signal(memory_block.width)
run_simulation(fragment,{'sys':[bench(),capture()],'ddr':[memory(),cpu_traffic(),video_traffic()]},clocks={'sys':20,'ddr':10})
print('PASS Ethernet DMA rings: TX/RX, wrap, masks, descriptor ownership, invalid address')
