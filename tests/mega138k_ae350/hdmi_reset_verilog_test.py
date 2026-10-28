"""Exercise HDMI reset assertion, phase-safe release and a stopped reset clock."""
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hdmi_migen_test_support import encoder_verilog, phy_verilog

ROOT = Path(__file__).resolve().parents[2]
HDMI = ROOT / 'gateware/devices/video/hdmi'

TB = r'''
`timescale 1ns/1ps
module DFFCE(output reg Q=0,input D,CLK,CE,CLEAR);
always @(posedge CLK or posedge CLEAR)if(CLEAR)Q<=0;else if(CE)Q<=D;
endmodule
module OSER10(output Q, input D0,D1,D2,D3,D4,D5,D6,D7,D8,D9,PCLK,FCLK,RESET);
  assign Q = RESET ? 1'b0 : D0;
endmodule
module ELVDS_OBUF(input I, output O,OB);
  assign O=I; assign OB=~I;
endmodule
module tb;
  reg clk=0, pixel=1, fast=1, reset_clock=0;
  reg run_reset_clock=1, resetn=0, locked=0;
  wire [2:0] dp,dn;
  wire cp,cn,ready;
  integer releases=0;
  realtime last_fast=0, last_pixel=0;
  always #6.25 clk=~clk;
  always #20 pixel=~pixel;
  always #4 fast=~fast;
  initial begin
    #10;
    forever begin
      if(run_reset_clock) reset_clock=1;
      #20; reset_clock=0; #20;
    end
  end
  always @(fast) last_fast=$realtime;
  always @(posedge pixel) last_pixel=$realtime;
  hdmi_phy dut(.resetn(resetn),.clk_pixel(pixel),
    .clk_5x_pixel(fast),.clk_reset(reset_clock),.locked(locked),
    .rgb(24'b0),.de(1'b0),.hsync(1'b1),.vsync(1'b1),
    .tmds_clk_p(cp),.tmds_clk_n(cn),.tmds_d_p(dp),.tmds_d_n(dn));
  always @(posedge dut.serializer_resetn) begin
    if(!locked || !resetn) $fatal(1,"released while reset or lock loss asserted");
    if(!reset_clock || $realtime-last_pixel < 9.99 || $realtime-last_pixel > 10.01)
      $fatal(1,"wrong reset release phase");
    if($realtime-last_fast < 1.99 || $realtime-last_fast > 2.01)
      $fatal(1,"reset release overlaps serializer clock edge");
    releases=releases+1;
  end
  task wait_release;
    begin
      wait(dut.serializer_resetn===1'b1);
      #1;
      if(cp!==pixel || cn!==!pixel) $fatal(1,"differential clock mismatch");
    end
  endtask
  initial begin
    #37; locked=1; #19; resetn=1;
    #100;
    if(dut.serializer_resetn!==0) $fatal(1,"startup hold too short");
    wait_release;
    #7; locked=0; #0.01;
    if(dut.serializer_resetn!==0) $fatal(1,"lock-loss assertion is not asynchronous");
    #13; locked=1; wait_release;
    #7; resetn=0; #0.01;
    if(dut.serializer_resetn!==0) $fatal(1,"external reset assertion is not asynchronous");
    run_reset_clock=0;
    #41; resetn=1; #200;
    if(dut.serializer_resetn!==0) $fatal(1,"released without reset clock");
    run_reset_clock=1;
    wait_release;
    #160;
    if(releases!=3) $fatal(1,"wrong number of releases: %0d",releases);
    $display("PASS HDMI asynchronous reset, lock loss, stopped clock and phase-locked release");
    $finish;
  end
  initial begin #3000; $fatal(1,"timeout"); end
endmodule
'''


