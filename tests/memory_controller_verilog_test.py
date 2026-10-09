"""Emitted full memory controller: client stalls never park shared arbitration."""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from migen.fhdl import verilog
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.memory import SharedMemoryController

TB=r"""
module tb;
reg sys_clk=0,sys_rst=1;
always #5 sys_clk=~sys_clk;
reg cyc=0,stb=0,we=0;reg [29:0] adr=0;reg [31:0] dat_w=0;
wire ack;wire [31:0] dat_r;
wire nvalid,nwe,nwvalid,nrready;wire [22:0] naddr;wire [127:0] nwdata;wire [15:0] nmask;
reg nready=1,nwready=0,nrvalid=0;reg [127:0] nrdata=0;
reg vvalid=0,vready=0;reg [22:0] vaddr=0;wire vcmdready,vrvalid,vlast;wire [15:0] vrdata;
reg svalid=0,swvalid=0,sdready=0;reg [22:0] saddr=4;reg [31:0] swdata=0;reg [3:0] swmask=15;
wire scmdready,swready,sdvalid;
reg rvalid=0,rrready=0;wire rcmdready,rrvalid;wire [31:0] rrdata;
reg [127:0] mem[0:63];integer i,j,pending=0,delay=0;reg [22:0] saved;reg saved_we;
controller dut(.sys_clk(sys_clk),.sys_rst(sys_rst),.cyc(cyc),.stb(stb),.we(we),.adr(adr),.dat_w(dat_w),.ack(ack),.dat_r(dat_r),
 .nvalid(nvalid),.nready(nready),.nwe(nwe),.naddr(naddr),.nwvalid(nwvalid),.nwready(nwready),.nwdata(nwdata),.nmask(nmask),.nrvalid(nrvalid),.nrready(nrready),.nrdata(nrdata),
 .vvalid(vvalid),.vcmdready(vcmdready),.vaddr(vaddr),.vready(vready),.vrvalid(vrvalid),.vrdata(vrdata),.vlast(vlast),
 .svalid(svalid),.scmdready(scmdready),.saddr(saddr),.swvalid(swvalid),.swready(swready),.swdata(swdata),.swmask(swmask),.sdvalid(sdvalid),.sdready(sdready),
 .rvalid(rvalid),.rcmdready(rcmdready),.rrvalid(rrvalid),.rrready(rrready),.rrdata(rrdata));
always @(posedge sys_clk)begin
 if(sys_rst)begin pending<=0;nready<=1;nwready<=0;nrvalid<=0;end
 else begin
  if(nvalid && nready)begin
   saved<=naddr;saved_we<=nwe;pending<=1;delay<=6;nready<=0;
  end
  if(pending)begin
   if(delay>0)delay<=delay-1;
   else if(saved_we)begin
    nwready<=1;
    if(nwvalid && nwready)begin
     if(saved<64)for(j=0;j<16;j=j+1)if(nmask[j])mem[saved][j*8+:8]<=nwdata[j*8+:8];
     nwready<=0;pending<=0;nready<=1;
    end
   end else if(!nrvalid)begin nrvalid<=1;nrdata<=saved<64?mem[saved]:0;end
   else if(nrready)begin nrvalid<=0;pending<=0;nready<=1;end
  end
 end
end
task send;
 input [31:0] value;input [3:0] mask;
 begin
  @(negedge sys_clk);swvalid=1;swdata=value;swmask=mask;
  @(posedge sys_clk);while(!swready)@(posedge sys_clk);
  @(negedge sys_clk);swvalid=0;
 end
endtask
initial begin
 for(i=0;i<64;i=i+1)for(j=0;j<16;j=j+1)mem[i][j*8+:8]=(i*16+j)&255;
 repeat(4)@(negedge sys_clk);sys_rst=0;
 repeat(300)@(negedge sys_clk);
 vvalid=1;@(posedge sys_clk);while(!vcmdready)@(posedge sys_clk);@(negedge sys_clk);vvalid=0;
 wait(vrvalid);#1;if(vrdata!==16'h0100)$fatal(1,"first pixel %h",vrdata);
 @(negedge sys_clk);cyc=1;stb=1;adr=32'h000000c0>>2;
 @(posedge sys_clk);while(!ack)@(posedge sys_clk);
 if(dat_r!==32'hc3c2c1c0)$fatal(1,"CPU read while LCD stalled");
 @(negedge sys_clk);cyc=0;stb=0;
 svalid=1;@(posedge sys_clk);while(!scmdready)@(posedge sys_clk);@(negedge sys_clk);svalid=0;
 send(32'h11223344,15);
 @(negedge sys_clk);rvalid=1;@(posedge sys_clk);while(!rcmdready)@(posedge sys_clk);@(negedge sys_clk);rvalid=0;
 wait(rrvalid);#1;if(rrdata!==32'h13121110)$fatal(1,"SD read while write partial/LCD stalled");
 if(mem[4][31:0]!==32'h43424140)$fatal(1,"partial write committed early");
 @(negedge sys_clk);rrready=1;@(posedge sys_clk);@(negedge sys_clk);rrready=0;
 send(32'h01020304,5);send(32'haabbccdd,15);
 wait(sdvalid);
 if(mem[4]!==128'h4f4e4d4caabbccdd4702450411223344)$fatal(1,"tail/mask/commit %h",mem[4]);
 @(negedge sys_clk);sdready=1;@(posedge sys_clk);@(negedge sys_clk);sdready=0;
 for(i=0;i<8;i=i+1)begin
  wait(vrvalid);#1;if(vrdata!==((2*i+1)<<8|2*i))$fatal(1,"pixel lane %d %h",i,vrdata);
  if(vlast!==(i==7))$fatal(1,"last lane %d",i);
  repeat(3)@(negedge sys_clk);
  vready=1;@(posedge sys_clk);@(negedge sys_clk);vready=0;
 end
 $display("Shared memory controller RTL PASS: stalled LCD, partial SD write, CPU/SD progress, lanes/masks/commit");$finish;
end
initial begin #200000;$fatal(1,"timeout");end
endmodule
"""

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog',required=True)
    args=parser.parse_args();compiler=Path(args.iverilog).resolve()
    runner=compiler.with_name('vvp.exe' if compiler.suffix=='.exe' else 'vvp')
    wb=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    raw=LiteDRAMNativePort('both',23,128);dut=SharedMemoryController(wb,raw)
    dut.comb += [wb.sel.eq(15),dut.video.cmd.we.eq(0),dut.video.cmd.count.eq(8),
                 dut.sd_write.cmd.we.eq(1),dut.sd_write.cmd.count.eq(3),
                 dut.sd_read.cmd.addr.eq(1),dut.sd_read.cmd.we.eq(0),dut.sd_read.cmd.count.eq(1)]
    ports={'cyc':wb.cyc,'stb':wb.stb,'we':wb.we,'adr':wb.adr,'dat_w':wb.dat_w,'dat_r':wb.dat_r,'ack':wb.ack,
        'nvalid':raw.cmd.valid,'nready':raw.cmd.ready,'nwe':raw.cmd.we,'naddr':raw.cmd.addr,
        'nwvalid':raw.wdata.valid,'nwready':raw.wdata.ready,'nwdata':raw.wdata.data,'nmask':raw.wdata.we,
        'nrvalid':raw.rdata.valid,'nrready':raw.rdata.ready,'nrdata':raw.rdata.data,
        'vvalid':dut.video.cmd.valid,'vcmdready':dut.video.cmd.ready,'vaddr':dut.video.cmd.addr,
        'vready':dut.video.rdata.ready,'vrvalid':dut.video.rdata.valid,'vrdata':dut.video.rdata.data,'vlast':dut.video.rdata.last,
        'svalid':dut.sd_write.cmd.valid,'scmdready':dut.sd_write.cmd.ready,'saddr':dut.sd_write.cmd.addr,
        'swvalid':dut.sd_write.wdata.valid,'swready':dut.sd_write.wdata.ready,
        'swdata':dut.sd_write.wdata.data,'swmask':dut.sd_write.wdata.we,'sdvalid':dut.sd_write.done.valid,'sdready':dut.sd_write.done.ready,
        'rvalid':dut.sd_read.cmd.valid,'rcmdready':dut.sd_read.cmd.ready,'rrvalid':dut.sd_read.rdata.valid,
        'rrready':dut.sd_read.rdata.ready,'rrdata':dut.sd_read.rdata.data}
    for name,signal in ports.items():signal.name_override=name
    with tempfile.TemporaryDirectory(prefix='memory-controller-rtl-') as directory:
        output=Path(directory).resolve()
        env=dict(os.environ,PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''),TMP=output.as_posix(),TMPDIR=output.as_posix())
        (output/'controller.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='controller')))
        (output/'tb.v').write_text(TB)
        subprocess.run([str(compiler),'-g2012','-s','tb','-o',(output/'sim').as_posix(),(output/'controller.v').as_posix(),(output/'tb.v').as_posix()],env=env,check=True)
        subprocess.run([str(runner),(output/'sim').as_posix()],env=env,check=True,timeout=30)
if __name__=='__main__':main()
