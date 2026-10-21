"""Measure PWM periods/duty endpoints and writes during a running period."""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen import Module, ClockDomain
from migen.fhdl import verilog
from gateware.machines.primer20k_vexriscv.peripherals.video import BacklightPWM

TB=r'''
module tb;
reg clk=0,rst=1;
reg [6:0] duty=100;
wire pin;
wire [6:0] phase;
always #5 clk=~clk;
pwm dut(.clk(clk),.rst(rst),.duty(duty),.pin(pin),.phase(phase));
integer d,i,highs,edges,previous;
task measure(input integer value);
begin
 duty=value;
 wait(phase==99);wait(phase==0);repeat(2)@(negedge clk);
 highs=0;edges=0;previous=pin;
 for(i=0;i<200;i=i+1)begin
  if(pin!==0 && pin!==1)$fatal(1,"unknown PWM");
  highs=highs+pin;
  if(pin!=previous)edges=edges+1;
  previous=pin;@(negedge clk);
 end
 if(pin!=previous)edges=edges+1;
 if(highs!==(value>=100?200:value*2))$fatal(1,"duty %d high clocks %d",value,highs);
 if(edges!==(value==0 || value>=100?0:2))$fatal(1,"PWM edges %d duty %d",edges,value);
end endtask
initial begin
 repeat(4)@(negedge clk);rst=0;
 if(pin!==1)$fatal(1,"reset must be full brightness");
 for(d=0;d<128;d=d+1)measure(d);
 measure(50);
 wait(phase==0);wait(phase==25);@(negedge clk);duty=75;
 // Current period must retain 50%, despite a write at 25% of its duration.
 wait(phase==60);repeat(2)@(negedge clk);
 if(pin!==0)$fatal(1,"midperiod update changed pulse width");
 measure(75);
 // The PWM is synchronous: reset restores the full-on endpoint.
 rst=1;repeat(3)@(negedge clk);if(pin!==1)$fatal(1,"reset output");
 $display("Backlight PWM PASS: 200 clocks/period =45kHz at9MHz, all128 CSR values, exact0..100 percent, saturated100..127, one pulse/period, boundary updates, full-on reset");
 $finish;
end
initial begin #20000000;$fatal(1,"timeout");end
endmodule
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iverilog',required=True)
    a=p.parse_args();compiler=Path(a.iverilog).resolve()
    runner=compiler.with_name('vvp.exe' if compiler.suffix=='.exe' else 'vvp')
    root=Path(__file__).resolve().parents[2]/'build'/'lcd-pwm'
    root.mkdir(parents=True,exist_ok=True)
    dut=Module();dut.clock_domains.cd_sys=ClockDomain('sys')
    dut.submodules.pwm=pwm=BacklightPWM()
    ports={'clk':dut.cd_sys.clk,'rst':dut.cd_sys.rst,'duty':pwm.duty,'pin':pwm.output,'phase':pwm.phase}
    for name,signal in ports.items():signal.name_override=name
    with tempfile.TemporaryDirectory(prefix='rtl-',dir=root) as tmp:
        out=Path(tmp).resolve()
        env=dict(os.environ,PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''),
                 TMP=out.as_posix(),TEMP=out.as_posix(),TMPDIR=out.as_posix())
        (out/'pwm.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='pwm')))
        (out/'tb.v').write_text(TB)
        subprocess.run([str(compiler),'-g2012','-s','tb','-o',str(out/'sim'),str(out/'pwm.v'),str(out/'tb.v')],env=env,check=True)
        subprocess.run([str(runner),str(out/'sim')],env=env,check=True,timeout=30)

if __name__=='__main__':main()
