"""Verify CSR skip: tight DMA, black margins, configurable geometry and underrun recovery."""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Module
from migen.fhdl import verilog
from litedram.common import LiteDRAMNativePort
from gateware.machines.primer20k_vexriscv.memory.ports import MemoryPort, PortBuffer
from gateware.machines.primer20k_vexriscv.peripherals.video import RGBLCD

TB = r"""
module tb;
reg [1:0] commit=0; reg [6:0] backlight_control=100; wire backlight,backlight_level;
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
lcd dut(.backlight_control(backlight_control),.backlight(backlight),.backlight_level(backlight_level),.sys_clk(sys_clk),.sys_rst(sys_rst),.video_clk(video_clk),.video_rst(video_rst),
 .enable(enable),.select_slot(1'b0),.base0(32'h07e00000),.base1(32'h07e20000),
 .skip_mode(1'b1),.format_indexed(1'b1),.commit(commit),.palette_address(palette_address),
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

   if(addr !== (requests<8000 ? 23'h7e0000 : 23'h7e2000)+(requests%4000))$fatal(1,"burst order %h",addr);
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
   expected=(((v-50)*320+h-125+(pixels>=130560 ? 37:0))%256)*257;
   expected=expected^16'h1234;
   if(pixels>=130560)expected=expected^16'hc318;
   if(h<125 || h>=445 || v<50 || v>=250)expected=0;
   if(palette_active!==(pixels>=130560))$fatal(1,"bank/slot commit mismatch");
   if(pixel!==expected)$fatal(1,"pixel %d,%d: %h != %h",h,v,pixel,expected);
   pixels=pixels+1;
  end
 end
end
initial begin
 repeat(4)@(negedge sys_clk);sys_rst=0;video_rst=0;
 if(backlight!==1)$fatal(1,"backlight default");
 backlight_control=0;repeat(3100)@(negedge sys_clk);if(backlight!==0 || backlight_level!==0)$fatal(1,"backlight off");
 backlight_control=100;repeat(3100)@(negedge sys_clk);if(backlight!==1 || backlight_level!==1)$fatal(1,"backlight on"); video_rst=1;
 for(j=0;j<512;j=j+1)begin
  @(negedge sys_clk);palette_address=j;palette_data=(j%256)*257 ^ (j>=256 ? 16'hc318:0) ^ 16'h1234;palette_we=1;
 end
 @(negedge sys_clk);palette_we=0;
 for(j=0;j<512;j=j+1)begin
  palette_address=j;@(negedge sys_clk);
  if(palette_readback!==((j%256)*257 ^ (j>=256 ? 16'hc318:0) ^ 16'h1234))$fatal(1,"palette readback %d",j);
 end
 enable=1;video_rst=0;
 wait(completed>=2);@(negedge sys_clk);commit=3;
 wait(completed>=3);
 wait(h==524 && v==285);@(negedge video_clk);
 if(pixels<261000)$fatal(1,"too few pixels %d",pixels);
 if(requests!=12000)$fatal(1,"wrong burst count %d",requests);
 $display("LCD skip RTL PASS: configured DMA length/order, every visible pixel and black margin, atomic bank/slot switch");$finish;
end
initial begin #40000000;$fatal(1,"timeout");end
endmodule
"""


