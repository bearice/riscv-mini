import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from mini_ops.mega138k_ae350.ae350_ethernet_suite import judge_mdio,judge_packets
class EthernetContractTests(unittest.TestCase):
    def test_discovery_does_not_claim_packets(self):
        w=[0]*160;w[0]=0x45544801;w[159]=0x455448ff;w[1]=25000000;w[2]=1
        w[17]=0x001cc916;w[81]=0x24
        r=judge_mdio(w);self.assertFalse(r['failures']);self.assertFalse(r['packets_verified'])
        self.assertTrue(r['phys'][0]['link_up'])
        w[49]=1;self.assertTrue(judge_mdio(w)['failures'])
    def test_packet_evidence_requires_wire_clock_guards_and_no_errors(self):
        w=[0]*160;w[15]=3;w[113]=125000000
        w[123]=0x444d4132;w[124]=w[125]=47;w[128]=3
        w[6]=49;w[11]=2
        w[8],w[9],w[10]=1,7,39;w[7]=w[118]=w[119]=47
        wire=dict(passed=True,icmp_cases=7,udp_cases=39)
        self.assertFalse(judge_packets(w,wire))
        for index,value in ((5,1),(12,1),(13,1),(14,1),(114,1),(115,1),(113,25000000),(119,46),(11,0),(6,48),(123,0),(124,48),(128,7),(129,1),(130,2)):
            bad=w.copy();bad[index]=value
            self.assertTrue(judge_packets(bad,wire),(index,value))
        self.assertTrue(judge_packets(w,dict(passed=False)))
    def test_missing_completion_or_phy_rejected(self):
        self.assertTrue(judge_mdio([])['failures']);self.assertTrue(judge_mdio([0]*160)['failures'])
if __name__=='__main__':unittest.main()
