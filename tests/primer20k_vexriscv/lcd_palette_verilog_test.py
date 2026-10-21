"""Verify INDEX8 DMA, synchronous palette lookup, bank+slot commit and black viewport."""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from migen import Module
from migen.fhdl import verilog
from litedram.common import LiteDRAMNativePort
from gateware.machines.primer20k_vexriscv.memory.ports import MemoryPort, PortBuffer
from gateware.machines.primer20k_vexriscv.peripherals.video import RGBLCD

TB = r"""
module tb;
reg [1:0] commit=0;
reg [8:0] palette_address=0;
reg [15:0] palette_data=0;
reg palette_we=0;
wire palette_active;
wire [15:0] palette_readback;
reg sys_clk=0,video_clk=0,sys_rst=1,video_rst=1,enable=0;
always #5 sys_clk=~sys_clk;
always #33 video_clk=~video_clk;
wire cmd_valid,cmd_we,rready;
wire [22:0] addr;
reg cmd_ready=1,rvalid=0;
reg [127:0] rdata=0;
wire [15:0] pixel;
wire [9:0] h;
wire [8:0] v;
wire de,underflow,scan_enable;
wire [31:0] completed;
integer pending=0,delay=0,requests=0,k,j,word_index,pixels=0,expected;
reg [22:0] saved;
lcd dut(.sys_clk(sys_clk),.sys_rst(sys_rst),.video_clk(video_clk),.video_rst(video_rst),
 .enable(enable),.select_slot(1'b0),.base0(32'h07e00000),.base1(32'h07e20000),
 .format_indexed(1'b1),.commit(commit),.palette_address(palette_address),
 .palette_data(palette_data),.palette_we(palette_we),.palette_active(palette_active),.palette_readback(palette_readback),
 .window_start(20'd575613),.window_end(19'd256445),
 .wrap_start(32'h07e00000),.wrap_end(32'h0),.cmd_valid(cmd_valid),.cmd_ready(cmd_ready),
 .addr(addr),.cmd_we(cmd_we),.rvalid(rvalid),.rready(rready),.rdata(rdata),
 .pixel(pixel),.h(h),.v(v),.de(de),.underflow(underflow),.scan_enable(scan_enable),.completed(completed));
always @(posedge sys_clk)begin
 if(sys_rst)begin pending<=0;rvalid<=0;cmd_ready<=1;end
 else begin
  if(cmd_valid && cmd_ready)begin
   if(cmd_we)$fatal(1,"LCD issued write");

   if(addr !== (requests<16320 ? 23'h7e0000 : 23'h7e2000)+(requests%8160))$fatal(1,"burst order %h",addr);
   requests<=requests+1;saved<=addr;pending<=1;delay<=5;cmd_ready<=0;
  end
  if(pending)begin
   if(delay>0)delay<=delay-1;
   else if(!rvalid)begin
    for(k=0;k<8;k=k+1)begin
     word_index=(saved-(saved>=23'h7e2000 ? 23'h7e2000 : 23'h7e0000))*16+k*2;
     rdata[16*k+:16]<=((word_index+1+(saved>=23'h7e2000 ? 37:0))%256)*256+((word_index+(saved>=23'h7e2000 ? 37:0))%256);
    end
    rvalid<=1;
   end else if(rready)begin rvalid<=0;pending<=0;cmd_ready<=1;end
  end
 end
end
always @(negedge video_clk)begin
 if(!video_rst && scan_enable)begin
  if(underflow)$fatal(1,"LCD underflow at %d,%d",h,v);
  if(de)begin
   expected=(((v-14)*480+h-45+(pixels>=130560 ? 37:0))%256)*257;
   if(pixels>=130560)expected=expected^16'hc318;
   if(h<125 || h>=445 || v<50 || v>=250)expected=0;
   if(palette_active!==(pixels>=130560))$fatal(1,"bank/slot commit mismatch");
   if(pixel!==expected)$fatal(1,"pixel %d,%d: %h != %h",h,v,pixel,expected);
   pixels=pixels+1;
  end
 end
end
initial begin
 repeat(4)@(negedge sys_clk);sys_rst=0;
 for(j=0;j<512;j=j+1)begin
  @(negedge sys_clk);palette_address=j;palette_data=(j%256)*257 ^ (j>=256 ? 16'hc318:0);palette_we=1;
 end
 @(negedge sys_clk);palette_we=0;
 for(j=0;j<512;j=j+1)begin
  palette_address=j;@(negedge sys_clk);
  if(palette_readback!==((j%256)*257 ^ (j>=256 ? 16'hc318:0)))$fatal(1,"palette readback %d",j);
 end
 enable=1;video_rst=0;
 wait(completed>=2);@(negedge sys_clk);commit=3;
 wait(completed>=3);
 if(pixels<261000)$fatal(1,"too few pixels %d",pixels);
 if(requests!=24480)$fatal(1,"wrong burst count %d",requests);
 $display("INDEX8 LCD RTL PASS: 256 colors, low/high index packing, half DMA bursts, atomic bank+slot switch, black viewport, no underflow");$finish;
end
initial begin #40000000;$fatal(1,"timeout");end
endmodule
"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog', required=True)
    args=parser.parse_args()
    compiler=Path(args.iverilog).resolve()
    runner=compiler.with_name('vvp.exe' if compiler.suffix=='.exe' else 'vvp')
    port=MemoryPort('read',data_width=16)
    native=LiteDRAMNativePort('read',23,128)
    dut=Module()
    dut.submodules.buffer=PortBuffer(port,native)
    dut.submodules.video=video=RGBLCD(port,None)
    ports={'enable':video._enable.storage,'select_slot':video._select.storage,
        'base0':video._base0.storage,'base1':video._base1.storage,
        'format_indexed':video._format.storage,'commit':video._commit.storage,
        'palette_address':video._palette_address.storage,'palette_data':video._palette_data.storage,
        'palette_we':video._palette_data.re,'palette_readback':video._palette_readback.status,'palette_active':video._palette_active.status,
        'window_start':video._window_start.storage,'window_end':video._window_end.storage,
        'wrap_start':video._wrap_start.storage,'wrap_end':video._wrap_end.storage,
        'cmd_valid':native.cmd.valid,'cmd_ready':native.cmd.ready,'addr':native.cmd.addr,
        'cmd_we':native.cmd.we,'rvalid':native.rdata.valid,'rready':native.rdata.ready,
        'rdata':native.rdata.data,'pixel':video.pixel,'h':video.scan.h,'v':video.scan.v,
        'de':video.scan.de,'underflow':video.scan.underflow,'scan_enable':video.scan.enable,
        'completed':video._completed.status}
    for name,signal in ports.items():signal.name_override=name
    with tempfile.TemporaryDirectory(prefix='lcd-memory-rtl-') as directory:
        output=Path(directory).resolve()
        env=dict(os.environ,PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''),
                 TMP=output.as_posix(),TMPDIR=output.as_posix())
        (output/'lcd.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='lcd')))
        (output/'tb.v').write_text(TB)
        subprocess.run([str(compiler),'-g2012','-s','tb','-o',(output/'sim').as_posix(),
                        (output/'lcd.v').as_posix(),(output/'tb.v').as_posix()],env=env,check=True)
        subprocess.run([str(runner),(output/'sim').as_posix()],env=env,check=True,timeout=60)


if __name__=='__main__':main()
