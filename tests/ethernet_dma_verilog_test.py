"""Exercise emitted packet DMA RTL against delayed Wishbone RAM/slot models."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migen import Module
from migen.fhdl import verilog
from litedram.common import LiteDRAMNativePort
from gateware.memory import MemoryPort, PortBuffer
from gateware.native_dma import NativeDMAArbiter
from litex.soc.interconnect import wishbone
from gateware.ethernet_dma import EthernetDMA

TB = r'''
module tb;
reg sys_clk=0,sys_rst=1;
always #5 sys_clk=~sys_clk;
reg [1:0] control=0;
reg [31:0] memory_base=0,slot_base=0;
reg [11:0] length=0;
wire busy,done,error,cyc,stb,we;
wire [29:0] adr;
wire [31:0] dat_w;
wire [3:0] sel;
reg ack=0,err=0;
reg [31:0] dat_r=0;
reg [7:0] ram[0:2047],slots[0:8191];
integer wait_count=0,transactions=0,i,j,n,index,saved_transactions;
reg pending=0,saved_we;
reg [29:0] saved_adr;
reg [31:0] saved_data;
reg [3:0] saved_sel;
wire ncmdvalid,ncmdwe,nwvalid,nrready;
wire [22:0] naddr;
wire [127:0] nwdata;
wire [15:0] nmask;
reg npending=0,nwrite=0;
reg [22:0] nsaved=0;
integer ndelay=0,k,ni;
wire ncmdready=~npending;
wire nwready=npending && nwrite && ndelay==0;
wire nrvalid=npending && !nwrite && ndelay==0;
reg [127:0] nrdata;
always @* begin
 for(integer z=0;z<16;z=z+1)nrdata[z*8+:8]=ram[nsaved*16+z];
end
always @(posedge sys_clk)begin
 if(ncmdvalid && ncmdready)begin npending<=1;nwrite<=ncmdwe;nsaved<=naddr;ndelay<=6;end
 else if(npending)begin
  if(ndelay>0)ndelay<=ndelay-1;
  else if(nwrite && nwvalid)begin
   for(k=0;k<16;k=k+1)if(nmask[k])ram[nsaved*16+k]<=nwdata[k*8+:8];
   npending<=0;
  end else if(!nwrite && nrready)npending<=0;
 end
end
dma dut(.*);
always @(posedge sys_clk) begin
 ack<=0;err<=0;
 if(pending)begin
  if(!cyc || !stb || adr!==saved_adr || we!==saved_we || sel!==saved_sel || dat_w!==saved_data)
   $fatal(1,"withdrawn or unstable bus cycle");
  if(wait_count==0)begin
   pending<=0;ack<=1;transactions<=transactions+1;
   index=adr*4;
   for(j=0;j<4;j=j+1)begin
    if(index>=32'h40000000 && index<32'h40000800)begin
     if(we && sel[j])ram[index-32'h40000000+j]<=dat_w[8*j+:8];
     dat_r[8*j+:8]<=ram[index-32'h40000000+j];
    end else if(index>=32'hb0000000 && index<32'hb0002000)begin
     if(we && sel[j])slots[index-32'hb0000000+j]<=dat_w[8*j+:8];
     dat_r[8*j+:8]<=slots[index-32'hb0000000+j];
    end else $fatal(1,"invalid bus address %h",index);
   end
  end else wait_count<=wait_count-1;
 end else if(cyc && stb && !ack)begin
  pending<=1;wait_count<=3;saved_adr<=adr;saved_we<=we;saved_sel<=sel;saved_data<=dat_w;
 end
end
task reset_command;
begin control=0;wait(!busy);repeat(4)@(negedge sys_clk);end
endtask
task copy;
input integer receive,bytes;
begin
 reset_command();memory_base=32'h40000000;slot_base=receive?32'hb0000000:32'hb0001000;
 length=bytes;control=receive?3:1;wait(done);#1;
 if(error || busy)$fatal(1,"copy failed");
 for(i=0;i<bytes;i=i+1)begin
  if(receive)begin if(ram[i]!==slots[i])$fatal(1,"RX byte %d",i);end
  else if(slots[4096+i]!==ram[i])$fatal(1,"TX byte %d",i);
 end
 if(receive && ram[bytes]!==8'he7)$fatal(1,"tail overwrite %d",bytes);
end
endtask
initial begin
 for(i=0;i<2048;i=i+1)ram[i]=(i*7+3)&255;
 for(i=0;i<8192;i=i+1)slots[i]=(i*13+9)&255;
 repeat(3)@(negedge sys_clk);sys_rst=0;
 for(n=1;n<=7;n=n+1)begin copy(0,n);for(i=0;i<2048;i=i+1)ram[i]=8'he7;copy(1,n);end
 copy(0,1514);for(i=0;i<2048;i=i+1)ram[i]=8'he7;copy(1,1514);
 copy(0,1536);for(i=0;i<2048;i=i+1)ram[i]=8'he7;copy(1,1536);
 reset_command();saved_transactions=transactions;
 memory_base=32'h40000001;length=64;slot_base=32'hb0001000;control=1;
 wait(done);#1;if(!error || transactions!=saved_transactions)$fatal(1,"invalid alignment accepted");
 reset_command();memory_base=32'h407ff000;control=1;wait(done);#1;
 if(!error || transactions!=saved_transactions)$fatal(1,"boot RAM accepted");
 reset_command();memory_base=32'h40000000;slot_base=32'hb0000000;control=1;wait(done);#1;
 if(!error || transactions!=saved_transactions)$fatal(1,"wrong slot direction accepted");
 reset_command();slot_base=32'hb0000000;control=3;
 wait(pending && !we);@(negedge sys_clk);control=0;wait(!busy);repeat(5)@(negedge sys_clk);
 if(transactions!=saved_transactions+1 || cyc)$fatal(1,"cancel did not drain just read cycle");
 copy(0,64);
 reset_command();slot_base=32'hb0001000;control=1;wait(pending && we);@(negedge sys_clk);control=0;
 wait(!busy);repeat(5)@(negedge sys_clk);if(cyc)$fatal(1,"cancelled write stuck");
 for(i=0;i<2048;i=i+1)ram[i]=8'he7;copy(1,65);
 $display("Ethernet native DMA RTL PASS: RX/TX, tails, delayed ACK, invalid descriptors, cancel/drain/restart");$finish;
end
initial begin #1000000;$fatal(1,"timeout");end
endmodule
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iverilog',required=True)
    args=p.parse_args();compiler=Path(args.iverilog).resolve()
    bus=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    dut=Module()
    backend=LiteDRAMNativePort('both',23,128)
    dut.submodules.arbiter=arbiter=NativeDMAArbiter(backend,2)
    reader,writer=MemoryPort('read'),MemoryPort('write')
    dut.submodules.read_buffer=PortBuffer(reader,arbiter.ports[0])
    dut.submodules.write_buffer=PortBuffer(writer,arbiter.ports[1])
    dut.submodules.engine=engine=EthernetDMA(bus,reader,writer)
    ports=dict(control=engine._control.storage,memory_base=engine._memory.storage,
        slot_base=engine._slot.storage,length=engine._length.storage,busy=engine._busy.status,
        done=engine._done.status,error=engine._error.status,
        **{n:getattr(bus,n) for n in ('cyc','stb','we','adr','dat_w','sel','ack','err','dat_r')})
    ports.update(ncmdvalid=backend.cmd.valid,ncmdready=backend.cmd.ready,naddr=backend.cmd.addr,
        ncmdwe=backend.cmd.we,nwvalid=backend.wdata.valid,nwready=backend.wdata.ready,
        nwdata=backend.wdata.data,nmask=backend.wdata.we,nrvalid=backend.rdata.valid,
        nrready=backend.rdata.ready,nrdata=backend.rdata.data)
    for name,signal in ports.items():signal.name_override=name
    with tempfile.TemporaryDirectory(prefix='eth-dma-rtl-',dir=Path.cwd()/'build') as directory:
        output=Path(directory)
        env=dict(os.environ,PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''),
                 TEMP=output.as_posix(),TMP=output.as_posix(),TMPDIR=output.as_posix())
        (output/'dma.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='dma')))
        (output/'tb.v').write_text(TB)
        subprocess.run([str(compiler),'-g2012','-s','tb','-o',(output/'sim').as_posix(),
                        (output/'dma.v').as_posix(),(output/'tb.v').as_posix()],env=env,check=True)
        subprocess.run([str(compiler.with_name('vvp.exe')),(output/'sim').as_posix()],env=env,check=True,timeout=30)


if __name__=='__main__':main()
