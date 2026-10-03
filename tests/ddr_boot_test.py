"""Protocol-level DDR boot checks; fake PHY is not a Gowin timing model."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Signal, ResetInserter
from migen.sim import run_simulation, passive
from litedram.phy.dfi import Interface
from gateware.ddr_boot import DDRBoot


def exercise(mode='good', retrain=False):
    phy = SimpleNamespace(dfi=Interface(13, 3, 1, 64, 2))
    for name in ('rdly_dq_rst', 'rdly_dq_inc', 'rdly_dq_dir',
                 'rdly_dq_bitslip_rst', 'rdly_dq_bitslip', 'burstdet_clr'):
        setattr(phy, name, Signal())
    phy.dly_sel = Signal(2)
    phy.burstdet_seen = Signal(2)
    dut = ResetInserter()(DDRBoot(phy, reset_cycles=8, release_cycles=12, delays=16, probe_cycles=8))
    trace = []
    tuning = [0, 0]
    slips = [0, 0]
    generation = [0]
    # Lane 0: narrow window on slip 0, largest on slip 2.
    # Lane 1: largest on slip 1. Subsequent reset moves the largest windows.
    def valid(lane):
        offset = generation[0]
        if mode == 'no_window': return False
        if lane == 0:
            return (slips[lane] == 0 and 1 <= tuning[lane] < 4) or (
                slips[lane] == 2 and 5+offset <= tuning[lane] < 13+offset)
        return slips[lane] == 1 and 2+offset <= tuning[lane] < 8+offset

    @passive
    def model():
        memory = [0, 0]
        pending = 0
        seen = 0
        cycles = 0
        while True:
            selected = (yield phy.dly_sel)
            for lane in range(2):
                if selected & (1 << lane):
                    if (yield phy.rdly_dq_bitslip_rst): slips[lane] = 0
                    elif (yield phy.rdly_dq_bitslip): slips[lane] = (slips[lane]+1) & 3
                    if (yield phy.rdly_dq_rst): tuning[lane] = 0
                    elif (yield phy.rdly_dq_inc):
                        if not (yield phy.rdly_dq_dir): tuning[lane] += 1
            p = dut.dfi.p0
            if not (yield p.cs_n):
                cmd = ((yield p.ras_n), (yield p.cas_n), (yield p.we_n))
                trace.append((cycles, cmd, (yield p.bank), (yield p.address)))
                if cmd == (1, 0, 0):
                    memory = []
                    for phase in dut.dfi.phases: memory.append((yield phase.wrdata))
                if cmd == (1, 0, 1):
                    assert not selected, 'DQS HOLD must be released during read'
                    pending = 4
            if (yield phy.burstdet_clr): seen = 0
            for phase in dut.dfi.phases: yield phase.rddata_valid.eq(0)
            if pending:
                pending -= 1
                if pending == 0:
                    for index, phase in enumerate(dut.dfi.phases):
                        value = 0
                        for lane in range(2):
                            mask = 0x00ff00ff00ff00ff << (lane*8)
                            if valid(lane): value |= memory[index] & mask
                        yield phase.rddata.eq(value)
                        yield phase.rddata_valid.eq(mode != 'missing_valid')
                    if mode != 'missing_burst':
                        seen = sum(int(valid(lane)) << lane for lane in range(2))
            yield phy.burstdet_seen.eq(seen)
            cycles += 1
            yield

    def drive():
        for attempt in range(2 if retrain else 1):
            for _ in range(40000):
                if (yield dut.ready) or (yield dut.failed): break
                yield
            else: raise AssertionError('FSM did not terminate')
            if mode == 'good':
                assert (yield dut.ready) and (yield dut.handover) and not (yield dut.failed)
                expected = [(9+generation[0], 2, 8), (5+generation[0], 1, 6)]
                for csr, fields in zip((dut.lane0, dut.lane1), expected):
                    value = (yield csr.status)
                    assert (value & 255, value >> 8 & 3, value >> 16 & 511) == fields, hex(value)
                assert (yield phy.dly_sel) == 0
            else:
                assert (yield dut.failed) and not (yield dut.ready) and not (yield dut.handover)
                length = len(trace)
                for _ in range(100): yield
                assert len(trace) == length, 'failed training must stop issuing commands'
            if retrain and attempt == 0:
                generation[0] = 1
                yield dut.reset.eq(1)
                for _ in range(4): yield
                assert not (yield dut.ready) and not (yield dut.handover)
                yield dut.reset.eq(0)
                yield
        mr = [row for row in trace if row[1] == (0, 0, 0)]
        assert [(row[2], row[3]) for row in mr[:4]] == [(2, 8), (3, 0), (1, 3), (0, 0x320)]
        assert all(b[0]-a[0] >= 16 for a, b in zip(mr[:3], mr[1:4]))

    run_simulation(dut, [drive(), model()])


class DDRBootTests(unittest.TestCase):
    def test_widest_window_and_reset_retraining(self): exercise(retrain=True)
    def test_no_valid_window(self): exercise('no_window')
    def test_missing_read_valid(self): exercise('missing_valid')
    def test_missing_burst(self): exercise('missing_burst')


if __name__ == '__main__': unittest.main()
