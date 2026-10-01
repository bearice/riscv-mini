"""Reject incomplete PnR evidence; retain real setup/hold and clock usage."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.build import pnr_report

# Resource rows captured from the M9 Gowin V1.9.11.02 routed report.
RESOURCES='''
  Logic     | 15528/20736 | 75%
  Register  | 8414/16173  | 53%
  CLS       | 9213/10368  | 89%
  I/O Port  | 131/207     | 64%
  IOLOGIC   | 62/207      | 30%
  BSRAM     | 34/46       | 74%
  PRIMARY   | 8/8         | 100%
  LW        | 8/8         | 100%
  GCLK_PIN  | 6/8         | 75%
  CLKDIV    | 1/8         | 13%
  DHCEN     | 1/16        | 7%
  DLL       | 1/4         | 25%
  DQS       | 2/9         | 23%
  rPLL      | 4/4         | 100%
'''
TIMING='<table><tr><td>Numbers of Setup Violated Endpoints</td><td>0</td></tr><tr><td>Numbers of Hold Violated Endpoints</td><td>0</td></tr></table>'

class BuildReportTests(unittest.TestCase):
    def report(self,resources=RESOURCES,timing=TIMING):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pnr=root/'gateware/impl/pnr';pnr.mkdir(parents=True)
            (pnr/'project.rpt.txt').write_text(resources)
            (pnr/'project_tr_content.html').write_text(timing)
            return pnr_report(root)

    def test_complete_report_and_violations(self):
        counts,resources=self.report()
        self.assertEqual(counts,{'setup':0,'hold':0})
        self.assertEqual(resources['CLS'],{'used':9213,'available':10368})
        self.assertEqual(resources['PRIMARY'],{'used':8,'available':8})
        self.assertEqual(resources['LW'],{'used':8,'available':8})
        self.assertEqual(resources['rPLL'],{'used':4,'available':4})
        counts,_=self.report(timing=TIMING.replace('<td>0</td>','<td>3</td>',1))
        self.assertEqual(counts,{'setup':3,'hold':0})

    def test_missing_clock_or_timing_is_not_success(self):
        for missing in ('  PRIMARY   | 8/8         | 100%\n',
                        '  rPLL      | 4/4         | 100%\n'):
            with self.assertRaisesRegex(RuntimeError,'Cannot verify'):
                self.report(resources=RESOURCES.replace(missing,''))
        with self.assertRaisesRegex(RuntimeError,'Cannot verify Hold timing'):
            self.report(timing=TIMING.replace('Hold','Other'))

    def test_invalid_usage_is_rejected(self):
        for value in ('9/8','0/0'):
            with self.assertRaisesRegex(RuntimeError,'Invalid PRIMARY'):
                self.report(resources=RESOURCES.replace('PRIMARY   | 8/8',f'PRIMARY   | {value}'))

if __name__=='__main__':unittest.main()