ALIGN_TB = r'''
`timescale 1ns/1ps
module DFFCE(output reg Q=0,input D,CLK,CE,CLEAR);
always @(posedge CLK or posedge CLEAR)if(CLEAR)Q<=0;else if(CE)Q<=D;
endmodule
module OSER10(output Q,input D0,D1,D2,D3,D4,D5,D6,D7,D8,D9,PCLK,FCLK,RESET);
assign Q=RESET?0:D0;
endmodule
module ELVDS_OBUF(input I,output O,OB);assign O=I;assign OB=~I;endmodule
module tb;
reg pixel=0,fast=0,release_clk=0,resetn=0,locked=0;
always #20 pixel=~pixel;
always #4 fast=~fast;
initial begin #10;forever #20 release_clk=~release_clk;end
reg [23:0] rgb=0;
reg de=0,hsync=0,vsync=0,checking=0;
wire cp,cn;
wire [2:0] dp,dn;
hdmi_phy dut(.resetn(resetn),.locked(locked),.clk_pixel(pixel),
 .clk_5x_pixel(fast),.clk_reset(release_clk),.rgb(rgb),.de(de),.hsync(hsync),.vsync(vsync),
 .tmds_clk_p(cp),.tmds_clk_n(cn),.tmds_d_p(dp),.tmds_d_n(dn));
wire [9:0] ref0,ref1,ref2;
tmds_encoder r0(.clk(pixel),.resetn(dut.pixel_resetn_q[3]),.de(de),.ctrl({vsync,hsync}),.din(rgb[23:16]),.dout(ref0));
tmds_encoder r1(.clk(pixel),.resetn(dut.pixel_resetn_q[3]),.de(de),.ctrl(2'b0),.din(rgb[15:8]),.dout(ref1));
tmds_encoder r2(.clk(pixel),.resetn(dut.pixel_resetn_q[3]),.de(de),.ctrl(2'b0),.din(rgb[7:0]),.dout(ref2));
reg [9:0] previous0,previous1,previous2;
integer verified=0;
always @(negedge pixel)begin
 if(checking)begin
  if(dut.symbol0!==previous0 || dut.symbol1!==previous1 || dut.symbol2!==previous2)
   $fatal(1,"RGB/DE/HS/VS pipeline changed TMDS stream");
  verified=verified+1;
 end
 previous0=ref0;previous1=ref1;previous2=ref2;
end
integer i;
initial begin
 #37;resetn=1;locked=1;
 repeat(20)@(posedge pixel);
 @(negedge pixel);#1;checking=1;
 for(i=0;i<512;i=i+1)begin
  @(negedge pixel);#1;
  rgb=(24'h13579b*i)^24'habcdef;de=(i%13)<9;hsync=(i%17)<3;vsync=(i%31)<4;
 end
 if(verified<500)$fatal(1,"insufficient stream checks");
 $display("PASS aligned RGB and control TMDS pipeline");$finish;
end
initial begin #30000;$fatal(1,"timeout");end
endmodule
'''


class HDMIResetTest(unittest.TestCase):
    def test_aligned_rgb_and_control_pipeline(self):
        self.check_rtl(ALIGN_TB,'PASS aligned')

    def test_reset_and_lock_transitions(self):
        self.check_rtl(TB,'PASS HDMI')

    def check_rtl(self,testbench,expected):
        iverilog, vvp = shutil.which('iverilog'), shutil.which('vvp')
        if not iverilog or not vvp:
            self.fail('iverilog/vvp required; run in a Nix shell with nixpkgs#iverilog')
        with tempfile.TemporaryDirectory(prefix='ae350-hdmi-reset-') as directory:
            directory = Path(directory)
            tb = directory / 'tb.v'
            tb.write_text(testbench)
            compiled = directory / 'tb.vvp'
            (directory/'phy.v').write_text(phy_verilog())
            (directory/'encoder.v').write_text(encoder_verilog())
            subprocess.run([iverilog, '-g2012', '-s', 'tb', '-I', str(HDMI),
                            '-o', str(compiled), str(tb),
                            str(directory/'phy.v'),str(directory/'encoder.v')], check=True)
            result = subprocess.run([vvp, str(compiled)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(expected, result.stdout)


if __name__ == '__main__':
    unittest.main()
