"""Scanout stride, FIFO stalls, frame cancellation, range checks and late reads."""
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
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI
from gateware.machines.mega138k_ae350.video_memory import VideoFrameReader

TB = r'''
module tb;
reg sys_clk=0,sys_rst=1;
always #5 sys_clk=~sys_clk;
reg request_valid=0;
reg request_run=1;
reg [31:0] request_base=32'h100000,request_stride=96,request_epoch=7;
wire request_ready,source_valid,busy,pending,address_error,timeout_error;
reg source_ready=0,cmd_ready=1,rd_data_valid=0;
reg [255:0] rd_data=0;
wire [255:0] source_data;
wire [31:0] source_block,source_epoch,frames,cancelled;
wire [28:0] addr;
wire [2:0] cmd;
wire cmd_en;
dut dut(.*);
integer commands=0,outputs=0,seen_in_epoch=0;
reg allow_return=1,physical_busy=0;
reg [28:0] queued_address;
integer latency=0;
reg [31:0] expected_epoch=7,expected_base=32'h100000;
reg [31:0] expected_address;
always @(posedge sys_clk)begin
  rd_data_valid<=0;
  if(cmd_en && cmd_ready)begin
    if(physical_busy || cmd!=1 || addr[2:0]!=0)$fatal(1,"overlapped/unaligned/non-read command");
    physical_busy<=1;queued_address<=addr;latency<=2;commands=commands+1;
  end
  if(physical_busy && allow_return)begin
    if(latency==0)begin
      physical_busy<=0;rd_data_valid<=1;rd_data<=queued_address;
    end else latency<=latency-1;
  end
  if(source_valid && source_ready)begin
    expected_address=(expected_base+(seen_in_epoch/2)*96+(seen_in_epoch%2)*32)>>2;
    if(source_epoch!==expected_epoch || source_block!==seen_in_epoch || source_data!==expected_address)
      $fatal(1,"wrong frame/stride/data epoch %0d block %0d data %x",source_epoch,source_block,source_data);
    seen_in_epoch=seen_in_epoch+1;outputs=outputs+1;
  end
end
task tick;begin @(posedge sys_clk);#1;end endtask
task change;begin @(negedge sys_clk);end endtask
task submit(input [31:0] base,epoch);
integer n;
begin
  change;request_base=base;request_epoch=epoch;request_valid=1;#1;
  n=0;while(!request_ready)begin tick;change;#1;n=n+1;if(n>50)$fatal(1,"request timeout");end
  tick;change;request_valid=0;
end endtask
task wait_outputs(input integer n);
integer count;
begin count=0;while(outputs<n)begin tick;count=count+1;if(count>300)$fatal(1,"output timeout");end end
endtask
initial begin
  repeat(3)tick;change;sys_rst=0;
  submit(32'h100000,7);
  wait(source_valid);repeat(12)tick;
  if(commands!=1 || !source_valid || source_block!=0)$fatal(1,"prefetch ignored backpressure");
  change;source_ready=1;wait_outputs(6);tick;
  if(frames!=1 || busy)$fatal(1,"frame did not complete");
  submit(32'h100001,8);repeat(6)tick;
  if(!address_error || commands!=6)$fatal(1,"invalid base reached DDR");
  // A new frame cannot steal a read already accepted by DRAM.
  change;allow_return=0;submit(32'h200000,8);
  wait(commands==7);change;request_base=32'h300000;request_epoch=9;request_valid=1;
  repeat(3)begin tick;if(request_ready || source_valid)$fatal(1,"cancelled in-flight read prematurely");end
  change;expected_epoch=9;expected_base=32'h300000;seen_in_epoch=0;allow_return=1;
  wait(request_ready);tick;change;request_valid=0;
  wait_outputs(12);tick;
  if(cancelled!=1)$fatal(1,"cancelled frame counter");
  // A timed-out read stays quarantined; its late response is discarded.
  change;allow_return=0;submit(32'h400000,10);
  wait(commands==14);repeat(15)tick;
  if(!timeout_error || !busy || request_ready || cmd_en)$fatal(1,"timeout did not quarantine");
  change;request_base=32'h500000;request_epoch=11;request_valid=1;
  repeat(3)begin tick;if(request_ready)$fatal(1,"timeout read ownership released early");end
  change;expected_epoch=11;expected_base=32'h500000;seen_in_epoch=0;allow_return=1;
  wait(request_ready);tick;change;request_valid=0;
  wait_outputs(18);tick;
  if(frames!=3)$fatal(1,"completed frame count %0d",frames);
  $display("PASS scanout stride, stalls, cancellation, bounds and late-response quarantine");$finish;
end
initial begin #100000;$fatal(1,"timeout");end
endmodule
'''


class VideoReaderTest(unittest.TestCase):
    def test_scanout_transactions(self):
        iv, vvp = shutil.which('iverilog'), shutil.which('vvp')
        self.assertTrue(iv and vvp, 'iverilog/vvp required')
        m = Module()
        m.clock_domains.cd_sys = ClockDomain('sys')
        ui = DDR3UI()
        m.submodules.reader = reader = VideoFrameReader(ui, width=32, height=3, response_timeout=8)
        ports = {'sys_clk': m.cd_sys.clk, 'sys_rst': m.cd_sys.rst}
        for name in ('cmd_ready', 'rd_data_valid', 'rd_data', 'cmd', 'cmd_en', 'addr'):
            ports[name] = getattr(ui, name)
        for prefix, endpoint, names in (
            ('request', reader.request, ('valid', 'ready', 'base', 'stride', 'epoch', 'run')),
            ('source', reader.source, ('valid', 'ready', 'data', 'block', 'epoch')),
        ):
            ports.update({prefix + '_' + name: getattr(endpoint, name) for name in names})
        ports.update({name: getattr(reader, name) for name in
                      ('busy', 'pending', 'address_error', 'timeout_error', 'frames', 'cancelled')})
        for name, signal in ports.items():
            signal.name_override = name
        with tempfile.TemporaryDirectory(prefix='ae350-video-reader-') as directory:
            directory = Path(directory)
            (directory / 'dut.v').write_text(str(verilog.convert(m, ios=set(ports.values()), name='dut')))
            (directory / 'tb.v').write_text(TB)
            compiled = directory / 'test.vvp'
            subprocess.run([iv, '-g2012', '-s', 'tb', '-o', str(compiled),
                            str(directory / 'dut.v'), str(directory / 'tb.v')], check=True)
            result = subprocess.run([vvp, str(compiled)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS scanout', result.stdout)


if __name__ == '__main__':
    unittest.main()
