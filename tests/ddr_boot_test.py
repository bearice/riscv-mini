"""ROM DDR CSR protocol: fresh capture, lane comparison and exclusive handover.

This exercises the command interface, not the physical PHY or the C algorithm.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen import Signal, ResetInserter
from migen.sim import run_simulation
from litedram.phy.dfi import Interface
from gateware.ddr_boot import DDRBoot, training_patterns

phy=SimpleNamespace(dfi=Interface(13,3,1,64,2),dly_sel=Signal(2),burstdet_seen=Signal(2))
for name in ('rdly_dq_rst','rdly_dq_inc','rdly_dq_dir','rdly_dq_bitslip_rst','rdly_dq_bitslip','burstdet_clr'):
    setattr(phy,name,Signal())
dut=ResetInserter()(DDRBoot(phy))

def drive():
    yield dut.control.storage.eq(3);yield dut.tuning.storage.eq(1|8);yield dut.tuning.re.eq(1);yield
    assert (yield phy.rdly_dq_inc) and (yield phy.dly_sel)==1
    yield dut.tuning.re.eq(0);yield
    assert not (yield phy.rdly_dq_inc),'pulse became held tuning'
    yield dut.tuning.storage.eq(0)
    for i,seed in enumerate((42,84,36)):
        yield dut.pattern.storage.eq(i)
        yield dut.command.storage.eq((1<<20)|(1<<22)|(1<<24));yield dut.command.re.eq(1);yield
        assert not (yield dut.dfi.p0.cs_n) and (yield dut.dfi.p0.rddata_en)
        assert (yield phy.burstdet_clr)
        yield dut.command.re.eq(0);yield;yield
        assert not ((yield dut.status.status)&0x34),'stale successful capture survived new read'
        a,b=training_patterns(seed)
        yield dut.dfi.p0.rddata.eq(a);yield dut.dfi.p1.rddata.eq(b)
        yield dut.dfi.p0.rddata_valid.eq(1);yield
        assert not ((yield dut.status.status)&4),'partial DFI valid accepted'
        yield dut.dfi.p1.rddata_valid.eq(1);yield;yield
        assert ((yield dut.status.status)&0x34)==0x34
        yield dut.dfi.p0.rddata.eq(a^0xff);yield;yield
        assert ((yield dut.status.status)&0x34)==0x24,'lane comparison not independent'
        yield dut.dfi.p0.rddata_valid.eq(0);yield dut.dfi.p1.rddata_valid.eq(0)
    yield dut.control.storage.eq(7);yield dut.command.re.eq(1);yield dut.tuning.re.eq(1)
    yield dut.tuning.storage.eq(3|8);yield
    assert (yield dut.ready) and (yield dut.handover)
    assert (yield dut.dfi.p0.cs_n) and not (yield phy.rdly_dq_inc) and not (yield phy.dly_sel)
    # CSR storage is driven directly by this harness; model its reset value.
    yield dut.control.storage.eq(0);yield
    assert not (yield dut.ready),'cleared control did not revoke DDR ownership'
run_simulation(dut,drive())
print('Software DDR command / fresh capture / lane match / exclusive handover PASS')
