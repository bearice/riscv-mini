"""ROM-owned DDR command/tuning interface; no autonomous training sequencer."""
from migen import Signal, Constant, Array, Cat, Mux, If, ResetInserter
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStorage, CSRStatus
from litedram.phy.dfi import Interface
from litedram.core.controller import LiteDRAMController, ControllerSettings
from litedram.core.crossbar import LiteDRAMCrossbar
from migen.genlib.record import DIR_M_TO_S

DDR_CMD_BUFFER_DEPTH = 1


def training_patterns(seed):
    a = ((0x19e34ab7 ^ seed) << 32) | (0xbc08f56d ^ (seed * 0x10204081 & 0xffffffff))
    return a, (~a ^ 0x792e5104c3a86df0) & ((1 << 64)-1)


class DDRBoot(LiteXModule):
    """Single-cycle command/pulse CSRs and a latched read comparison.

    Software owns JEDEC timing, window search and retries. Fixed test patterns
    avoid wide software write-data CSRs. Reads latch only on both DFI valids;
    a missing capture cannot be mistaken for a previous successful probe.
    """
    def __init__(self, phy):
        self.control=CSRStorage(4, name='control') # reset_n, CKE, handover, failed
        self.command=CSRStorage(25, name='command') # addr16, bank3, RAS/CAS/WE, WR/RD enables
        self.tuning=CSRStorage(7, name='tuning') # lane mask2, reset/inc/dir, slip reset/inc
        self.pattern=CSRStorage(2, name='pattern')
        self.status=CSRStatus(32, name='status')
        self.lane0=CSRStorage(32, name='lane0')
        self.lane1=CSRStorage(32, name='lane1')
        self.ready=Signal();self.handover=Signal();self.failed=Signal()
        self.comb += [self.ready.eq(self.control.storage[2]), self.handover.eq(self.ready),
                      self.failed.eq(self.control.storage[3])]
        self.dfi=dfi=Interface(len(phy.dfi.p0.address),len(phy.dfi.p0.bank),1,64,2)
        tuning=self.tuning.storage; pulse=self.tuning.re & ~self.ready
        self.comb += [phy.dly_sel.eq(Mux(self.ready,0,tuning[:2])),
            phy.rdly_dq_rst.eq(pulse & tuning[2]),phy.rdly_dq_inc.eq(pulse & tuning[3]),
            phy.rdly_dq_dir.eq(tuning[4]),phy.rdly_dq_bitslip_rst.eq(pulse & tuning[5]),
            phy.rdly_dq_bitslip.eq(pulse & tuning[6])]
        pairs=[training_patterns(seed) for seed in (42,84,36)]
        a=Signal(64);b=Signal(64)
        self.comb += [a.eq(Array(Constant(p[0],64) for p in pairs)[self.pattern.storage]),
                      b.eq(Array(Constant(p[1],64) for p in pairs)[self.pattern.storage])]
        for i,p in enumerate(dfi.phases):
            self.comb += [p.reset_n.eq(self.control.storage[0]),p.cke.eq(self.control.storage[1]),
                          p.odt.eq(0),p.wrdata.eq(a if i==0 else b),p.wrdata_mask.eq(0)]
        command=self.command.storage;issue=self.command.re & ~self.ready
        self.comb += [dfi.p0.address.eq(command[:16]),dfi.p0.bank.eq(command[16:19]),
            dfi.p0.cs_n.eq(~issue),dfi.p0.ras_n.eq(Mux(issue,command[20],1)),
            dfi.p0.cas_n.eq(Mux(issue,command[21],1)),dfi.p0.we_n.eq(Mux(issue,command[22],1)),
            dfi.p0.wrdata_en.eq(issue & command[23]),dfi.p0.rddata_en.eq(issue & command[24]),
            phy.burstdet_clr.eq(issue & command[24])]
        captured=Signal();matches=Signal(2);equal=[]
        for lane in range(2):
            expected=Cat(*[word[16*n+8*lane:16*n+8*lane+8] for word in (a,b) for n in range(4)])
            actual=Cat(*[p.rddata[16*n+8*lane:16*n+8*lane+8] for p in dfi.phases for n in range(4)])
            equal.append(actual==expected)
        self.sync += If(issue & command[24],captured.eq(0),matches.eq(0)).Elif(
            dfi.p0.rddata_valid & dfi.p1.rddata_valid,captured.eq(1),matches.eq(Cat(*equal)))
        self.comb += self.status.status.eq(Cat(self.ready,self.failed,captured,Constant(0,1),matches,phy.burstdet_seen))


class SoftwareDDRCore(LiteXModule):
    def __init__(self, phy, module, clk_freq):
        self.boot=DDRBoot(phy)
        self.controller=ResetInserter()(LiteDRAMController(
            phy.settings,module.geom_settings,module.timing_settings,clk_freq,
            # The crossbar gates each bank's valid with the other banks' locks.
            # Depth 0 makes lookahead.valid combinational and feeds req.valid
            # back into req.lock. Keep one registered entry to break that loop.
            controller_settings=ControllerSettings(
                cmd_buffer_depth=DDR_CMD_BUFFER_DEPTH,with_auto_precharge=False)))
        self.crossbar=ResetInserter()(LiteDRAMCrossbar(self.controller.interface))
        self.comb += [self.controller.reset.eq(~self.boot.handover),self.crossbar.reset.eq(~self.boot.handover)]
        for physical,training,controller in zip(phy.dfi.phases,self.boot.dfi.phases,self.controller.dfi.phases):
            for name,_,direction in physical.layout:
                dst=getattr(physical,name)
                if direction==DIR_M_TO_S:
                    self.comb += dst.eq(Mux(self.boot.handover,getattr(controller,name),getattr(training,name)))
                else:
                    self.comb += [getattr(training,name).eq(dst),getattr(controller,name).eq(dst)]
