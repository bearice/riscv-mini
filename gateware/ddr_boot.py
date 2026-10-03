"""Autonomous H5TQ1G63EFR boot at sys=60 MHz, CK=120 MHz, DLL off.

Training uses bank 0 / row 0 / column 0. No CPU/DMA request is accepted until
ownership transfers to LiteDRAM. A failed training remains isolated until reset.
"""
from migen import Signal, Constant, Array, Cat, Mux, If, FSM, NextState, NextValue, ResetInserter
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSRStatus
from litedram.phy.dfi import Interface
from litedram.core.controller import LiteDRAMController
from litedram.core.crossbar import LiteDRAMCrossbar
from migen.genlib.record import DIR_M_TO_S


def training_patterns(seed):
    a = ((0x19e34ab7 ^ seed) << 32) | (0xbc08f56d ^ (seed * 0x10204081 & 0xffffffff))
    b = (~a ^ 0x792e5104c3a86df0) & ((1 << 64)-1)
    return a, b


class DDRBoot(LiteXModule):
    def __init__(self, phy, *, reset_cycles=12000, release_cycles=30000, delays=256, probe_cycles=64):
        self.ready = Signal()
        self.failed = Signal()
        self.handover = Signal()
        self.status = CSRStatus(32, description="bit0 ready, bit1 failed, bit2 lane, bits8:15 FSM state")
        self.lane0 = CSRStatus(32, description="tap[7:0], bitslip[9:8], window[24:16]")
        self.lane1 = CSRStatus(32, description="tap[7:0], bitslip[9:8], window[24:16]")
        self.dfi = dfi = Interface(len(phy.dfi.p0.address), len(phy.dfi.p0.bank), 1, 64, 2)
        lane = Signal()
        slip = Signal(2)
        tap = Signal(8)
        run = Signal(9)
        run_start = Signal(8)
        best_len = Signal(9)
        best_tap = Signal(8)
        best_slip = Signal(2)
        steps = Signal(8)
        verifying = Signal()
        pattern = Signal(2)
        all_ok = Signal(reset=1)
        reset_n = Signal()
        cke = Signal()
        timer = Signal(max=max(reset_cycles, release_cycles, 301)+1)
        self.sync += If(timer != 0, timer.eq(timer-1))
        # Hold the selected byte lane only while its delay/bitslip is adjusted.
        hold = Signal(reset=1)
        self.comb += phy.dly_sel.eq(Mux(hold, 1 << lane, 0))
        pairs = [training_patterns(seed) for seed in (42, 84, 36)]
        a = Signal(64)
        b = Signal(64)
        self.comb += [a.eq(Array(Constant(p[0], 64) for p in pairs)[pattern]),
                      b.eq(Array(Constant(p[1], 64) for p in pairs)[pattern])]
        for i, phase in enumerate(dfi.phases):
            self.comb += [phase.reset_n.eq(reset_n), phase.cke.eq(cke), phase.odt.eq(0),
                          phase.wrdata.eq(a if i == 0 else b), phase.wrdata_mask.eq(0)]

        # Separate bounded probe engine; data is compared on rddata_valid,
        # rather than sampling a potentially stale PHY output after a delay.
        start = Signal()
        done = Signal()
        ok = Signal()
        captured = Signal()
        matched = Signal()
        mismatch = Signal()
        lane_errors = []
        for byte_lane in range(2):
            expected = Cat(*[word[16*n+8*byte_lane:16*n+8*byte_lane+8]
                             for word in (a, b) for n in range(4)])
            actual = Cat(*[phase.rddata[16*n+8*byte_lane:16*n+8*byte_lane+8]
                           for phase in dfi.phases for n in range(4)])
            lane_errors.append(actual != expected)
        self.comb += mismatch.eq(Mux(lane, lane_errors[1], lane_errors[0]))
        self.probe = probe = FSM(reset_state='IDLE')
        probe_timer = Signal(7)
        self.sync += If(probe_timer != 0, probe_timer.eq(probe_timer-1))
        probe.act('IDLE', If(start, NextValue(captured, 0), NextValue(matched, 0), NextState('ACT')))
        def command(ras=1, cas=1, we=1, address=0, bank=0):
            return [dfi.p0.cs_n.eq(0), dfi.p0.ras_n.eq(ras), dfi.p0.cas_n.eq(cas),
                    dfi.p0.we_n.eq(we), dfi.p0.address.eq(address), dfi.p0.bank.eq(bank)]
        def probe_wait(name, following):
            probe.act(name, If(probe_timer == 0, NextState(following)))
        probe.act('ACT', *command(ras=0), NextValue(probe_timer, probe_cycles-1), NextState('ACT_WAIT'))
        probe_wait('ACT_WAIT', 'WRITE')
        probe.act('WRITE', *command(cas=0, we=0), dfi.p0.wrdata_en.eq(1),
                  NextValue(probe_timer, probe_cycles-1), NextState('WRITE_WAIT'))
        probe_wait('WRITE_WAIT', 'CLEAR')
        probe.act('CLEAR', phy.burstdet_clr.eq(1), NextState('READ'))
        probe.act('READ', *command(cas=0), dfi.p0.rddata_en.eq(1),
                  NextValue(probe_timer, probe_cycles-1), NextState('READ_WAIT'))
        probe.act('READ_WAIT',
                  If(dfi.p0.rddata_valid & dfi.p1.rddata_valid,
                     NextValue(captured, 1), NextValue(matched, ~mismatch)),
                  If(probe_timer == 0, NextState('PRE')))
        probe.act('PRE', *command(ras=0, we=0), NextValue(probe_timer, probe_cycles-1), NextState('PRE_WAIT'))
        probe_wait('PRE_WAIT', 'REF')
        probe.act('REF', *command(ras=0, cas=0), NextValue(probe_timer, probe_cycles-1), NextState('REF_WAIT'))
        probe_wait('REF_WAIT', 'DONE')
        probe.act('DONE', done.eq(1), ok.eq(captured & matched & (phy.burstdet_seen >> lane)), NextState('IDLE'))

        self.fsm = fsm = FSM(reset_state='RESET')
        stage = Signal(8)
        self.comb += self.status.status.eq(Cat(self.ready, self.failed, lane, Constant(0, 5), stage))
        def wait(name, following):
            fsm.act(name, If(timer == 0, NextState(following)))
        fsm.act('RESET', NextValue(timer, reset_cycles-1), NextState('RESET_WAIT'))
        wait('RESET_WAIT', 'RELEASE')
        fsm.act('RELEASE', NextValue(reset_n, 1), NextValue(timer, release_cycles-1), NextState('RELEASE_WAIT'))
        wait('RELEASE_WAIT', 'CKE')
        fsm.act('CKE', NextValue(cke, 1), NextValue(timer, 63), NextState('CKE_WAIT'))
        wait('CKE_WAIT', 'MR2')
        for name, bank, address, following in [('MR2', 2, 0x8, 'MR3'), ('MR3', 3, 0, 'MR1'),
                                                ('MR1', 1, 0x3, 'MR0'), ('MR0', 0, 0x320, 'ZQ')]:
            fsm.act(name, *command(ras=0, cas=0, we=0, bank=bank, address=address),
                    NextValue(timer, 15), NextState(name+'_WAIT'))
            wait(name+'_WAIT', following)
        fsm.act('ZQ', *command(we=0, address=0x400), NextValue(timer, 299), NextState('ZQ_WAIT'))
        wait('ZQ_WAIT', 'LANE')
        fsm.act('LANE', NextValue(best_len, 0), NextValue(slip, 0), phy.rdly_dq_bitslip_rst.eq(1),
                NextValue(timer, 3), NextState('SLIP_WAIT'))
        wait('SLIP_WAIT', 'DELAY_RESET')
        # Same saturating reset procedure as the accepted software training:
        # RLOADN then 255 positive RMOVE pulses, each separated by a low cycle.
        fsm.act('DELAY_RESET', phy.rdly_dq_rst.eq(1), phy.rdly_dq_dir.eq(1),
                NextValue(steps, 255), NextValue(run, 0), NextValue(tap, 0), NextState('RESET_GAP'))
        fsm.act('RESET_GAP', phy.rdly_dq_dir.eq(1), NextState('RESET_MOVE'))
        fsm.act('RESET_MOVE', phy.rdly_dq_dir.eq(1), phy.rdly_dq_inc.eq(1),
                NextValue(steps, steps-1), NextState('RESET_MOVE_GAP'))
        fsm.act('RESET_MOVE_GAP', phy.rdly_dq_dir.eq(1),
                If(steps == 0, NextValue(timer, 3), NextState('DELAY_WAIT')).Else(NextState('RESET_MOVE')))
        wait('DELAY_WAIT', 'ADJUST')
        fsm.act('ADJUST', If(verifying & (tap != best_tap), phy.rdly_dq_inc.eq(1),
                            NextValue(tap, tap+1), NextState('ADJUST_GAP')).Else(NextState('PATTERNS')))
        fsm.act('ADJUST_GAP', NextState('ADJUST'))
        fsm.act('PATTERNS', NextValue(pattern, 0), NextValue(all_ok, 1), NextValue(hold, 0),
                NextValue(timer, 7), NextState('PROBE_SETTLE'))
        wait('PROBE_SETTLE', 'PROBE_START')
        fsm.act('PROBE_START', start.eq(1), NextState('PROBE_WAIT'))
        fsm.act('PROBE_WAIT', If(done,
                NextValue(all_ok, all_ok & ok),
                If(pattern == 2, NextValue(hold, 1), NextState('RESULT')).Else(
                    NextValue(pattern, pattern+1), NextState('PROBE_START'))))
        midpoint = Mux(run == 0, tap, run_start) + ((run+1) >> 1)
        fsm.act('RESULT',
                If(verifying,
                    If(~all_ok, NextState('FAILED')).Else(
                        If(lane == 0,
                            NextValue(self.lane0.status, Cat(best_tap, best_slip, Constant(0, 6), best_len)),
                            NextValue(lane, 1), NextValue(verifying, 0), NextState('LANE')
                        ).Else(
                            NextValue(self.lane1.status, Cat(best_tap, best_slip, Constant(0, 6), best_len)),
                            NextValue(hold, 0), NextState('HANDOVER')))
                ).Else(
                    If(all_ok,
                       If(run == 0, NextValue(run_start, tap)),
                       NextValue(run, run+1),
                       If(run+1 > best_len, NextValue(best_len, run+1),
                          NextValue(best_tap, midpoint), NextValue(best_slip, slip))
                    ).Else(NextValue(run, 0)), NextState('SCAN_NEXT')))
        fsm.act('SCAN_NEXT',
                If(tap == delays-1,
                    If(slip == 3, NextState('SELECT')).Else(
                        phy.rdly_dq_bitslip.eq(1), NextValue(slip, slip+1),
                        NextValue(timer, 3), NextState('SLIP_WAIT'))
                ).Else(phy.rdly_dq_inc.eq(1), NextValue(tap, tap+1),
                       NextValue(timer, 3), NextState('TAP_WAIT')))
        wait('TAP_WAIT', 'PATTERNS')
        fsm.act('SELECT', If(best_len < 4, NextState('FAILED')).Else(
                phy.rdly_dq_bitslip_rst.eq(1), NextValue(slip, 0), NextValue(verifying, 1), NextState('SELECT_GAP')))
        fsm.act('SELECT_GAP', NextState('SELECT_SLIP'))
        fsm.act('SELECT_SLIP', If(slip == best_slip, NextState('DELAY_RESET')).Else(
                phy.rdly_dq_bitslip.eq(1), NextValue(slip, slip+1), NextState('SELECT_GAP')))
        fsm.act('HANDOVER', NextValue(self.handover, 1), NextValue(timer, 63), NextState('HANDOVER_WAIT'))
        wait('HANDOVER_WAIT', 'READY')
        fsm.act('READY', NextValue(self.ready, 1))
        fsm.act('FAILED', NextValue(hold, 0), NextValue(self.failed, 1))
        for number, name in enumerate(list(fsm.actions)):
            fsm.act(name, stage.eq(number))


class HardwareDDRCore(LiteXModule):
    """LiteDRAM controller/crossbar with exclusive hardware boot ownership.

    Unlike LiteDRAMCore this has no software DFI injector or PHY tuning CSRs.
    Both controller and crossbar stay reset during training; boot status is
    readable by the ROM independently of the unavailable DDR memory bus.
    """
    def __init__(self, phy, module, clk_freq):
        self.boot = DDRBoot(phy)
        self.controller = ResetInserter()(LiteDRAMController(
            phy.settings, module.geom_settings, module.timing_settings, clk_freq))
        self.crossbar = ResetInserter()(LiteDRAMCrossbar(self.controller.interface))
        self.comb += [self.controller.reset.eq(~self.boot.handover),
                      self.crossbar.reset.eq(~self.boot.handover)]
        for physical, training, controller in zip(phy.dfi.phases, self.boot.dfi.phases, self.controller.dfi.phases):
            for name, _, direction in physical.layout:
                dst = getattr(physical, name)
                if direction == DIR_M_TO_S:
                    self.comb += dst.eq(Mux(self.boot.handover, getattr(controller, name), getattr(training, name)))
                else:
                    self.comb += [getattr(training, name).eq(dst), getattr(controller, name).eq(dst)]
