"""Shared cache visibility, eviction, masks, maintenance and backpressure."""
import argparse
import random
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Module, ResetInserter
from migen.sim import run_simulation, passive
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.shared_l2 import SharedL2

parser=argparse.ArgumentParser();parser.add_argument('--writeback',action='store_true');args=parser.parse_args()
dut=Module();wb=wishbone.Interface(data_width=32,address_width=32,addressing='word')
port=LiteDRAMNativePort('both',23,128)
dut.submodules.cache=cache=SharedL2(wb,port,size=256,writeback=args.writeback)
ram={};counts={'reads':0,'writes':0};events=[]
def initial(a):return sum(((a*17+i*11)&255)<<(i*8) for i in range(16))
def value(a):return ram.get(a,initial(a))
@passive
def memory():
    cycle=0;read=None;write=None
    while True:
        if (yield port.cmd.valid) and (yield port.cmd.ready):
            a=(yield port.cmd.addr)
            if (yield port.cmd.we):write=a
            else:read=[a,5];counts['reads']+=1
        if (yield port.wdata.valid) and (yield port.wdata.ready):
            assert write is not None
            old=value(write);new=(yield port.wdata.data);mask=(yield port.wdata.we)
            for i in range(16):
                if mask>>i&1:old=(old & ~(255<<(i*8))) | (new & (255<<(i*8)))
            ram[write]=old;counts['writes']+=1;events.append(('write',write,mask));write=None
        if read and read[1]==0 and (yield port.rdata.valid) and (yield port.rdata.ready):read=None
        if read and read[1]>0:read[1]-=1
        yield port.cmd.ready.eq(read is None and write is None and cycle%3!=0)
        yield port.wdata.ready.eq(write is not None and cycle%4==1)
        yield port.rdata.valid.eq(read is not None and read[1]==0)
        if read:yield port.rdata.data.eq(value(read[0]))
        cycle+=1;yield

