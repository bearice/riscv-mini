"""Exercise real async FIFOs, byte packing, mode changes and stop admission."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from migen import Module,ClockDomain,Signal
from migen.fhdl import verilog
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.build.gowin.common import GowinAsyncResetSynchronizer
from gateware.machines.mega138k_ae350.peripherals.video import FabricVideo,VideoPixels
from gateware.machines.mega138k_ae350.video_modes import VideoMode,MODES,pll_config
from gateware.machines.mega138k_ae350.video_clock import VideoPLL,RESET_FINE
from gateware.machines.mega138k_ae350.address_map import VIDEO_REGS

TB=r'''
`timescale 1ns/1ps
module DFFP(output reg Q,input D,CLK,PRESET);
always @(posedge CLK or posedge PRESET)if(PRESET)Q<=1;else Q<=D;
endmodule
`include "regs.vh"
module tb;
reg sys_clk=0,sys_rst=1,ddr_clk=0,ddr_rst=0,hdmi_pix_clk=0,hdmi_pix_rst=0,run_pix=1;
always #6 sys_clk=~sys_clk;
always #5 ddr_clk=~ddr_clk;
always #20 hdmi_pix_clk=run_pix ? ~hdmi_pix_clk:0;
reg reset=1,selected=1,commit=0,write=0,clock_locked=1,hpd_input=0;
reg [11:0] off=0;
reg [31:0] wdata=0;
wire [31:0] rdata;
wire ok,pending,stopped,mode_busy,clock_reset,de,pixel_enabled,irq;
wire [23:0] rgb;
wire [11:0] x,y;
wire [2:0] ui_cmd;
wire [28:0] ui_addr;
wire ui_cmd_en;
reg ui_cmd_ready=1,ui_rd_data_valid=0;
reg [255:0] ui_rd_data=0;
dut dut(.*);

integer current_mode=0,fmt=0,width=32,height=3,stride=64,step=2;
integer checked=0,commands=0,delay_left=0,cycles=0;
reg memory_busy=0;
reg [31:0] saved_address;
function [7:0] memory_byte(input integer address);
integer offset,index,component,pixel;
begin
 offset=address-32'h100000;
 index=(offset/stride)*width+(offset%stride)/step;
 component=(offset%stride)%step;
 if(fmt==0)pixel=index%3==0?16'hf800:index%3==1?16'h07e0:16'h001f;
 else pixel=24'h123456+index;
 memory_byte=(pixel>>(component*8))&255;
end endfunction
integer j;
always @(posedge ddr_clk)begin
 ui_rd_data_valid<=0;cycles=cycles+1;ui_cmd_ready<=cycles%7!=1;
 if(ui_cmd_en && ui_cmd_ready)begin
  if(memory_busy)$fatal(1,"overlapping DDR command");
  memory_busy<=1;saved_address<=ui_addr<<2;delay_left<=2;commands=commands+1;
 end
 if(memory_busy)begin
  if(delay_left==0)begin
   ui_rd_data_valid<=1;memory_busy<=0;
   for(j=0;j<32;j=j+1)ui_rd_data[j*8+:8]<=memory_byte(saved_address+j);
  end else delay_left<=delay_left-1;
 end
end
integer index,expected_color;
always @(posedge hdmi_pix_clk)if(!reset && !mode_busy && de && pixel_enabled)begin
 index=y*width+x;
 if(fmt==0)expected_color=index%3==0?24'h0000ff:index%3==1?24'h00ff00:24'hff0000;
 else begin
  expected_color=24'h123456+index;
  expected_color=((expected_color&255)<<16)|(expected_color&65280)|((expected_color>>16)&255);
 end
 if(rgb!==expected_color[23:0])$fatal(1,"mode%0d fmt%0d x%0d y%0d got%x expected%x",current_mode,fmt,x,y,rgb,expected_color);
 checked=checked+1;
end
task tick;begin @(posedge sys_clk);#1;end endtask
task wr(input[11:0] a,input[31:0] d);
begin
 @(negedge sys_clk);off=a;wdata=d;write=1;commit=0;#1;
 if(!ok)$fatal(1,"CSR write rejected %x",a);
 tick;@(negedge sys_clk);commit=1;
 tick;@(negedge sys_clk);commit=0;write=0;
end endtask
task await_status(input[31:0] mask,value);
integer n;
begin
 @(negedge sys_clk);off=`VIDEO_STATUS;write=0;#1;n=0;
 while((rdata&mask)!=value)begin tick;n=n+1;if(n>200000)begin off=`VIDEO_LAST_ERROR;#1;$fatal(1,"status timeout error%x m%0d f%0d mask%0d",rdata,current_mode,fmt,mask);end end
end endtask
integer m,f,before_stop,mode_commands;
initial begin
 repeat(20)tick;@(negedge sys_clk);sys_rst=0;reset=0;
 await_status(16,16);
 for(m=0;m<3;m=m+1)for(f=0;f<3;f=f+1)begin
  current_mode=m;fmt=f;step=f+2;width=32*(m+1);height=3+m;
  stride=width*step+32;
  // Stopped scanout alone is insufficient while output remains enabled.
  wr(`VIDEO_MODE_CONFIG,m|(f<<8));
  if(mode_busy)$fatal(1,"mode switch with output enabled");
  wr(`VIDEO_CLEAR_ERRORS,1);wr(`VIDEO_ENABLE,0);
  mode_commands=commands;run_pix=0;
  clock_locked=0;wr(`VIDEO_MODE_CONFIG,m|(f<<8));
  wr(`VIDEO_IRQ_ENABLE,0);wr(`VIDEO_IRQ_STATUS,3);
  // HPD remains observable through APB while the pixel clock is stopped.
  hpd_input=1;repeat(20)tick;off=`VIDEO_HPD;#1;
  if(rdata!=3)$fatal(1,"HPD connection stalled with stopped pixel clock");
  off=`VIDEO_IRQ_STATUS;#1;if(rdata!=1 || irq)$fatal(1,"masked HPD event lost");
  wr(`VIDEO_IRQ_ENABLE,3);#1;if(!irq)$fatal(1,"HPD IRQ not asserted");
  wr(`VIDEO_IRQ_STATUS,1);#1;if(irq)$fatal(1,"HPD IRQ not acknowledged");
  hpd_input=0;repeat(20)tick;off=`VIDEO_HPD;#1;
  if(rdata!=0)$fatal(1,"HPD disconnect stalled with stopped pixel clock");
  off=`VIDEO_IRQ_STATUS;#1;if(rdata!=2 || !irq)$fatal(1,"disconnect IRQ missing");
  wr(`VIDEO_IRQ_ENABLE,0);#1;if(irq)$fatal(1,"IRQ mask ineffective");
  wr(`VIDEO_IRQ_STATUS,2);
  repeat(1500)tick;
  if(!mode_busy)$fatal(1,"mode completed without PLL lock");
  clock_locked=1;repeat(1500)tick;
  if(!mode_busy)$fatal(1,"mode completed without fresh pixel-domain acknowledgment");
  if(commands!=mode_commands)$fatal(1,"stale FIFO descriptor issued DDR read during clock restart");
  run_pix=1;wait(!mode_busy);repeat(150)tick;
  off=`VIDEO_GEOMETRY;#1;
  if(rdata!=(width|(height<<16)))$fatal(1,"geometry %x",rdata);
  off=`VIDEO_FORMAT;#1;if(rdata!=f)$fatal(1,"format readback");
  wr(`VIDEO_BASE0,32'h100000);wr(`VIDEO_STRIDE,stride);
  wr(`VIDEO_ENABLE,1);
  checked=0;wr(`VIDEO_CONTROL,1);wr(`VIDEO_COMMIT,1);await_status(7,1);
  wait(checked>=width*height*2);
  // A live disable request cannot release framebuffer ownership.
  wr(`VIDEO_ENABLE,0);off=`VIDEO_ENABLE;#1;if(rdata!=1)$fatal(1,"live disable accepted");
  // Live ownership must reject mode changes.
  wr(`VIDEO_MODE_CONFIG,0);off=`VIDEO_MODE;#1;if(rdata!=m)$fatal(1,"live mode write accepted");
  off=`VIDEO_ERRORS;#1;if(!(rdata&1))$fatal(1,"live mode rejection unreported");
  wr(`VIDEO_CLEAR_ERRORS,1);
  wr(`VIDEO_CONTROL,0);wr(`VIDEO_COMMIT,1);await_status(21,16);
  before_stop=commands;repeat(1000)tick;
  if(commands!=before_stop)$fatal(1,"reads after stop");
  off=`VIDEO_UNDERFLOWS;#1;if(rdata!=0)$fatal(1,"underflow");
 end
 $display("PASS runtime modes, 565/888/8888, burst boundaries, stride, drain and lock");$finish;
end
initial begin #10000000;$fatal(1,"timeout");end
endmodule
'''


def run_rtl(module,ports,tb,extra=(),timeout=90):
    iv,vvp=shutil.which('iverilog'),shutil.which('vvp')
    if not iv or not vvp:raise AssertionError('iverilog/vvp required')
    for name,signal in ports.items():signal.name_override=name
    with tempfile.TemporaryDirectory(prefix='ae350-runtime-') as tmp:
        tmp=Path(tmp)
        (tmp/'dut.v').write_text(str(verilog.convert(module,ios=set(ports.values()),name='dut',
            special_overrides={AsyncResetSynchronizer:GowinAsyncResetSynchronizer})))
        (tmp/'tb.v').write_text(tb)
        (tmp/'regs.vh').write_text(''.join('`define VIDEO_%s 12\'h%x\n'%(k,v) for k,v in VIDEO_REGS.items()))
        subprocess.run([iv,'-g2012','-I',str(tmp),'-s','tb','-o',str(tmp/'run'),str(tmp/'dut.v'),str(tmp/'tb.v'),*map(str,extra)],check=True)
        result=subprocess.run([vvp,str(tmp/'run')],text=True,capture_output=True,timeout=timeout)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
        return result.stdout


class RuntimeTest(unittest.TestCase):
    def test_async_scanout_formats_and_switches(self):
        modes=tuple(VideoMode(str(i),32*(i+1),3+i,2,2,2,1,1,1,25000000,bool(i&1),bool(i&1)) for i in range(3))
        m=Module()
        for name in ('sys','ddr','hdmi_pix'):setattr(m.clock_domains,'cd_'+name,ClockDomain(name))
        with patch('gateware.machines.mega138k_ae350.peripherals.video.MODES',modes):
            m.submodules.video=v=FabricVideo(width=32,height=3,runtime=True,hpd_supported=True,hpd_debounce_cycles=8)
        ports={n+'_clk':getattr(m,'cd_'+n).clk for n in ('sys','ddr','hdmi_pix')}
        ports.update({n+'_rst':getattr(m,'cd_'+n).rst for n in ('sys','ddr','hdmi_pix')})
        ports.update({n:getattr(v,n) for n in ('reset','selected','commit','write','off','wdata','rdata','ok','pending','stopped','mode_busy','clock_reset','clock_locked','hpd_input','irq')})
        ports.update({n:getattr(v.pixels,n) for n in ('de','rgb','x','y')})
        ports['pixel_enabled']=v.pixels.enabled
        ports.update({'ui_'+n:getattr(v.ui,n) for n in ('cmd','addr','cmd_en','cmd_ready','rd_data_valid','rd_data')})
        self.assertIn('PASS runtime',run_rtl(m,ports,TB))

    def test_full_resolution_sync_polarities(self):
        m=Module()
        m.clock_domains.cd_sys=ClockDomain('sys')
        m.submodules.pixels=v=VideoPixels(runtime=True)
        fields=('width','height','h_front','h_sync','h_back','v_front','v_sync','v_back','h_positive','v_positive')
        ports=dict(sys_clk=m.cd_sys.clk,sys_rst=m.cd_sys.rst)
        ports.update({n:getattr(v,n) for n in (*fields,'x','y','de','hsync','vsync','frame_start')})
        cases=[]
        for mode in MODES:
            assignments=';'.join('%s=%d'%(n,getattr(mode,n)) for n in fields)+';'
            cases.append('sys_rst=1;'+assignments+'repeat(3)@(negedge sys_clk);sys_rst=0;\n'
                'wait(frame_start);@(negedge sys_clk);count=0;active=0;hs_count=0;vs_count=0;\n'
                'repeat(%d)begin @(posedge sys_clk);\n'
                'if(de !== (x<width && y<height))$fatal(1,"DE");\n'
                'if(hsync !== (((x>=width+h_front && x<width+h_front+h_sync)?0:1)^h_positive))$fatal(1,"HS");\n'
                'if(vsync !== (((y>=height+v_front && y<height+v_front+v_sync)?0:1)^v_positive))$fatal(1,"VS");\n'
                'if(de)active=active+1;\n'
                'if(hsync==h_positive)hs_count=hs_count+1;\n'
                'if(vsync==v_positive)vs_count=vs_count+1;end\n'
                'if(active!=%d || hs_count!=%d || vs_count!=%d)$fatal(1,"full-frame cadence");\n'
                '@(negedge sys_clk);\n'
                %(mode.h_total*mode.v_total,mode.width*mode.height,mode.h_sync*mode.v_total,mode.v_sync*mode.h_total))
        tb="""`timescale 1ns/1ps
module tb;
reg sys_clk=0,sys_rst=1;
always #5 sys_clk=~sys_clk;
reg [11:0] width,height,h_front,h_sync,h_back,v_front,v_sync,v_back;
reg h_positive,v_positive;
wire [11:0] x,y;
wire de,hsync,vsync,frame_start;
integer count,active,hs_count,vs_count;
dut dut(.*);
initial begin
"""+''.join(cases)+"""
$display("PASS full mode timing and sync polarities");$finish;
end
endmodule
"""
        self.assertIn('PASS full mode',run_rtl(m,ports,tb,timeout=300))

    def test_vendor_pll_dynamic_modes(self):
        library=Path(os.environ['GOWIN_HOME'])/'IDE/simlib/gw5a/prim_sim.v'
        self.assertTrue(library.is_file())
        m=Module()
        for name in ('pix','serial','release'):
            setattr(m.clock_domains,'cd_'+name,ClockDomain(name,reset_less=True))
        clk50=Signal()
        m.submodules.pll=v=VideoPLL(clk50,(m.cd_pix,m.cd_serial,m.cd_release),50000000)
        ports=dict(clk50=clk50,reset=v.reset,mode=v.mode,locked=v.locked,
                   pix=m.cd_pix.clk,serial=m.cd_serial.clk,release_clk=m.cd_release.clk)
        checks=[]
        for i,mode in enumerate(MODES):
            config=pll_config(mode.pixel_hz)
            period=1e9/config['actual_hz']
            phase=period*RESET_FINE/(8*config['pixel_div'])
            checks.append("reset=1;#200;mode=%d;#200;reset=0;wait(locked);repeat(8)@(posedge pix);\n"
                "@(posedge pix);start=$realtime;@(posedge pix);period=$realtime-start;\n"
                "if(period<%f || period>%f)$fatal(1,\"pixel mode %d period %%f\",period);\n"
                "@(posedge serial);start=$realtime;@(posedge serial);period=$realtime-start;\n"
                "if(period<%f || period>%f)$fatal(1,\"serializer mode %d period %%f\",period);\n"
                "@(posedge pix);start=$realtime;#%f;\n"
                "if(last_release-start<%f || last_release-start>%f)$fatal(1,\"reset clock phase\");\n"
                %(i,period-.04,period+.04,i,period/5-.02,period/5+.02,i,phase+.05,phase-.02,phase+.02))
        tb="""`timescale 1ns/1ps
