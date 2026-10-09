"""Run emitted ring RTL with delayed DDR/SRAM and independent MAC completions."""
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
from litex.soc.interconnect import wishbone
from gateware.memory import MemoryPort, PortBuffer
from gateware.native_dma import NativeDMAArbiter
from gateware.ethernet_ring import EthernetRingDMA

TB = r'''
module tb;
reg sys_clk=0,sys_rst=1;
always #5 sys_clk=~sys_clk;
reg control=0;
reg [31:0] rx_base=32'h1000,tx_base=32'h1100;
reg [3:0] mask=3;
reg [15:0] rx_consumer=0,tx_producer=0;
wire [15:0] rx_producer,tx_consumer;
wire busy,error,active;
wire [31:0] rx_packets,tx_packets;
reg rx_valid=0,rx_slot=0,tx_ready=1,tx_done=0;
reg [11:0] rx_length=65;
wire rx_release,tx_start,tx_slot;
wire [11:0] tx_length;
wire cyc,stb,we;wire [29:0] adr;wire [31:0] dat_w;wire [3:0] sel;
reg ack=0,err=0;reg [31:0] dat_r=0;
reg [7:0] ram[0:65535],slots[0:8191];
integer i,j,k,index,wait_count=0,tx_wait=0,starts=0,tx_index=0,transactions=0,ncommands=0;
reg pending=0,saved_we;reg [29:0] saved_adr;reg [31:0] saved_data;reg [3:0] saved_sel;
wire ncmdvalid,ncmdwe,nwvalid,nrready;
wire [22:0] naddr;wire [127:0] nwdata;wire [15:0] nmask;
reg npending=0,nwrite=0;reg [22:0] nsaved=0;integer ndelay=0;
wire ncmdready=~npending,nwready=npending && nwrite && ndelay==0;
wire nrvalid=npending && !nwrite && ndelay==0;reg [127:0] nrdata;
always @* for(integer z=0;z<16;z=z+1)nrdata[z*8+:8]=ram[nsaved*16+z];
always @(posedge sys_clk)begin
 if(ncmdvalid && ncmdready)begin
  if(naddr*16>=65536)$fatal(1,"out of bounds DDR");
  npending<=1;nwrite<=ncmdwe;nsaved<=naddr;ndelay<=7;ncommands<=ncommands+1;
 end else if(npending)begin
  if(ndelay>0)ndelay<=ndelay-1;
  else if(nwrite && nwvalid)begin
   for(k=0;k<16;k=k+1)if(nmask[k])ram[nsaved*16+k]<=nwdata[k*8+:8];
   npending<=0;
  end else if(!nwrite && nrready)npending<=0;
 end
end
ring dut(.*);
function [31:0] word_at(input integer a);
 word_at={ram[a+3],ram[a+2],ram[a+1],ram[a]};
endfunction
task put_word(input integer a,input [31:0] value);
 for(integer b=0;b<4;b=b+1)ram[a+b]=value[b*8+:8];
endtask
task descriptor(input integer a,input integer buffer,input integer length);
 put_word(a,buffer);put_word(a+4,length);put_word(a+8,32'h80000000);put_word(a+12,32'hc001cafe);
endtask
always @(posedge sys_clk)begin
 ack<=0;
 if(pending)begin
  if(!cyc || !stb || adr!==saved_adr || we!==saved_we || sel!==saved_sel || dat_w!==saved_data)
   $fatal(1,"unstable/withdrawn SRAM transaction");
  if(wait_count==0)begin
   pending<=0;ack<=1;transactions<=transactions+1;index=adr*4-32'hf1000000;
   if(index<0 || index>=8192)$fatal(1,"invalid SRAM address");
   for(j=0;j<4;j=j+1)begin
    if(we && sel[j])slots[index+j]<=dat_w[j*8+:8];
    dat_r[j*8+:8]<=slots[index+j];
   end
  end else wait_count<=wait_count-1;
 end else if(cyc && stb && !ack)begin
  pending<=1;wait_count<=3;saved_adr<=adr;saved_we<=we;saved_data<=dat_w;saved_sel<=sel;
 end
end
always @(posedge sys_clk)begin
 tx_done<=0;
 if(rx_release)rx_valid<=0;
 if(tx_start && tx_ready)begin
  starts<=starts+1;tx_wait<=35;tx_ready<=0;
  for(integer z=0;z<tx_length;z=z+1)
   if(slots[4096+tx_slot*2048+z]!==ram[32'h4000+(tx_index%4)*2048+z])$fatal(1,"TX payload mismatch");
  if(word_at(32'h1108+(tx_index%4)*16)!==32'h80000000)$fatal(1,"TX ownership released before transmission");
 end else if(tx_wait>0)begin
  if(word_at(32'h1108+(tx_index%4)*16)!==32'h80000000)$fatal(1,"early TX completion");
  tx_wait<=tx_wait-1;
  if(tx_wait==1)begin tx_done<=1;tx_ready<=1;tx_index<=tx_index+1;end
 end
end
task receive_packet(input integer size,input integer seq);
 @(negedge sys_clk);
 rx_slot=seq%2;rx_length=size;
 for(integer z=0;z<size;z=z+1)slots[rx_slot*2048+z]=(z*17+seq*13)&255;
 rx_valid=1;
 wait(rx_producer==seq+1);@(negedge sys_clk);
 for(integer z=0;z<size;z=z+1)
  if(ram[32'h2000+(seq%4)*2048+z]!==((z*17+seq*13)&255))$fatal(1,"RX payload mismatch at %0d",z);
 if(ram[32'h2000+(seq%4)*2048+size]!==8'he7)$fatal(1,"RX tail overwrote sentinel");
 if(word_at(32'h1008+(seq%4)*16)!==(32'h40000000|size))$fatal(1,"RX status");
 if(word_at(32'h100c+(seq%4)*16)!==32'hc001cafe)$fatal(1,"cookie overwritten");
endtask
integer saved;
initial begin
 for(i=0;i<65536;i=i+1)ram[i]=8'he7;
 for(i=0;i<8192;i=i+1)slots[i]=0;
 for(i=0;i<4;i=i+1)begin
  descriptor(32'h1000+i*16,32'h2000+i*2048,1536);
  descriptor(32'h1100+i*16,32'h4000+i*2048,65+i);
  for(j=0;j<68;j=j+1)ram[32'h4000+i*2048+j]=(j*37+i*11)&255;
 end
 repeat(5)@(negedge sys_clk);sys_rst=0;
 // Invalid boot-RAM ring cannot issue any DMA traffic.
 rx_base=32'h007ff000;control=1;repeat(30)@(negedge sys_clk);
 if(!error || ncommands || cyc)$fatal(1,"invalid ring accessed memory");
 control=0;repeat(5)@(negedge sys_clk);rx_base=32'h1000;control=1;tx_producer=4;
 // No per-packet CPU DMA command: four TX descriptors were published once.
 receive_packet(14,0);receive_packet(17,1);receive_packet(65,2);receive_packet(1530,3);
 wait(tx_consumer==4);@(negedge sys_clk);
 if(starts!=4 || tx_packets!=4 || rx_packets!=4 || error)$fatal(1,"ring counts/fairness");
 for(i=0;i<4;i=i+1)if(word_at(32'h1108+i*16)!==(32'h40000000|(65+i)))$fatal(1,"TX completion");
 // Full RX ring must retain the MAC packet without touching old buffers.
 rx_slot=0;rx_length=31;for(i=0;i<31;i=i+1)slots[i]=(i*17+4*13)&255;rx_valid=1;
 saved=transactions;repeat(100)@(negedge sys_clk);
 if(rx_producer!=4 || transactions!=saved || !rx_valid)$fatal(1,"full ring overwrite/release");
 descriptor(32'h1000,32'h2000,1536);ram[32'h2000+31]=8'he7;rx_consumer=1;
 wait(rx_producer==5);@(negedge sys_clk);
 if(word_at(32'h1008)!==32'h4000001f)$fatal(1,"RX wraparound");
 // Invalid payload consumes the owned descriptor with ERROR, no SRAM read.
 descriptor(32'h1010,32'h2801,1536);rx_consumer=2;rx_length=64;rx_valid=1;
 saved=transactions;wait(rx_producer==6);@(negedge sys_clk);
 if(word_at(32'h1018)!==32'h60000040 || transactions!=saved || !error)$fatal(1,"bad descriptor");
 // A TX larger than the MAC MTU must error without staging or starting it.
 descriptor(32'h1100,32'h4000,1536);saved=transactions;tx_producer=5;
 wait(tx_consumer==5);@(negedge sys_clk);
 if(word_at(32'h1108)!==32'h60000600 || starts!=4 || transactions!=saved)$fatal(1,"oversized TX descriptor");
 // Stop while SRAM ACK is delayed, then restart with fresh ring ownership.
 control=0;wait(!busy);repeat(12)@(negedge sys_clk);
 descriptor(32'h1000,32'h2000,1536);tx_producer=0;rx_consumer=0;control=1;rx_valid=1;rx_length=65;
 wait(pending && !we);@(negedge sys_clk);control=0;
 wait(!busy);repeat(20)@(negedge sys_clk);
 if(cyc || rx_release || rx_producer || tx_consumer)$fatal(1,"stop did not drain/reset");
 rx_valid=0;descriptor(32'h1000,32'h2000,1536);ram[32'h2000+33]=8'he7;control=1;
 receive_packet(33,0);
 $display("Ethernet ring RTL PASS: autonomous RX/TX, fairness, full/backpressure, wrap, tails, committed ownership, invalid descriptors, stop/drain/restart");$finish;
end
initial begin #10000000;$fatal(1,"timeout: RX=%0d TX=%0d",rx_producer,tx_consumer);end
endmodule
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iverilog',required=True)
    compiler=Path(p.parse_args().iverilog).resolve()
    dut=Module()
    backend=LiteDRAMNativePort('both',23,128)
    dut.submodules.arbiter=arb=NativeDMAArbiter(backend,2)
    reader,writer=MemoryPort('read'),MemoryPort('write')
    dut.submodules.read_buffer=PortBuffer(reader,arb.ports[0])
    dut.submodules.write_buffer=PortBuffer(writer,arb.ports[1])
    bus=wishbone.Interface(data_width=32,address_width=32,addressing='word')
    dut.submodules.ring=ring=EthernetRingDMA(bus,reader,writer)
    ports={name:getattr(ring,'_'+name).storage for name in
        ('control','rx_base','tx_base','mask','rx_consumer','tx_producer')}
    ports.update({name:getattr(ring,'_'+name).status for name in
        ('rx_producer','tx_consumer','busy','error','rx_packets','tx_packets')})
    ports.update({name:getattr(ring,name) for name in
        ('active','rx_valid','rx_slot','rx_length','rx_release','tx_ready','tx_done','tx_start','tx_slot','tx_length')})
    ports.update({name:getattr(bus,name) for name in ('cyc','stb','we','adr','dat_w','sel','ack','err','dat_r')})
    ports.update(ncmdvalid=backend.cmd.valid,ncmdready=backend.cmd.ready,naddr=backend.cmd.addr,
        ncmdwe=backend.cmd.we,nwvalid=backend.wdata.valid,nwready=backend.wdata.ready,
        nwdata=backend.wdata.data,nmask=backend.wdata.we,nrvalid=backend.rdata.valid,
        nrready=backend.rdata.ready,nrdata=backend.rdata.data)
    for name,signal in ports.items():signal.name_override=name
    with tempfile.TemporaryDirectory(prefix='eth-ring-rtl-',dir=Path.cwd()/'build') as tmp:
        out=Path(tmp)
        env=dict(os.environ,PATH=str(compiler.parent)+os.pathsep+os.environ.get('PATH',''),TEMP=tmp,TMP=tmp,TMPDIR=tmp)
        (out/'ring.v').write_text(str(verilog.convert(dut,ios=set(ports.values()),name='ring')))
        (out/'tb.v').write_text(TB)
        subprocess.run([str(compiler),'-g2012','-s','tb','-o',str(out/'sim'),str(out/'ring.v'),str(out/'tb.v')],env=env,check=True)
        subprocess.run([str(compiler.with_name('vvp.exe')),str(out/'sim')],env=env,check=True,timeout=30)


if __name__=='__main__':main()