def cpu(a,data=None,sel=15):
    yield wb.adr.eq((0x00000000>>2)+a//4);yield wb.we.eq(data is not None)
    yield wb.dat_w.eq(data or 0);yield wb.sel.eq(sel);yield wb.cyc.eq(1);yield wb.stb.eq(1)
    for n in range(2000):
        yield
        if (yield wb.ack):
            result=(yield wb.dat_r);break
    else:raise AssertionError(('CPU timeout',a,(yield cache.fsm.state)))
    yield wb.cyc.eq(0);yield wb.stb.eq(0);yield;yield
    return result

def dma(a,data=None,mask=65535,client=None,data_delay=0):
    p=client or cache.dma
    yield p.cmd.addr.eq(a//16);yield p.cmd.we.eq(data is not None);yield p.cmd.valid.eq(1)
    yield p.wdata.data.eq(data or 0);yield p.wdata.we.eq(mask);yield p.wdata.valid.eq(data is not None and not data_delay)
    for _ in range(2000):
        yield
        if (yield p.cmd.ready):break
    else:raise AssertionError('DMA command timeout')
    yield p.cmd.valid.eq(0)
    if data is not None:
        # Native command and data are separate handshakes. A producer may
        # deliver data later, and must not get a completion before commit.
        for _ in range(data_delay):
            yield
            assert not (yield p.wdata.ready),'write completed before data arrived'
        yield p.wdata.valid.eq(1)
        for _ in range(2000):
            yield
            if (yield p.wdata.ready):break
        else:raise AssertionError('DMA write timeout')
        yield p.wdata.valid.eq(0);yield;yield
        return
    # Consumer stalls after command acceptance; response must remain stable.
    yield p.rdata.ready.eq(0)
    for _ in range(20):yield
    for _ in range(2000):
        if (yield p.rdata.valid):break
        yield
    else:raise AssertionError('DMA response timeout')
    result=(yield p.rdata.data)
    for _ in range(4):
        yield
        assert (yield p.rdata.valid) and (yield p.rdata.data)==result
    yield p.rdata.ready.eq(1);yield;yield p.rdata.ready.eq(0);yield;yield
    return result

def maintenance(invalidate=False):
    reg=cache._invalidate if invalidate else cache._flush
    yield reg.re.eq(1);yield;yield reg.re.eq(0);yield
    for _ in range(4000):
        yield
        if not (yield cache._busy.status):break
    else:raise AssertionError('Maintenance timeout')

def test():
    for _ in range(30):yield
    old=value(0)
    assert (yield from cpu(0))==old&0xffffffff
    before=counts['reads']
    assert (yield from dma(0,client=cache.video))==old
    assert counts['reads']==before,'streaming hit must use shared RAM'
    yield from cpu(4,0x11223344)
    expected=(old & ~(0xffffffff<<32)) | (0x11223344<<32)
    assert (yield from dma(0))==expected,'DMA must see CPU store'
    if args.writeback:assert value(0)==old,'store should remain dirty'
    yield from dma(0,0xa5<<80,1<<10)
    expected=(expected & ~(255<<80)) | (0xa5<<80)
    assert (yield from cpu(8))==(expected>>64)&0xffffffff,'CPU must see DMA byte store'
    # Conflicting nonallocating streaming reads must not evict a CPU line.
    before=counts['reads']
    assert (yield from dma(256,client=cache.video))==value(16)
    assert (yield from dma(0))==expected
    assert counts['reads']==before+1
    # Allocation conflict must write back the OLD address with all merged bytes.
    yield from cpu(256)
    assert value(0)==expected
    yield from cpu(260,0xfeedbeef)
    yield from maintenance()
    assert value(16)>>32&0xffffffff==0xfeedbeef
    # Partial streaming miss preserves all unselected bytes.
    prev=value(80)
    yield from dma(1280,0xcc,1)
    assert (yield from dma(1280))==(prev & ~255)|0xcc
    yield from dma(1280,0xdd<<8,2,data_delay=19)
    assert (yield from dma(1280))==(prev & ~65535)|0xddcc
    yield from cpu(32,0x12345678)
    assert (yield from cpu(32))==0x12345678
    assert (yield from dma(32))&0xffffffff==0x12345678,'ACKed CPU write must be visible through L2'
    # Every CPU byte lane and a halfword must preserve unrelated bytes.
    expected=value(4)
    for lane in range(4):
        for byte in range(4):
            v=0x70+lane*4+byte
            yield from cpu(64+lane*4,v<<(byte*8),1<<byte)
            offset=lane*32+byte*8
            expected=(expected & ~(255<<offset)) | (v<<offset)
    yield from cpu(72,0xabcd,3)
    expected=(expected & ~(65535<<64)) | (0xabcd<<64)
    assert (yield from dma(64))==expected
    yield from maintenance()
    assert value(4)==expected
    yield from cpu(48,0xabcdef01)
    yield cache._enable.storage.eq(0)
    for _ in range(2000):
        yield
        if not (yield cache._busy.status):break
    else:raise AssertionError('disable timeout')
    assert value(3)&0xffffffff==0xabcdef01
    yield cache._enable.storage.eq(1)
    for _ in range(100):yield
    yield from maintenance(True)
    # Cancelled accepted read must drain without acknowledging a new owner.
    yield wb.adr.eq((0x00000000>>2)+0x10000//4);yield wb.we.eq(0)
    yield wb.cyc.eq(1);yield wb.stb.eq(1)
    for _ in range(3):yield
    yield wb.cyc.eq(0);yield wb.stb.eq(0)
    for _ in range(40):
        yield
        assert not (yield wb.ack)
    assert (yield from cpu(0))==value(0)&0xffffffff
    print('Shared L2 PASS',{'writeback':args.writeback,**counts})

run_simulation(dut,[test(),memory()])

dut=Module();wb=wishbone.Interface(data_width=32,address_width=32,addressing='word')
port=LiteDRAMNativePort('both',23,128)
dut.submodules.cache=cache=SharedL2(wb,port,size=256,writeback=args.writeback)
completed=[0,0,0]
def contend(client):
    for n in range(20):
        a=0x20000+client*0x1000+n*16
        if client==0:assert (yield from cpu(a))==value(a//16)&0xffffffff
        else:assert (yield from dma(a,client=cache.video if client==1 else cache.dma))==value(a//16)
        completed[client]+=1
run_simulation(dut,[contend(0),contend(1),contend(2),memory()])
assert completed==[20,20,20],completed
print('Shared arbitration PASS CPU/video/DMA=',completed)

# A system reset must sweep valid/dirty tags before serving the next owner.
# Clean first: preserving dirty lines across a whole-system reset is not promised.
dut=Module();wb=wishbone.Interface(data_width=32,address_width=32,addressing='word')
port=LiteDRAMNativePort('both',23,128)
dut.submodules.cache=cache=ResetInserter()(SharedL2(wb,port,size=256,writeback=args.writeback))
def reset_check():
    for _ in range(30):yield
    yield from cpu(0,0x87654321)
    yield from maintenance()
    assert (yield from cpu(0))==0x87654321
    ram[0]=(value(0)&~0xffffffff)|0x12345678
    yield cache.reset.eq(1);yield;yield;yield cache.reset.eq(0)
    assert (yield from cpu(0))==0x12345678,'reset left a stale cache tag'
run_simulation(dut,[reset_check(),memory()])
print('Shared reset PASS')

# Exercise frequent direct-map conflicts with an independent byte-level
# reference image, mixing CPU writes and native DMA writes/readers.
dut=Module();wb=wishbone.Interface(data_width=32,address_width=32,addressing='word')
port=LiteDRAMNativePort('both',23,128)
dut.submodules.cache=cache=SharedL2(wb,port,size=256,writeback=args.writeback)
def mixed_check():
    rng=random.Random(0x20);expected={}
    for _ in range(30):yield
    for n in range(120):
        line=rng.randrange(64);a=line*16
        old=expected.get(line,value(line))
        if n%2:
            lane=rng.randrange(4);sel=rng.randrange(1,16);v=rng.getrandbits(32)
            yield from cpu(a+lane*4,v,sel)
            for b in range(4):
                if sel>>b&1:
                    offset=lane*32+b*8
                    old=(old&~(255<<offset))|(((v>>(b*8))&255)<<offset)
        else:
            mask=rng.randrange(1,65536);v=rng.getrandbits(128)
            yield from dma(a,v,mask,data_delay=n%7)
            for b in range(16):
                if mask>>b&1:old=(old&~(255<<(b*8)))|(v&(255<<(b*8)))
        expected[line]=old
        assert (yield from dma(a,client=cache.video if n%3 else cache.dma))==old
        lane=rng.randrange(4)
        assert (yield from cpu(a+lane*4))==(old>>(lane*32))&0xffffffff
    yield from maintenance()
    assert all(value(line)==v for line,v in expected.items()),'flush differs from reference image'
run_simulation(dut,[mixed_check(),memory()])
print('Shared mixed CPU/DMA reference PASS 120 operations')
