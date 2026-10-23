"""Exact sync cadence, RGB565 channel order and recovery after a missing pixel."""
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
from gateware.machines.mega138k_ae350.peripherals.video import VideoPixels

TB = r'''
module tb;
reg sys_clk=0,sys_rst=1,enabled=1;
always #5 sys_clk=~sys_clk;
wire de,hsync,vsync,vblank,frame_start,frame_bad,source_ready;
wire [23:0] rgb;
wire [31:0] epoch,frames,underflows,missing_pixels;
reg [31:0] source_index=0,source_epoch=0;
wire [15:0] source_data=source_index%3==0 ? 16'hf800 : source_index%3==1 ? 16'h07e0 : 16'h001f;
integer frame_number=0;
wire source_valid=(source_index<96) && !(frame_number==2 && !frame_bad && source_index==10);
dut dut(.*);
integer x=0,y=3,index=0,active_count=0;
reg [23:0] expected_rgb;
always @(posedge sys_clk)begin
  if(sys_rst)begin
    x=0;y=3;index=0;frame_number=0;source_index<=0;source_epoch<=0;
  end else begin
    if(de !== (x<32 && y<3) || hsync !== !(x>=34 && x<36) ||
       vsync !== !(y==4) || vblank !== (y>=3))$fatal(1,"sync cadence x=%0d y=%0d",x,y);
    if(frame_start)begin
      if(frame_number>0 && active_count!=96)$fatal(1,"active pixels per frame %0d",active_count);
      frame_number=frame_number+1;index=0;active_count=0;
      source_epoch<=source_epoch+1;source_index<=0;
    end else if(source_valid && source_ready)source_index<=source_index+1;
    if(de)begin
      expected_rgb=index%3==0 ? 24'h0000ff : index%3==1 ? 24'h00ff00 : 24'hff0000;
      if(frame_number==2 && index>=10)expected_rgb=0;
      if(rgb!==expected_rgb)$fatal(1,"frame %0d pixel %0d RGB %x expected %x",frame_number,index,rgb,expected_rgb);
      index=index+1;active_count=active_count+1;
    end
    if(x==37)begin x=0;y=(y==5)?0:y+1;end else x=x+1;
  end
end
initial begin
  repeat(3)@(posedge sys_clk);@(negedge sys_clk);sys_rst=0;
  wait(frame_number==4);#1;
  if(underflows!=1 || missing_pixels!=86 || frames!=3)$fatal(1,"underflow accounting %0d/%0d/%0d",underflows,missing_pixels,frames);
  if(frame_bad)$fatal(1,"bad-frame state survived vblank");
  $display("PASS continuous timing, RGB565, frame blanking and next-frame recovery");$finish;
end
initial begin #50000;$fatal(1,"timeout");end
endmodule
'''


class PixelTimingTest(unittest.TestCase):
    def test_sync_and_underflow_recovery(self):
        iv, vvp = shutil.which('iverilog'), shutil.which('vvp')
        self.assertTrue(iv and vvp, 'iverilog/vvp required')
        m = Module()
        m.clock_domains.cd_sys = ClockDomain('sys')
        m.submodules.video = video = VideoPixels(width=32, height=3,
            h_front=2, h_sync=2, h_back=2, v_front=1, v_sync=1, v_back=1)
        ports = dict(sys_clk=m.cd_sys.clk, sys_rst=m.cd_sys.rst)
        ports.update({name: getattr(video, name) for name in
                      ('enabled', 'rgb', 'de', 'hsync', 'vsync', 'vblank', 'frame_start',
                       'epoch', 'frames', 'underflows', 'missing_pixels', 'frame_bad')})
        ports.update({'source_' + name: getattr(video.source, name)
                      for name in ('valid', 'ready', 'data', 'index', 'epoch')})
        for name, signal in ports.items():
            signal.name_override = name
        with tempfile.TemporaryDirectory(prefix='ae350-video-pixels-') as directory:
            directory = Path(directory)
            (directory / 'dut.v').write_text(str(verilog.convert(m, ios=set(ports.values()), name='dut')))
            (directory / 'tb.v').write_text(TB)
            compiled = directory / 'test.vvp'
            subprocess.run([iv, '-g2012', '-s', 'tb', '-o', str(compiled),
                            str(directory / 'dut.v'), str(directory / 'tb.v')], check=True)
            r = subprocess.run([vvp, str(compiled)], text=True, capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('PASS continuous timing', r.stdout)


if __name__ == '__main__':
    unittest.main()
