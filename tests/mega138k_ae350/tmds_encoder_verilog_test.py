"""Compare TMDS symbols with an independent integer encoder and fixed latency."""
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hdmi_migen_test_support import encoder_verilog
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]


def encode(value,de,control,disparity):
    if not de:return (0b1101010100,0b0010101011,0b0101010100,0b1010101011)[control],0
    xnor=value.bit_count()>4 or (value.bit_count()==4 and not value&1)
    previous=value&1
    word=previous
    for bit in range(1,8):
        previous=previous^((value>>bit)&1)^xnor
        word|=previous<<bit
    flag=int(not xnor)
    ones=word.bit_count()
    zeros=8-ones
    if disparity==0 or ones==zeros:
        symbol=((1-flag)<<9)|(flag<<8)|(word if flag else word^255)
        disparity+=(ones-zeros) if flag else (zeros-ones)
    elif (disparity>0 and ones>zeros) or (disparity<0 and zeros>ones):
        symbol=512|(flag<<8)|(word^255)
        disparity+=zeros-ones+2*flag
    else:
        symbol=(flag<<8)|word
        disparity+=ones-zeros-2*(1-flag)
    return symbol,disparity


class EncoderTest(unittest.TestCase):
    def test_symbols_and_latency(self):
        rng=random.Random(771)
        samples=[(0,0,0)]*8
        samples += [(i,1,0) for i in range(256)]
        samples += [(rng.randrange(256),int(i%13<9),rng.randrange(4)) for i in range(2048)]
        samples += [(0,0,i%4) for i in range(16)]
        expected=[];disparity=0
        for value,de,control in samples:
            symbol,disparity=encode(value,de,control,disparity)
            expected.append(symbol)
        with tempfile.TemporaryDirectory(prefix='ae350-tmds-') as tmp:
            tmp=Path(tmp)
            (tmp/'inputs.hex').write_text(''.join('%03x\n'%(v|(de<<8)|(c<<9)) for v,de,c in samples))
            (tmp/'expected.hex').write_text(''.join('%03x\n'%v for v in expected))
            tb=r'''
`timescale 1ns/1ps
module tb;
reg clk=0,resetn=0,de=0;
reg [1:0] ctrl=0;
reg [7:0] din=0;
wire [9:0] dout;
always #5 clk=~clk;
tmds_encoder dut(.*);
reg [10:0] samples[0:COUNT-1];
reg [9:0] expected[0:COUNT-1];
integer i;
initial begin
 $readmemh("inputs.hex",samples);$readmemh("expected.hex",expected);
 repeat(5)@(negedge clk);resetn=1;
 for(i=0;i<COUNT;i=i+1)begin
  @(negedge clk);{ctrl,de,din}=samples[i];
  @(posedge clk);#0.01;
  if(i>=2 && dout!==expected[i-2])$fatal(1,"symbol %0d got%x expected%x",i,dout,expected[i-2]);
 end
 $display("PASS TMDS symbols and latency");$finish;
end
endmodule
'''.replace('COUNT',str(len(samples)))
            (tmp/'tb.v').write_text(tb)
            (tmp/'encoder.v').write_text(encoder_verilog())
            iv,vvp=shutil.which('iverilog'),shutil.which('vvp')
            self.assertTrue(iv and vvp)
            subprocess.run([iv,'-g2012','-s','tb','-o',str(tmp/'run'),str(tmp/'tb.v'),str(tmp/'encoder.v')],check=True)
            result=subprocess.run([vvp,str(tmp/'run')],cwd=tmp,text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS TMDS',result.stdout)


if __name__=='__main__':unittest.main()
