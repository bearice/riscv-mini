"""DDR read ownership, atomic writes, ready stalls and round-robin fairness."""
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
from gateware.machines.mega138k_ae350.video_memory import VideoMemoryArbiter

TB = r'''
module tb;
reg sys_clk=0,sys_rst=1;
always #5 sys_clk=~sys_clk;
reg cpu_pending=0,video_pending=0,cpu_cmd_en=0,video_cmd_en=0;
reg [2:0] cpu_cmd=1;
reg [28:0] cpu_addr=29'h100,video_addr=29'h200;
reg cpu_wr_data_en=0;
reg [255:0] cpu_wr_data=256'h123456789abcdef;
reg [31:0] cpu_wr_data_mask=32'hfedcba98;
reg memory_cmd_ready=0,memory_wr_data_rdy=0,memory_rd_data_valid=0;
reg [255:0] memory_rd_data=0;
wire cpu_cmd_ready,cpu_wr_data_rdy,video_cmd_ready,cpu_rd_data_valid,video_rd_data_valid;
wire [255:0] cpu_rd_data,video_rd_data,memory_wr_data;
wire [31:0] memory_wr_data_mask,cpu_commands,video_commands,max_video_wait;
wire [2:0] memory_cmd;
wire [28:0] memory_addr;
wire memory_cmd_en,memory_wr_data_en,memory_wr_data_end,read_pending,unexpected_response;
dut dut(.*);
task tick; begin @(posedge sys_clk); #1; end endtask
task change; begin @(negedge sys_clk); end endtask
task response(input bit video_owner,input [255:0] data);
begin
  change; memory_rd_data=data;memory_rd_data_valid=1;#1;
  if(video_owner)begin
    if(!video_rd_data_valid || cpu_rd_data_valid || video_rd_data!==data)$fatal(1,"video response isolation");
  end else begin
    if(!cpu_rd_data_valid || video_rd_data_valid || cpu_rd_data!==data)$fatal(1,"CPU response isolation");
  end
  tick;change;memory_rd_data_valid=0;
end endtask
integer i;
initial begin
  repeat(3)tick;change;sys_rst=0;
  cpu_pending=1;video_pending=1;cpu_cmd_en=1;video_cmd_en=1;
  memory_cmd_ready=1;memory_wr_data_rdy=1;#1;
  if(!cpu_cmd_ready || video_cmd_ready || memory_addr!==cpu_addr)$fatal(1,"initial grant");
  tick;if(!read_pending)$fatal(1,"read ownership not retained");
  change;cpu_pending=0;cpu_cmd_en=0;
  // A client may time out and withdraw demand. Its response still owns the UI.
  repeat(12)begin
    #1;if(memory_cmd_en || video_cmd_ready || cpu_rd_data_valid || video_rd_data_valid)$fatal(1,"read stolen before late response");
    tick;change;
  end
  response(0,256'hdeadbeef);
  #1;if(!video_cmd_ready || memory_addr!==video_addr)$fatal(1,"video lost waiting grant");
  tick;change;video_pending=0;video_cmd_en=0;
  repeat(4)tick;
  response(1,256'hcafebabe);
  // Neither half of an atomic write may be accepted alone.
  cpu_pending=1;cpu_cmd=0;cpu_cmd_en=1;cpu_wr_data_en=1;
  memory_wr_data_rdy=0;#1;
  if(memory_cmd_en || memory_wr_data_en || cpu_cmd_ready)$fatal(1,"write with no data space");
  repeat(3)tick;change;memory_wr_data_rdy=1;memory_cmd_ready=0;#1;
  if(memory_wr_data_en || cpu_cmd_ready || cpu_wr_data_rdy)$fatal(1,"data without command");
  repeat(3)tick;change;memory_cmd_ready=1;#1;
  if(!memory_cmd_en || !memory_wr_data_en || !memory_wr_data_end ||
     memory_wr_data!==cpu_wr_data || memory_wr_data_mask!==cpu_wr_data_mask)$fatal(1,"atomic write payload");
  tick;change;cpu_pending=0;cpu_cmd_en=0;cpu_wr_data_en=0;
  // Saturated clients must alternate; read latency cannot change ownership.
  cpu_pending=1;video_pending=1;cpu_cmd=1;cpu_cmd_en=1;video_cmd_en=1;
  for(i=0;i<10;i=i+1)begin
    #1;
    if(video_cmd_ready !== (i%2==0) || cpu_cmd_ready !== (i%2==1))$fatal(1,"round robin %0d",i);
    tick;repeat(3)tick;
    response(i%2==0,256'h1000+i);
  end
  cpu_pending=0;video_pending=0;cpu_cmd_en=0;video_cmd_en=0;
  #1;if(cpu_commands!=7 || video_commands!=6)$fatal(1,"command counters %0d/%0d",cpu_commands,video_commands);
  if(max_video_wait<12)$fatal(1,"wait interval was not observed");
  memory_rd_data_valid=1;#1;
  if(cpu_rd_data_valid || video_rd_data_valid)$fatal(1,"unsolicited response delivered");
  tick;if(!unexpected_response)$fatal(1,"unsolicited response not flagged");
  $display("PASS DDR video arbitration: late responses, writes, stalls and fairness");$finish;
end
initial begin #10000;$fatal(1,"timeout");end
endmodule
'''


class VideoMemoryTest(unittest.TestCase):
    def test_transaction_ownership(self):
        iv, vvp = shutil.which('iverilog'), shutil.which('vvp')
        self.assertTrue(iv and vvp, 'iverilog/vvp required')
        m = Module()
        m.clock_domains.cd_sys = ClockDomain('sys')
        memory, cpu, video = DDR3UI(), DDR3UI(), DDR3UI()
        m.submodules.arbiter = arbiter = VideoMemoryArbiter(memory, cpu, video)
        ports = {'sys_clk': m.cd_sys.clk, 'sys_rst': m.cd_sys.rst}
        for prefix, ui, names in (
            ('memory', memory, ('cmd_ready', 'wr_data_rdy', 'rd_data_valid', 'rd_data',
                               'cmd', 'cmd_en', 'addr', 'wr_data', 'wr_data_mask', 'wr_data_en', 'wr_data_end')),
            ('cpu', cpu, ('cmd', 'cmd_en', 'addr', 'wr_data', 'wr_data_mask', 'wr_data_en',
                         'cmd_ready', 'wr_data_rdy', 'rd_data_valid', 'rd_data')),
            ('video', video, ('cmd_en', 'addr', 'cmd_ready', 'rd_data_valid', 'rd_data')),
        ):
            ports.update({prefix + '_' + name: getattr(ui, name) for name in names})
        ports.update({name: getattr(arbiter, name) for name in
                      ('cpu_pending', 'video_pending', 'cpu_commands', 'video_commands',
                       'max_video_wait', 'read_pending', 'unexpected_response')})
        for name, signal in ports.items():
            signal.name_override = name
        with tempfile.TemporaryDirectory(prefix='ae350-video-arbiter-') as directory:
            directory = Path(directory)
            (directory / 'dut.v').write_text(str(verilog.convert(m, ios=set(ports.values()), name='dut')))
            (directory / 'tb.v').write_text(TB)
            compiled = directory / 'test.vvp'
            subprocess.run([iv, '-g2012', '-s', 'tb', '-o', str(compiled),
                            str(directory / 'dut.v'), str(directory / 'tb.v')], check=True)
            result = subprocess.run([vvp, str(compiled)], text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS DDR video', result.stdout)


if __name__ == '__main__':
    unittest.main()
