"""Actual LCD RTL: dynamic bases, invalid ranges and in-frame CSR updates."""
import os,sys,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from migen import Module,ClockDomain
from migen.fhdl import verilog
from litedram.common import LiteDRAMNativePort
from gateware.video import RGBLCD
out=ROOT/'build/video-address-rtl';out.mkdir(parents=True,exist_ok=True)
dut=Module();dut.clock_domains.cd_sys=ClockDomain('sys');dut.clock_domains.cd_video=ClockDomain('video')
port=LiteDRAMNativePort('read',23,128);dut.submodules.lcd=lcd=RGBLCD(port,None)
ports={'sys_clk':dut.cd_sys.clk,'sys_rst':dut.cd_sys.rst,
    'video_clk':dut.cd_video.clk,'video_rst':dut.cd_video.rst,
    'base0':lcd._base0.storage,'base1':lcd._base1.storage,'enable':lcd._enable.storage,
    'select_buffer':lcd._select.storage,'valid_base':lcd.valid_base,'frame_base':lcd.frame_base,
    'busy':lcd._busy.status,'address_error':lcd._address_error.status,'pending':lcd.pending,
    'cmd_valid':port.cmd.valid,'cmd_addr':port.cmd.addr,'cmd_ready':port.cmd.ready,
    'rdata_valid':port.rdata.valid,'rdata_data':port.rdata.data}
for name,signal in ports.items():signal.name_override=name
(out/'lcd.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='lcd')))
(out/'tb.v').write_text('''module tb;
reg sys_clk=0,video_clk=0,sys_rst=1,video_rst=1;
always #5 sys_clk=~sys_clk; always #20 video_clk=~video_clk;
reg [31:0] base0=0,base1=0;reg enable=0,select_buffer=0;
wire valid_base,busy,address_error,pending,cmd_valid;wire [31:0] frame_base;wire [22:0] cmd_addr;
lcd dut(.sys_clk(sys_clk),.sys_rst(sys_rst),.video_clk(video_clk),.video_rst(video_rst),
 .base0(base0),.base1(base1),.enable(enable),.select_buffer(select_buffer),.valid_base(valid_base),
 .busy(busy),.address_error(address_error),.pending(pending),.frame_base(frame_base),
 .cmd_valid(cmd_valid),.cmd_addr(cmd_addr),.cmd_ready(1'b1),.rdata_valid(1'b0),.rdata_data(128'b0));
task tick;begin @(posedge sys_clk);#1;end endtask
task check(input [31:0] address,input slot,input expected);
 integer i;begin
 sys_rst=1;video_rst=1;enable=0;repeat(8)tick;
 base0=slot?32'h100000:address;base1=slot?address:32'h200000;select_buffer=slot;
 sys_rst=0;video_rst=0;repeat(8)tick;
 if(valid_base!==expected)$fatal(1,"admission %x",address);
 enable=1;force dut.pending=1'b1;tick;release dut.pending;repeat(3)tick;
 if(busy!==expected || address_error!==!expected)$fatal(1,"start %x",address);
 if(expected)begin
 if(frame_base!==address)$fatal(1,"latch %x",frame_base);
 base0=32'h300000;base1=32'h400000;
 for(i=0;i<20;i=i+1)begin tick;
 if(frame_base!==address)$fatal(1,"midframe update");
 if(cmd_valid && (cmd_addr<(address>>4) || cmd_addr>=((address+261120)>>4)))$fatal(1,"DMA range");
 end end
 end endtask
initial begin
 check(32'h10000,0,1);check(32'h345000,1,1);check(32'h07fc0400,0,1);
 check(0,0,0);check(32'h10001,1,0);check(32'h07fc0410,0,0);check(32'hf1000000,1,0);
 $display("LCD RTL dynamic CSR bases, range/alignment rejection and frame latching PASS");$finish;
end
initial begin #100000;$fatal(1,"timeout");end
endmodule
''')
iv=Path('C:/msys64/ucrt64/bin/iverilog.exe');env={**os.environ,'PATH':str(iv.parent)+os.pathsep+os.environ['PATH'],
    'TMP':str(out),'TEMP':str(out),'TMPDIR':str(out)}
subprocess.run([str(iv),'-g2012','-s','tb','-o',str(out/'test.vvp'),str(out/'lcd.v'),str(out/'tb.v')],check=True,env=env)
subprocess.run([str(iv.with_name('vvp.exe')),str(out/'test.vvp')],check=True,env=env)
