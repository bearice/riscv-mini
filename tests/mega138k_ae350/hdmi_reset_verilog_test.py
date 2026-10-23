"""Exercise HDMI reset assertion, phase-safe release and a stopped reset clock."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HDMI = ROOT / 'gateware/devices/video/hdmi'

TB = r'''
`timescale 1ns/1ps
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
    #8;
    forever begin
      if(run_reset_clock) reset_clock=1;
      #20; reset_clock=0; #20;
    end
  end
  always @(fast) last_fast=$realtime;
  always @(posedge pixel) last_pixel=$realtime;
  ae350_hdmi_phy dut(.resetn(resetn),.clk_pixel(pixel),
    .clk_5x_pixel(fast),.clk_reset(reset_clock),.locked(locked),
    .rgb(24'b0),.de(1'b0),.hsync(1'b1),.vsync(1'b1),
    .tmds_clk_p(cp),.tmds_clk_n(cn),.tmds_d_p(dp),.tmds_d_n(dn));
  always @(posedge dut.serializer_resetn) begin
    if(!locked || !resetn) $fatal(1,"released while reset or lock loss asserted");
    if(!reset_clock || $realtime-last_pixel < 7.99) $fatal(1,"wrong reset release phase");
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


class HDMIResetTest(unittest.TestCase):
    def test_reset_and_lock_transitions(self):
        iverilog, vvp = shutil.which('iverilog'), shutil.which('vvp')
        if not iverilog or not vvp:
            self.fail('iverilog/vvp required; run in a Nix shell with nixpkgs#iverilog')
        with tempfile.TemporaryDirectory(prefix='ae350-hdmi-reset-') as directory:
            directory = Path(directory)
            tb = directory / 'tb.v'
            tb.write_text(TB)
            compiled = directory / 'tb.vvp'
            subprocess.run([iverilog, '-g2012', '-s', 'tb', '-I', str(HDMI),
                            '-o', str(compiled), str(tb),
                            *map(str, sorted(HDMI.glob('*.v')))], check=True)
            result = subprocess.run([vvp, str(compiled)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS HDMI', result.stdout)


if __name__ == '__main__':
    unittest.main()