module DFFP(output reg Q,input D,CLK,PRESET);
always @(posedge CLK or posedge PRESET)if(PRESET)Q<=1;else Q<=D;
endmodule
module tb;
reg clk50=0,reset=1;
reg [2:0] mode=0;
wire locked,pix,serial,release_clk;
always #10 clk50=~clk50;
GSR GSR(.GSRI(1'b1));
dut dut(.*);
realtime start,period,last_release=-100;
always @(posedge release_clk)last_release=$realtime;
initial begin
"""+''.join(checks)+"""
$display("PASS vendor dynamic PLL clocks");$finish;
end
initial begin #20000000;$fatal(1,"PLL timeout");end
endmodule
"""
        # Icarus does not schedule vendor always @(*) blocks containing only
        # parameters. The library initializes static fine/coarse to zero, so
        # snapshot that initializer from the actual parameters; keep all PLL
        # frequency/divider/lock/phase propagation logic untouched.
        source=library.read_text(errors='replace')
        start=source.index('module PLL (')
        end=source.index('endmodule',start)+len('endmodule')
        model=source[start:end]
        for name,param in (('fine_2','CLKOUT2_PE_FINE'),('coarse_2','CLKOUT2_PE_COARSE')):
            original=name+' = 0;'
            self.assertEqual(model.count(original),1,'review vendor static-phase compatibility patch')
            model=model.replace(original,name+' = '+param+';')
        with tempfile.TemporaryDirectory(prefix='ae350-pll-model-') as tmp:
            snapshot=Path(tmp)/'prim_sim.v'
            snapshot.write_text(source[:start]+model+source[end:])
            self.assertIn('PASS vendor',run_rtl(m,ports,tb,(snapshot,)))

    def test_mode_catalog(self):
        for mode in MODES:
            c=pll_config(mode.pixel_hz)
            self.assertLess(abs(c['actual_hz']/mode.pixel_hz-1),.005)
            self.assertEqual(c['pixel_div'],5*c['serial_div'])
            for bpp in (2,3,4):self.assertEqual(mode.width*bpp%32,0)
            self.assertLess(mode.h_total,4096)
            self.assertLess(mode.v_total,4096)


if __name__=='__main__':unittest.main()
