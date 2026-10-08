"""Check the emitted SD packer's byte mask, rather than Migen shift semantics."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen.fhdl import verilog
from litedram.common import LiteDRAMNativePort
from gateware.native_dma import NativeSDTransfer

TB = r'''
module tb;
reg sys_clk=0,sys_rst=1;
always #5 sys_clk=~sys_clk;
reg enable=0,valid=0;
reg [31:0] data=0;
wire ready,done,error,cmd_valid,wvalid;
wire [127:0] wdata;
wire [15:0] mask;
integer i;
packer dut(.sys_clk(sys_clk),.sys_rst(sys_rst),.base(32'h00810000),
 .length(13'd16),.enable(enable),.valid(valid),.data(data),.ready(ready),
 .done(done),.error(error),.cmd_valid(cmd_valid),.cmd_ready(1'b1),
 .wvalid(wvalid),.wready(1'b1),.wdata(wdata),.mask(mask));
initial begin
 repeat(3)@(negedge sys_clk);sys_rst=0;enable=1;
 for(i=0;i<4;i=i+1)begin
  @(negedge sys_clk);valid=1;data=32'h11223344+i;
  @(posedge sys_clk);while(!ready)@(posedge sys_clk);
  @(negedge sys_clk);valid=0;
 end
 wait(wvalid);
 if(mask!==16'hffff)$fatal(1,"SD beat mask %04x != ffff",mask);
 if(wdata!==128'h47332211463322114533221144332211)$fatal(1,"SD packed data %032x",wdata);
 wait(done);if(error)$fatal(1,"DMA error");
 $display("Native SD emitted Verilog PASS: four packed words and full byte mask");$finish;
end
initial begin #10000;$fatal(1,"timeout");end
endmodule
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog',required=True)
    args=parser.parse_args();compiler=Path(args.iverilog).resolve()
    runner=compiler.with_name('vvp.exe' if compiler.suffix=='.exe' else 'vvp')
    port=LiteDRAMNativePort('both',23,128);dut=NativeSDTransfer(port,True)
    ports={'base':dut._base.storage,'length':dut._length.storage,'enable':dut._enable.storage,
           'done':dut._done.status,'error':dut._error.status,'valid':dut.sink.valid,
           'ready':dut.sink.ready,'data':dut.sink.data,'cmd_valid':port.cmd.valid,
           'cmd_ready':port.cmd.ready,'wvalid':port.wdata.valid,'wready':port.wdata.ready,
           'wdata':port.wdata.data,'mask':port.wdata.we}
    for name,signal in ports.items():signal.name_override=name
    with tempfile.TemporaryDirectory(prefix='sd-rtl-') as directory:
        output=Path(directory).resolve()
        env=dict(os.environ,PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''),
                 TMP=output.as_posix(),TMPDIR=output.as_posix())
        (output/'packer.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='packer')))
        (output/'tb.v').write_text(TB)
        subprocess.run([str(compiler),'-g2012','-s','tb','-o',(output/'sim').as_posix(),
                        (output/'packer.v').as_posix(),(output/'tb.v').as_posix()],env=env,check=True)
        subprocess.run([str(runner),(output/'sim').as_posix()],env=env,check=True,timeout=30)

if __name__=='__main__':main()
