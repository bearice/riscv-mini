"""Real async FIFOs: coherent commit, vblank flip, invalid base and drained stop."""
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
from migen.genlib.resetsync import AsyncResetSynchronizer
from litex.build.gowin.common import GowinAsyncResetSynchronizer
from gateware.machines.mega138k_ae350.peripherals.video import FabricVideo
from gateware.machines.mega138k_ae350.address_map import VIDEO_REGS

TB = r'''
`timescale 1ns/1ps
module DFFP(output reg Q, input D,CLK,PRESET);
  always @(posedge CLK or posedge PRESET) if(PRESET)Q<=1'b1;else Q<=D;
endmodule
`include "video_regs.vh"
module tb;
reg sys_clk=0,sys_rst=1,ddr_clk=0,ddr_rst=0,hdmi_pix_clk=0,hdmi_pix_rst=0;
always #6 sys_clk=~sys_clk;
always #5 ddr_clk=~ddr_clk;
always #20 hdmi_pix_clk=~hdmi_pix_clk;
reg reset=1,selected=1,commit=0,write=0;
reg [11:0] off=0;
reg [31:0] wdata=0;
wire [31:0] rdata,active_base_pixel;
wire ok,pending,stopped,pixel_enabled,de,vblank,frame_start;
wire [23:0] rgb;
wire [7:0] x;
wire [5:0] y;
wire [2:0] ui_cmd;
wire [28:0] ui_addr;
wire ui_cmd_en;
reg ui_cmd_ready=1,ui_rd_data_valid=0;
reg [255:0] ui_rd_data=0;
dut dut(.*);
integer commands=0,delay_left=0,ddr_cycles=0,red_pixels=0,green_pixels=0;
reg memory_busy=0;
reg [28:0] saved_address;
always @(posedge ddr_clk)begin
  ui_rd_data_valid<=0;ddr_cycles=ddr_cycles+1;
  ui_cmd_ready<=ddr_cycles%5!=2;
  if(ui_cmd_en && ui_cmd_ready)begin
    if(memory_busy || ui_cmd!=1)$fatal(1,"overlapping DDR read");
    memory_busy<=1;saved_address<=ui_addr;delay_left<=3;commands=commands+1;
  end
  if(memory_busy)begin
    if(delay_left==0)begin
      ui_rd_data_valid<=1;memory_busy<=0;
      ui_rd_data<=saved_address>=29'h80000 ? {16{16'h07e0}} : {16{16'hf800}};
    end else delay_left<=delay_left-1;
  end
end
reg [31:0] last_base=0;
always @(posedge hdmi_pix_clk)begin
  if(!reset)begin
    if(active_base_pixel!=last_base && !vblank)$fatal(1,"mid-frame configuration change");
    last_base=active_base_pixel;
    if(de && pixel_enabled)begin
      if(active_base_pixel==32'h100000)begin
        if(rgb!==24'h0000ff)$fatal(1,"red frame data/alignment %x",rgb);
        red_pixels=red_pixels+1;
      end else if(active_base_pixel==32'h200000)begin
        if(rgb!==24'h00ff00)$fatal(1,"green frame data/alignment %x",rgb);
        green_pixels=green_pixels+1;
      end else $fatal(1,"uncommitted framebuffer address %x",active_base_pixel);
    end
  end
end
task tick;begin @(posedge sys_clk);#1;end endtask
task change;begin @(negedge sys_clk);end endtask
task wr(input [11:0] a,input [31:0] d);
begin
  change;off=a;wdata=d;write=1;commit=0;#1;if(!ok)$fatal(1,"bad CSR write %x",a);
  tick;change;commit=1;
  tick;change;commit=0;write=0;
end endtask
task await_status(input [31:0] mask,value);
integer n;
begin
  change;off=`VIDEO_STATUS;write=0;n=0;#1;
  while((rdata&mask)!=value)begin tick;n=n+1;if(n>100000)$fatal(1,"status timeout %x",rdata);end
end endtask
integer before_stop;
initial begin
  repeat(20)tick;change;sys_rst=0;reset=0;
  wr(`VIDEO_BASE0,32'h100000);wr(`VIDEO_BASE1,32'h200000);wr(`VIDEO_STRIDE,32'd32);
  wr(`VIDEO_CONTROL,1);wr(`VIDEO_COMMIT,1);
  await_status(7,1);
  wait(de && x==8 && y==0);wr(`VIDEO_CONTROL,3);wr(`VIDEO_COMMIT,1);
  await_status(7,3);
  wait(green_pixels>=32);
  if(red_pixels<32)$fatal(1,"old frame was truncated on flip");
  wr(`VIDEO_STRIDE,32'h10000);wr(`VIDEO_COMMIT,1);
  change;off=`VIDEO_ADDRESS_ERROR;#1;if(rdata!=1)$fatal(1,"oversize stride was accepted");
  if(pending || active_base_pixel!=32'h200000)$fatal(1,"oversize stride was published");
  wr(`VIDEO_CLEAR_ERRORS,1);wr(`VIDEO_STRIDE,32'd32);
  wr(`VIDEO_BASE1,32'h200001);wr(`VIDEO_COMMIT,1);
  change;off=`VIDEO_ADDRESS_ERROR;#1;if(rdata!=1)$fatal(1,"invalid base not rejected");
  if(pending || active_base_pixel!=32'h200000)$fatal(1,"invalid configuration was published");
  change;off=`VIDEO_UNDERFLOWS;#1;if(rdata!=0)$fatal(1,"unexpected scanout underflow %0d",rdata);
  wr(`VIDEO_CONTROL,0);wr(`VIDEO_COMMIT,1);await_status(21,16);
  before_stop=commands;repeat(80000)tick;
  if(commands!=before_stop || !stopped)$fatal(1,"DDR reads continued after drained stop");
  $display("PASS async scanout: coherent vblank flip, RGB565, invalid base and drained stop");$finish;
end
initial begin #5000000;$fatal(1,"timeout");end
endmodule
'''


