"""Transport restart: coordinated reset assertion, independent domain release."""
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from migen import ResetInserter
from ae350_burst_test import RegisteredDUT,run_dut
from scripts.tests.test_ddr3_ahb import Model,ahb

class CDCResetTests(unittest.TestCase):
    def exercise(self, first, pending):
        dut=RegisteredDUT(response_timeout=64,ui_timeout=32)
        ResetInserter(['sys','ddr'])(dut.ddr)
        ResetInserter()(dut.ic)
        model=Model(dut.ui,dut.mon)
        def process():
            if pending:
                # Cancel a request before the UI has accepted it. A platform
                # restart resets the CPU as well; no old HRESP is owed after it.
                model.stall=True
                yield dut.bus.addr.eq(0x2000)
                yield dut.bus.size.eq(3)
                yield dut.bus.write.eq(0)
                yield dut.bus.trans.eq(2)
                yield
                yield dut.bus.trans.eq(0)
                for _ in range(12): yield
            yield dut.ddr.reset_sys.eq(1)
            yield dut.ddr.reset_ddr.eq(1)
            yield dut.ic.reset.eq(1)
            for _ in range(8):yield
            if first=='ddr':
                yield dut.ddr.reset_ddr.eq(0)
            else:
                yield dut.ddr.reset_sys.eq(0)
                yield dut.ic.reset.eq(0)
            for _ in range(9):yield
            yield dut.ddr.reset_sys.eq(0)
            yield dut.ddr.reset_ddr.eq(0)
            yield dut.ic.reset.eq(0)
            model.stall=False
            for _ in range(12):yield
            e,_=yield from ahb(dut.bus,True,0x2020,0x1122334455667788,size=3,timeout=400)
            self.assertEqual(e,0)
            e,v=yield from ahb(dut.bus,False,0x2020,size=3,timeout=400)
            self.assertEqual((e,v),(0,0x1122334455667788))
            self.assertEqual((yield dut.ddr.n_req),2)
            self.assertEqual((yield dut.ic.n_accepted),2)
        run_dut(dut,process,model,True)
    def test_ui_releases_first(self):self.exercise('ddr',False)
    def test_mem_releases_first(self):self.exercise('mem',False)
    def test_pending_request_ui_releases_first(self):self.exercise('ddr',True)
    def test_pending_request_mem_releases_first(self):self.exercise('mem',True)

if __name__=='__main__':unittest.main()
