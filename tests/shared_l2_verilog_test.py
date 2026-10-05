"""Exercise CPU byte lanes in emitted RTL, including Verilog operand sizing.

Migen simulation alone does not reproduce self-determined arithmetic widths
inside a Verilog shift operand. Run with Icarus Verilog on PATH, or --iverilog.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen.fhdl import verilog
from litex.soc.interconnect import wishbone
from litedram.common import LiteDRAMNativePort
from gateware.shared_l2 import SharedL2
from migen import Signal

TB = r'''
module tb;
reg sys_clk=0, sys_rst=1;
always #5 sys_clk=~sys_clk;
reg [29:0] cpu_adr=0;
reg [31:0] cpu_dat_w=0;
reg [3:0] cpu_sel=15;
reg cpu_cyc=0, cpu_stb=0, cpu_we=0;
wire cpu_ack;
wire [31:0] cpu_dat_r;
wire cmd_valid, cmd_we, wdata_valid, rdata_ready;
wire [22:0] cmd_addr;
wire [127:0] wdata_data;
wire [15:0] wdata_we;
reg rdata_valid=0;
reg [127:0] rdata_data=0;
reg [127:0] ram [0:511];
reg [8:0] write_addr=0;
integer i,b,lane;
reg [31:0] result, expected [0:3];
cache dut(.sys_clk(sys_clk),.sys_rst(sys_rst),
 .cpu_adr(cpu_adr),.cpu_dat_w(cpu_dat_w),.cpu_sel(cpu_sel),
 .cpu_cyc(cpu_cyc),.cpu_stb(cpu_stb),.cpu_we(cpu_we),
 .cpu_ack(cpu_ack),.cpu_dat_r(cpu_dat_r),
 .cmd_valid(cmd_valid),.cmd_ready(1'b1),.cmd_we(cmd_we),.cmd_addr(cmd_addr),
 .wdata_valid(wdata_valid),.wdata_ready(1'b1),.wdata_data(wdata_data),.wdata_we(wdata_we),
 .rdata_valid(rdata_valid),.rdata_ready(rdata_ready),.rdata_data(rdata_data));
always @(posedge sys_clk) begin
 rdata_valid<=0;
 if(cmd_valid) begin
  if(cmd_we) write_addr<=cmd_addr[8:0];
  else begin rdata_valid<=1; rdata_data<=ram[cmd_addr[8:0]]; end
 end
 if(wdata_valid)
  for(integer j=0;j<16;j=j+1)
   if(wdata_we[j]) ram[write_addr][8*j+:8]<=wdata_data[8*j+:8];
end
task access(input [31:0] addr,input wr,input [31:0] data,input [3:0] sel);
 begin
  @(negedge sys_clk);
  cpu_adr=addr>>2; cpu_we=wr; cpu_dat_w=data; cpu_sel=sel; cpu_cyc=1; cpu_stb=1;
  @(posedge sys_clk);
  while(!cpu_ack) @(posedge sys_clk);
  result=cpu_dat_r;
  @(negedge sys_clk); cpu_cyc=0; cpu_stb=0;
 end
endtask
initial begin
 for(i=0;i<512;i=i+1)ram[i]=0;
 repeat(3) @(negedge sys_clk); sys_rst=0;
 repeat(25) @(negedge sys_clk);
 // Four stores to the exact boot-stack line that exposed the hardware bug.
 for(lane=0;lane<4;lane=lane+1) begin
  expected[lane]=32'h11223344+lane;
  access(32'h407ffff0+4*lane,1,expected[lane],15);
 end
 for(lane=0;lane<4;lane=lane+1) begin
  access(32'h407ffff0+4*lane,0,0,15);
  if(result!==expected[lane]) $fatal(1,"word lane %0d: %08x != %08x",lane,result,expected[lane]);
 end
 // All sixteen byte enables must preserve neighbouring bytes and words.
 for(lane=0;lane<4;lane=lane+1)
  for(b=0;b<4;b=b+1) begin
   access(32'h407ffff0+4*lane,1,(32'h80+4*lane+b)<<(8*b),1<<b);
   expected[lane]=(expected[lane]&~(32'hff<<(8*b)))|((32'h80+4*lane+b)<<(8*b));
  end
 for(lane=0;lane<4;lane=lane+1) begin
  access(32'h407ffff0+4*lane,0,0,15);
  if(result!==expected[lane]) $fatal(1,"byte lane %0d: %08x != %08x",lane,result,expected[lane]);
 end
 // A conflicting CPU line forces dirty writeback, then reload the old line.
 access(32'h407ffef0,0,0,15);
 for(lane=0;lane<4;lane=lane+1) begin
  access(32'h407ffff0+4*lane,0,0,15);
  if(result!==expected[lane]) $fatal(1,"eviction lane %0d: %08x != %08x",lane,result,expected[lane]);
 end
 $display("Shared L2 emitted Verilog PASS: four words, sixteen bytes, eviction");
 $finish;
end
initial begin #100000; $fatal(1,"timeout"); end
endmodule
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog', default=shutil.which('iverilog'))
    args = parser.parse_args()
    if not args.iverilog:
        parser.error('Icarus Verilog is required; supply --iverilog')
    compiler = Path(args.iverilog).resolve()
    runner = compiler.with_name('vvp.exe' if compiler.suffix == '.exe' else 'vvp')
    env = dict(os.environ, PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH', ''))
    for writeback,boot in ((False,False),(True,False),(True,True)):
        wb = wishbone.Interface(data_width=32, address_width=32, addressing='word')
        native = LiteDRAMNativePort('both', 23, 128)
        ready=Signal(reset=0 if boot else 1)
        dut = SharedL2(wb, native, size=4096 if boot else 256, writeback=writeback,
                       enabled=ready if boot else 1,boot_ram=boot)
        ports = {}
        if boot:ports['ddr_ready']=ready
        for field in ('adr', 'dat_w', 'sel', 'cyc', 'stb', 'we', 'ack', 'dat_r'):
            ports['cpu_'+field] = getattr(wb, field)
        for group, fields in [('cmd', ('valid', 'ready', 'we', 'addr')),
                              ('wdata', ('valid', 'ready', 'data', 'we')),
                              ('rdata', ('valid', 'ready', 'data'))]:
            for field in fields:
                ports[group+'_'+field] = getattr(getattr(native, group), field)
        for name, signal in ports.items():
            signal.name_override = name
        with tempfile.TemporaryDirectory(prefix='shared-l2-rtl-') as directory:
            output = Path(directory).resolve()
            # MinGW Icarus expects forward slashes in its temporary directory.
            run_env = dict(env, TMP=output.as_posix(), TMPDIR=output.as_posix())
            (output/'cache.v').write_text(str(verilog.convert(dut, ios=set(ports.values()), name='cache')))
            bench=TB
            if boot:
                bench=bench.replace('reg [29:0] cpu_adr=0;', 'reg ddr_ready=0;\nreg [29:0] cpu_adr=0;')
                bench=bench.replace('cache dut(.sys_clk', 'cache dut(.ddr_ready(ddr_ready),.sys_clk')
                bench=bench.replace('if(cmd_valid) begin', 'if(cmd_valid && !ddr_ready) $fatal(1,"DDR request before ready");\n if(cmd_valid) begin')
                bench=bench.replace('// A conflicting CPU line', 'ddr_ready=1; repeat(2) @(negedge sys_clk);\n // A conflicting CPU line')
                bench=bench.replace("32'h407ffef0","32'h407feff0")
                bench=bench.replace('repeat(300)', 'repeat(600)')
            (output/'tb.v').write_text(bench)
            subprocess.run([str(compiler), '-g2012', '-s', 'tb', '-o', (output/'sim').as_posix(),
                            (output/'cache.v').as_posix(), (output/'tb.v').as_posix()], env=run_env, check=True)
            print('writeback =', writeback, 'boot_ram =',boot, flush=True)
            subprocess.run([str(runner), (output/'sim').as_posix()], env=run_env, check=True, timeout=30)


if __name__ == '__main__':
    main()