class ScanoutTest(unittest.TestCase):
    def test_coherent_scanout_and_stop(self):
        iv, vvp = shutil.which('iverilog'), shutil.which('vvp')
        self.assertTrue(iv and vvp, 'iverilog/vvp required')
        m = Module()
        for name in ('sys', 'ddr', 'hdmi_pix'):
            setattr(m.clock_domains, 'cd_' + name, ClockDomain(name))
        m.submodules.video = video = FabricVideo(width=16, height=2, fifo_depth=32, response_timeout=64)
        ports = {}
        for name in ('sys', 'ddr', 'hdmi_pix'):
            cd = getattr(m, 'cd_' + name)
            ports.update({name + '_clk': cd.clk, name + '_rst': cd.rst})
        ports.update({name: getattr(video, name) for name in
                      ('reset', 'selected', 'commit', 'write', 'off', 'wdata', 'rdata', 'ok',
                       'pending', 'stopped', 'active_base_pixel')})
        for name in ('cmd', 'cmd_en', 'addr', 'cmd_ready', 'rd_data_valid', 'rd_data'):
            ports['ui_' + name] = getattr(video.ui, name)
        ports.update({name: getattr(video.pixels, name) for name in
                      ('rgb', 'de', 'vblank', 'frame_start', 'x', 'y')})
        ports['pixel_enabled'] = video.pixels.enabled
        for name, signal in ports.items():
            signal.name_override = name
        with tempfile.TemporaryDirectory(prefix='ae350-video-scanout-') as directory:
            directory = Path(directory)
            (directory / 'dut.v').write_text(str(verilog.convert(m, ios=set(ports.values()), name='dut',
                special_overrides={AsyncResetSynchronizer: GowinAsyncResetSynchronizer})))
            (directory / 'tb.v').write_text(TB)
            (directory / 'video_regs.vh').write_text(''.join("`define VIDEO_%s 12'h%03x\n" % item for item in VIDEO_REGS.items()))
            compiled = directory / 'test.vvp'
            subprocess.run([iv, '-g2012', '-s', 'tb', '-I', str(directory), '-o', str(compiled),
                            str(directory / 'dut.v'), str(directory / 'tb.v')], check=True)
            r = subprocess.run([vvp, str(compiled)], text=True, capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('PASS async scanout', r.stdout)


if __name__ == '__main__':
    unittest.main()
