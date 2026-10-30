"""Reject missing I/O coverage, bad skew/duty and half-cycle folding errors."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from scripts.modular.machines.mega138k_ae350.ethernet_timing import qualify_tx


def fixture(missing=None,data_extra=0.,clock_fall=.05,serialized=False):
    out=['1 eth_tx_clk Generated 8.000 125.000MHz 0.000 4.000 clk50 clk50']
    for mode,base in (('setup',10.),('hold',6.)):
        out += [mode.title()+' Analysis Report', 'Report Command:report_timing -'+mode+' -max_paths 256 -max_common_paths 16']
        for endpoint in ('clk','ctl','data0','data1','data2','data3'):
            name='ethernet_management_tx_'+('data_'+endpoint[-1] if endpoint.startswith('data') else endpoint)+'_obuf'
            for edge in ('R','F'):
                for polarity in ((edge,) if endpoint=='clk' else ('R','F')):
                    if (mode,endpoint,edge,polarity)==missing:continue
                    latency=base+(clock_fall if edge=='F' else 0)
                    if endpoint!='clk':latency+=.1+(.02 if polarity=='F' else -.02)+data_extra
                    phase=4. if serialized and edge=='F' else 0.
                    launch='R' if phase else edge;active=0. if launch=='R' else 4.
                    arrival=active+phase+latency
                    tcl=3. if mode=='setup' else 1.;net=2. if mode=='setup' else 1.
                    tco=1.+phase;q=active+tcl+net+tco
                    out.append(f'''Path Summary:
Slack : 1.0
Data Arrival Time : {arrival:.3f}
Data Required Time : 16
From : ODDR_1
To : {name}
Launch Clk : eth_tx_clk:[{launch}]
Latch Clk : eth_tx_clk:[R]
Data Arrival Path:
 {active:.3f} {active:.3f} active clock edge time
 {active+tcl:.3f} {tcl:.3f} tCL RR 10 PLL_B[2] ae350_eth_pll/CLKOUT0
 {active+tcl+net:.3f} {net:.3f} tNET RR 1 IOB1[A] ODDR_1/CLK
 {q:.3f} {tco:.3f} tC2Q R{polarity} 1 IOB1[A] ODDR_1/Q0
 {arrival:.3f} {arrival-q:.3f} tINS {polarity}{polarity} 1 IOB1[A] {name}/O
Data Required Path:
 15.965 -0.035 tUnc
Path Statistics:
''')
    return '\n'.join(out)

class EthernetTimingTests(unittest.TestCase):
    def test_routed_pairs_and_corners(self):
        result=qualify_tx(fixture())
        self.assertEqual(result['model'],'routed-ddr-skew-v1')
        self.assertAlmostEqual(result['worst_abs_skew_ns'],.12)
        self.assertGreater(result['worst_skew_margin_ns'],0)
        self.assertEqual(set(result['corners']),{'setup','hold'})
    def test_missing_lane_edge_or_polarity_rejected(self):
        for missing in (('setup','data2','F','F'),('hold','clk','R','R'),('hold','ctl','R','F')):
            with self.assertRaisesRegex(ValueError,'Missing routed TX coverage'):qualify_tx(fixture(missing=missing))
    def test_bad_skew_rejected(self):
        for extra in (.6,-.8,4.):
            with self.assertRaisesRegex(ValueError,'skew exceeds'):qualify_tx(fixture(data_extra=extra))
    def test_clock_duty_rejected(self):
        with self.assertRaisesRegex(ValueError,'duty outside'):qualify_tx(fixture(clock_fall=.5))
    def test_only_explicit_c2q_serialization_phase_is_removed(self):
        self.assertEqual(qualify_tx(fixture(serialized=True))['worst_abs_skew_ns'],.12)
        with self.assertRaisesRegex(ValueError,'skew exceeds'):qualify_tx(fixture(data_extra=4.))
    def test_builder_requires_the_io_gate_even_with_zero_tool_violations(self):
        from scripts.modular.machines.mega138k_ae350.ae350 import timing_report
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);pnr=root/'gateware/impl/pnr';pnr.mkdir(parents=True)
            (pnr/'project_tr_content.html').write_text('<p>Numbers of Setup Violated Endpoints 0</p><p>Numbers of Hold Violated Endpoints 0</p>')
            report=pnr/'project.tr';report.write_text(fixture()+'\nMPW Summary:\nSlack:0.1\n')
            contract=root/'memory-map.json'
            contract.write_text(json.dumps({'ethernet':{'packets':{'tx_timing':'routed-ddr-skew-v1'}}}))
            self.assertEqual(timing_report(root)['rgmii_tx']['model'],'routed-ddr-skew-v1')
            report.write_text(fixture(data_extra=.8)+'\nMPW Summary:\nSlack:0.1\n')
            with self.assertRaisesRegex(ValueError,'skew exceeds'):timing_report(root)
            report.write_text(fixture()+'\nMPW Summary:\nSlack:0.1\n');contract.write_text('{}')
            with self.assertRaisesRegex(ValueError,'mandatory'):timing_report(root)
    def test_incomplete_reports_and_wrong_clock_rejected(self):
        with self.assertRaisesRegex(ValueError,'exhaustive'):qualify_tx(fixture().replace('max_common_paths 16','max_common_paths 1'))
        with self.assertRaisesRegex(ValueError,'125MHz'):qualify_tx(fixture().replace('8.000 125.000','16.000 62.500'))
        with self.assertRaisesRegex(ValueError,'actual PLL-to-pad'):qualify_tx(fixture().replace('ae350_eth_pll/CLKOUT0','dummy'))

if __name__=='__main__':unittest.main()
