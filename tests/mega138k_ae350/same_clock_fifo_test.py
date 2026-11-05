"""Buffered DDR queues retain capacity, ordering and stalled output data."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen.fhdl import verilog
from gateware.machines.mega138k_ae350.ddr3_ahb import SameClockFIFO
iv=shutil.which('iverilog') or str(next(Path('/nix/store').glob('*iverilog*/bin/iverilog')))
vvp=str(Path(iv).with_name('vvp'))
for depth in (2,4):
    fifo=SameClockFIFO(390,depth);ios=set()
    for name in ('din','dout','we','re','readable','writable','level'):
        signal=getattr(fifo,name);signal.name_override='f_'+name;ios.add(signal)
    rtl=str(verilog.convert(fifo,ios=ios,name='dut'))
    tb=r'''
module tb;
reg clk=0,rst=1,we=0,re=0;always #5 clk=~clk;
reg[389:0]din=0;wire[389:0]dout;wire readable,writable;wire[2:0]level;
reg[389:0]expected[0:2047],stalled_data;
integer head=0,tail=0,count=0,i;reg stalled=0;
dut d(.write_clk(clk),.write_rst(rst),.f_we(we),.f_re(re),.f_din(din),
.f_dout(dout),.f_readable(readable),.f_writable(writable),.f_level(level));
always @(posedge clk)if(!rst)begin
 if(stalled&&dout!==stalled_data)$fatal(1,"stalled data changed");
 stalled=readable&&!re;stalled_data=dout;
 if(re&&readable)begin
  if(!count||dout!==expected[head])$fatal(1,"FIFO ordering/data mismatch");
  head=head+1;count=count-1;
 end
 if(we&&writable)begin expected[tail]=din;tail=tail+1;count=count+1;end
 if(count>DEPTH)$fatal(1,"logical FIFO capacity exceeded");
 #1;if(level!==count)$fatal(1,"level mismatch %d %d",level,count);
end
initial begin
 repeat(5)@(negedge clk);rst=0;we=1;re=0;
 for(i=0;i<DEPTH*4;i=i+1)begin @(negedge clk);din={6'h2a,{12{i[31:0]}}};end
 if(count!=DEPTH||writable)$fatal(1,"FIFO capacity not available");
 for(i=0;i<500;i=i+1)begin
  @(negedge clk);we=$random;re=$random;din={6'h2a,{12{i[31:0]}}};
 end
 @(negedge clk);we=0;re=1;repeat(20)@(negedge clk);
 if(tail<50)$fatal(1,"FIFO made no progress");
 if(count||readable)$fatal(1,"FIFO did not drain");
 $display("PASS buffered DDR FIFO capacity/order/backpressure depth=DEPTH");$finish;
end
endmodule
'''.replace('DEPTH',str(depth)).replace('wire[2:0]level;', 'wire[%d:0]level;' % (len(fifo.level)-1))
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);(root/'dut.v').write_text(rtl);(root/'tb.v').write_text(tb)
        subprocess.run([iv,'-g2012','-s','tb','-o',str(root/'sim'),str(root/'dut.v'),str(root/'tb.v')],check=True,capture_output=True)
        subprocess.run([vvp,str(root/'sim')],check=True,timeout=30)