RANGE_TB = r"""module tb;
reg sys_clk=0,video_clk=0,sys_rst=1,video_rst=1;
always #5 sys_clk=~sys_clk;always #33 video_clk=~video_clk;
reg skip_mode=1,indexed=1;
reg [31:0] base=32'h07ff0600,wrap_start=0,wrap_end=0;
reg [19:0] window_start=575613;
reg [18:0] window_end=256445;
wire valid_base;
lcd dut(.sys_clk(sys_clk),.sys_rst(sys_rst),.video_clk(video_clk),.video_rst(video_rst),
 .enable(1'b0),.select_slot(1'b0),.base0(base),.base1(base),.format_indexed(indexed),.skip_mode(skip_mode),
 .commit(2'b0),.palette_address(9'b0),.palette_data(16'b0),.palette_we(1'b0),
 .window_start(window_start),.window_end(window_end),.wrap_start(wrap_start),.wrap_end(wrap_end),
 .cmd_ready(1'b0),.rvalid(1'b0),.rdata(128'b0),.valid_base(valid_base));
task check(input expected);begin repeat(24)@(negedge sys_clk);if(valid_base!==expected)$fatal(1,"bad valid_base %h expected%d",base,expected);end endtask
initial begin
 repeat(4)@(negedge sys_clk);sys_rst=0;video_rst=0;
 check(1);
 base=32'h07ff0610;check(0);
 base=32'h00010001;check(0);
 base=32'h00010000;window_end=256444;check(0);
 window_end=256445;indexed=0;check(1);
 indexed=1;wrap_start=32'h10000;wrap_end=32'h30000;check(1);
 wrap_end=32'h10000;check(0);
 skip_mode=0;wrap_end=0;base=32'h07fe0200;check(1);
 base=32'h07fe0210;check(0);
 indexed=0;base=32'h07fc0400;check(1);
 base=32'h07fc0410;check(0);
 skip_mode=1;indexed=1;base=32'h10000;
 window_start=(1<<19)+(14<<10)+45;window_end=(15<<10)+61;check(1);
 window_end=(15<<10)+60;check(0);
 window_start=(1<<19)+(13<<10)+45;check(0);
 window_start=(1<<19)+(14<<10)+44;check(0);
 window_start=(1<<19)+(14<<10)+45;window_end=(287<<10)+61;check(0);
 window_end=(15<<10)+526;check(0);
 window_end=(14<<10)+61;check(0);
 window_end=(15<<10)+45;check(0);
 window_start=(14<<10)+45;window_end=(15<<10)+61;check(0);
 window_start=(1<<19)+(14<<10)+45;window_end=(286<<10)+525;base=32'hfffffff0;check(0);
 $display("LCD skip/legacy DDR, ring and geometry admission PASS");$finish;
end
endmodule"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog', required=True)
    parser.add_argument('--case', choices=['all', 'doom', 'small', 'full', 'rgb', 'range', 'stall', 'starve'], default='all')
    args=parser.parse_args()
    if args.case=='all':
        for case in ('doom','small','full','rgb','range','stall','starve'):
            print('CASE '+case,flush=True)
            subprocess.run([sys.executable,__file__,'--iverilog',args.iverilog,'--case',case],check=True)
        return
    global TB
    if args.case=='range': TB=RANGE_TB
    if args.case in ('small','full'):
        w,h,x,y=(160,100,65,34) if args.case=='small' else (480,272,45,14)
        bursts=w*h//16
        TB=TB.replace('575613',str(x+(y<<10)+(1<<19))).replace('256445',str(x+w+((y+h)<<10)))
        TB=re.sub(r'\b(4000|8000|12000)\b',lambda m:str({4000:bursts,8000:bursts*2,12000:bursts*3}[int(m[0])]),TB)
        TB=TB.replace('(v-50)*320+h-125',f'(v-{y})*{w}+h-{x}')
        TB=TB.replace('h<125 || h>=445 || v<50 || v>=250',f'h<{x} || h>={x+w} || v<{y} || v>={y+h}')
    if args.case=='full':
        TB=TB.replace('wait(h==524 && v==285);@(negedge video_clk);', '')
    if args.case=='rgb':
        TB=TB.replace('.format_indexed(1\'b1)', '.format_indexed(1\'b0)').replace('.select_slot(1\'b0)', '.select_slot(commit[0])')
        TB=re.sub(r'\b(4000|8000|12000)\b',lambda m:str(int(m[0])*2),TB)
        TB=TB.replace('expected=(((v-50)*320+h-125+(pixels>=130560 ? 37:0))%256)*257;',
            'expected=((v-50)*320+h-125)*2+(pixels>=130560 ? 37:0); expected=((expected+1)%256)*256+(expected%256);')
        TB=TB.replace("expected=expected^16'h1234;", '').replace("if(pixels>=130560)expected=expected^16'hc318;", '')
    if args.case=='stall':
        TB=TB.replace('delay<=5;', 'delay<=requests%61==0 ? 1800 : (requests%13==0 ? 120 : 35);')
    if args.case=='starve':
        TB=TB.replace('delay<=5;', 'delay<=requests==6000 ? 150000 : 5;')
        TB=TB.replace('pixels=0,expected;', 'pixels=0,expected,underflows_seen=0,damaged=0;')
        TB=TB.replace('if(underflow)$fatal(1,"LCD underflow at %d,%d",h,v);', 'if(underflow)begin underflows_seen=underflows_seen+1;damaged=1;end')
        TB=TB.replace('if(pixel!==expected)', 'if(damaged)expected=0; if(pixel!==expected)')
        TB=TB.replace('if(de)begin', 'if(h==0 && v==0)damaged=0; if(de)begin')
        TB=TB.replace('$display("LCD skip', 'if(underflows_seen!=1)$fatal(1,"underrun count %d",underflows_seen); $display("Recovered LCD skip')
    compiler=Path(args.iverilog).resolve()
    runner=compiler.with_name('vvp.exe' if compiler.suffix=='.exe' else 'vvp')
    port=MemoryPort('read',data_width=16)
    native=LiteDRAMNativePort('read',23,128)
    dut=Module()
    dut.submodules.buffer=PortBuffer(port,native)
    dut.submodules.video=video=RGBLCD(port,None)
    assert video._backlight.storage.reset.value==100
    ports={'enable':video._enable.storage,'select_slot':video._select.storage,
        'base0':video._base0.storage,'base1':video._base1.storage,
        'skip_mode':video._skip.storage,'format_indexed':video._format.storage,'commit':video._commit.storage,
        'palette_address':video._palette_address.storage,'palette_data':video._palette_data.storage,
        'palette_we':video._palette_data.re,'palette_readback':video._palette_readback.status,'palette_active':video._palette_active.status,
        'window_start':video._window_start.storage,'window_end':video._window_end.storage,
        'wrap_start':video._wrap_start.storage,'wrap_end':video._wrap_end.storage,
        'cmd_valid':native.cmd.valid,'cmd_ready':native.cmd.ready,'addr':native.cmd.addr,
        'cmd_we':native.cmd.we,'rvalid':native.rdata.valid,'rready':native.rdata.ready,
        'rdata':native.rdata.data,'pixel':video.pixel,'h':video.scan.h,'v':video.scan.v,
        'de':video.scan.de,'underflow':video.scan.underflow,'scan_enable':video.scan.enable,
        'valid_base':video.valid_base,'completed':video._completed.status,'backlight_control':video._backlight.storage,'backlight':video.backlight,'backlight_level':video._backlight_level.status}
    for name,signal in ports.items():signal.name_override=name
    temporary=Path(__file__).resolve().parents[1]/'build'/'lcd-csr'
    temporary.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rtl-',dir=temporary) as directory:
        output=Path(directory).resolve()
        env=dict(os.environ,PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''),
                 TMP=output.as_posix(),TMPDIR=output.as_posix())
        (output/'lcd.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='lcd')))
        (output/'tb.v').write_text(TB)
        subprocess.run([str(compiler),'-g2012','-s','tb','-o',(output/'sim').as_posix(),
                        (output/'lcd.v').as_posix(),(output/'tb.v').as_posix()],env=env,check=True)
        subprocess.run([str(runner),(output/'sim').as_posix()],env=env,check=True,timeout=60)


if __name__=='__main__':main()
