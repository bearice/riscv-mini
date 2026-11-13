"""Generated-Verilog BilinearFilter against the LERP reference, including zero weights."""
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from migen import ClockDomain, Module
from migen.fhdl import verilog
from gateware.machines.mega138k_ae350.peripherals.gfx2d import BilinearFilter

COUNT = 512
LATENCY = BilinearFilter.LATENCY

TB = r'''
module tb;
reg sys_clk=0,sys_rst=0;
always #5 sys_clk=~sys_clk;
reg [145:0] vin [0:%(count)d-1];
reg [31:0] vexp [0:%(count)d-1];
reg [31:0] p00=0,p10=0,p01=0,p11=0;
reg [8:0] wu=0,wv=0;
wire [31:0] out;
dut dut(.*);
integer i,errors=0;
initial begin
  $readmemh("in.hex",vin);
  $readmemh("exp.hex",vexp);
  for(i=0;i<%(count)d+%(latency)d;i=i+1)begin
    if(i<%(count)d){wv,wu,p11,p01,p10,p00}=vin[i];
    @(posedge sys_clk);#1;
    if(i>=%(latency)d-1 && i-%(latency)d+1<%(count)d && out!==vexp[i-%(latency)d+1])begin
      if(errors<8)$display("vector %%0d: got %%08x expected %%08x",i-%(latency)d+1,out,vexp[i-%(latency)d+1]);
      errors=errors+1;
    end
  end
  if(errors)$fatal(1,"%%0d mismatches",errors);
  $display("PASS bilinear");
  $finish;
end
endmodule
''' % {'count': COUNT, 'latency': LATENCY}


def lerp(s, d, w):
    return (((s - d) * w + (d << 8) + 128) >> 8) & 0xFF


def reference(p00, p10, p01, p11, wu, wv):
    out = 0
    for c in range(4):
        ch = [(p >> (8 * c)) & 0xFF for p in (p00, p10, p01, p11)]
        top = lerp(ch[1], ch[0], wu)
        bot = lerp(ch[3], ch[2], wu)
        out |= lerp(bot, top, wv) << (8 * c)
    return out


class BilinearVerilogTest(unittest.TestCase):
    def test_generated_verilog_matches_reference(self):
        iv, vvp = shutil.which('iverilog'), shutil.which('vvp')
        self.assertTrue(iv and vvp, 'iverilog/vvp required')
        m = Module()
        m.clock_domains.cd_sys = ClockDomain('sys')
        m.submodules.bilinear = f = BilinearFilter()
        ports = {'sys_clk': m.cd_sys.clk, 'sys_rst': m.cd_sys.rst,
                 'p00': f.p00, 'p10': f.p10, 'p01': f.p01, 'p11': f.p11,
                 'wu': f.wu, 'wv': f.wv, 'out': f.out}
        for name, signal in ports.items():
            signal.name_override = name

        rng = random.Random(0x6C)
        weight = lambda a: a + (a >> 7)
        vectors = []
        for i in range(COUNT):
            p = [rng.getrandbits(32) for _ in range(4)]
            if i < 64:
                wu = wv = 0
            elif i < 128:
                wu, wv = weight(255), weight(rng.randrange(256))
            else:
                wu, wv = weight(rng.randrange(256)), weight(rng.randrange(256))
            vectors.append((p, wu, wv))
        # Zero weights must return P00 exactly.
        for p, wu, wv in vectors[:64]:
            self.assertEqual(reference(*p, wu, wv), p[0])

        with tempfile.TemporaryDirectory(prefix='ae350-gfx2d-bilinear-') as directory:
            directory = Path(directory)
            (directory / 'dut.v').write_text(str(verilog.convert(m, ios=set(ports.values()), name='dut')))
            (directory / 'tb.v').write_text(TB)
            (directory / 'in.hex').write_text(''.join(
                '%037x\n' % ((wv << 137) | (wu << 128) | (p[3] << 96) | (p[2] << 64) | (p[1] << 32) | p[0])
                for p, wu, wv in vectors))
            (directory / 'exp.hex').write_text(''.join(
                '%08x\n' % reference(*p, wu, wv) for p, wu, wv in vectors))
            compiled = directory / 'test.vvp'
            subprocess.run([iv, '-g2012', '-s', 'tb', '-o', str(compiled),
                            str(directory / 'dut.v'), str(directory / 'tb.v')], check=True)
            result = subprocess.run([vvp, str(compiled)], text=True, capture_output=True,
                                    timeout=60, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS bilinear', result.stdout)


if __name__ == '__main__':
    unittest.main()
