"""Full-MTU MAC/CDC regression against independent wire and payload oracles."""
import random
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from migen import Module,ClockDomain
from litex.gen.fhdl import verilog
from migen.fhdl.tools import list_targets
from litex.soc.interconnect import stream
from liteeth.common import eth_phy_description
from liteeth.mac.core import LiteEthMACCore
from gateware.machines.mega138k_ae350.peripherals.ethernet_rgmii import add_mac_timing_buffers

class PHY(Module):
    dw=8;tx_clk_freq=125e6;rx_clk_freq=125e6
    def __init__(self):
        self.sink=stream.Endpoint(eth_phy_description(8))
        self.source=stream.Endpoint(eth_phy_description(8))

top=Module()
clocks={}
for name in ('sys','eth_tx','eth_rx'):
    clocks[name]=ClockDomain(name);top.clock_domains += clocks[name]
top.submodules.phy=phy=PHY()
top.submodules.mac=mac=LiteEthMACCore(phy,32,with_sys_datapath=True,
                                     with_store_and_forward=True,tx_cdc_depth=8)
add_mac_timing_buffers(mac)
top.comb += [phy.sink.connect(phy.source,omit={'ready'}),phy.sink.ready.eq(1)]
ios=set()
for name in ('sys','eth_tx','eth_rx'):
    cd=clocks[name];ios.update((cd.clk,cd.rst))
for endpoint,prefix,fields in ((mac.sink,'tx',('valid','ready','data','be','last')),
                               (mac.source,'rx',('valid','ready','data','be','last','error')),
                               (phy.sink,'wire',('valid','data','last'))):
    for name in fields:
        signal=getattr(endpoint,name);signal.name_override=prefix+'_'+name;ios.add(signal)
for name,signal in (('crc_errors',mac.rx_datapath.crc_errors.status),
                    ('preamble_errors',mac.rx_datapath.preamble_errors.status),
                    ('drops',mac.rx_datapath.drops.status)):
    signal.name_override=name;ios.add(signal)
rng=random.Random(0xae350)
frames=[bytes(rng.getrandbits(8) for _ in range(n)) for n in (14,61,98,127,1514,1404,1236,1315,778,60)]
words=[];wire=bytearray();received=bytearray();wire_ends=[];rx_ends=[]
for frame in frames:
    for i in range(0,len(frame),4):
        part=frame[i:i+4]
        words.append(int.from_bytes(part,'little')|(((1<<len(part))-1)<<32)|((i+4>=len(frame))<<36))
    padded=frame.ljust(60,b'\0')
    wire+=b'\x55'*7+b'\xd5'+padded+zlib.crc32(padded).to_bytes(4,'little')
    received+=padded;wire_ends.append(len(wire));rx_ends.append(len(received))

TB=r'''
`timescale 1ns/1ps
module tb;
reg sys_clk=0,eth_tx_clk=0,eth_rx_clk=0;
always #10 sys_clk=~sys_clk;
always #4 eth_tx_clk=~eth_tx_clk;
initial begin #2;forever #4 eth_rx_clk=~eth_rx_clk;end
reg sys_rst=1,eth_tx_rst=1,eth_rx_rst=1;
reg tx_valid=0,tx_last=0,rx_ready=0;
reg [31:0]tx_data=0;
reg [3:0]tx_be=0;
wire tx_ready,rx_valid,rx_last,wire_valid,wire_last;
wire [31:0]rx_data,crc_errors,preamble_errors,drops;
wire [3:0]rx_be,rx_error;
wire [7:0]wire_data;
dut dut(.*);
reg [36:0]words[0:NWORDS-1];
reg [7:0]wire_bytes[0:WIRE_BYTES-1],rx_bytes[0:RX_BYTES-1];
integer wire_ends[0:NFRAMES-1],rx_ends[0:NFRAMES-1];
integer wp=0,wf=0,rp=0,rf=0,cycles=0,lane,gap=1000;
reg in_frame=0,held=0;
reg [40:0]held_word;
always @(negedge sys_clk)begin
  if(sys_rst)begin cycles=0;rx_ready=0;end
  else begin cycles=cycles+1;rx_ready=(cycles%7<4);end
end
always @(posedge eth_tx_clk)if(!eth_tx_rst)begin
  if(wire_valid)begin
    if(!in_frame && wf!=0 && gap<12)$fatal(1,"short IFG %0d",gap);
    if(wp>=WIRE_BYTES || wire_data!==wire_bytes[wp])$fatal(1,"wire byte %0d got%02x expected%02x",wp,wire_data,wire_bytes[wp]);
    wp=wp+1;in_frame=1;gap=0;
    if(wire_last)begin
      if(wp!=wire_ends[wf])$fatal(1,"wire frame boundary %0d",wf);
      wf=wf+1;in_frame=0;
    end
  end else begin
    if(in_frame)$fatal(1,"wire underrun");
    gap=gap+1;
  end
end
always @(posedge sys_clk)if(!sys_rst)begin
  if(held && (!rx_valid || {rx_data,rx_be,rx_last,rx_error}!==held_word))$fatal(1,"RX changed under backpressure");
  held=rx_valid&&!rx_ready;held_word={rx_data,rx_be,rx_last,rx_error};
  if(rx_valid && rx_ready)begin
    if(rx_error)$fatal(1,"RX error flags");
    for(lane=0;lane<4;lane=lane+1)if(rx_be[lane])begin
      if(rp>=RX_BYTES || rx_data[lane*8+:8]!==rx_bytes[rp])$fatal(1,"RX byte %0d",rp);
      rp=rp+1;
    end
    if(rx_last)begin
      if(rp!=rx_ends[rf])$fatal(1,"RX frame boundary %0d",rf);
      rf=rf+1;
    end
  end
end
integer i;
initial begin
  $readmemh("words.hex",words);$readmemh("wire.hex",wire_bytes);$readmemh("rx.hex",rx_bytes);
  $readmemh("wire_ends.hex",wire_ends);$readmemh("rx_ends.hex",rx_ends);
  repeat(12)@(negedge sys_clk);
  sys_rst=0;eth_tx_rst=0;eth_rx_rst=0;
  for(i=0;i<NWORDS;i=i+1)begin
    @(negedge sys_clk);tx_valid=1;tx_data=words[i][31:0];tx_be=words[i][35:32];tx_last=words[i][36];
    @(posedge sys_clk);while(!tx_ready)@(posedge sys_clk);
    if(i%11==0)begin @(negedge sys_clk);tx_valid=0;repeat(2)@(posedge sys_clk);end
  end
  @(negedge sys_clk);tx_valid=0;
  wait(rf==NFRAMES);repeat(50)@(posedge sys_clk);
  if(wf!=NFRAMES || wp!=WIRE_BYTES || rp!=RX_BYTES || crc_errors || preamble_errors || drops)
    $fatal(1,"completion/counters TX%0d RX%0d CRC%0d preamble%0d drops%0d",wf,rf,crc_errors,preamble_errors,drops);
  $display("PASS MAC32 full MTU/CDC backpressure/wrap/FCS/IFG");$finish;
end
initial begin #2000000;$fatal(1,"MAC simulation timeout");end
endmodule
'''
for key,value in dict(NWORDS=len(words),NFRAMES=len(frames),WIRE_BYTES=len(wire),RX_BYTES=len(received)).items():
    TB=TB.replace(key,str(value))
if not shutil.which('iverilog') or not shutil.which('vvp'):raise SystemExit('iverilog/vvp required')
with tempfile.TemporaryDirectory(prefix='ae350-mac-') as tmp:
    tmp=Path(tmp)
    # Icarus can repeatedly schedule separate generated blocks through
    # defaults on output signals that another block also reads. Emit the
    # same acyclic AST as one topologically ordered always_comb block so
    # intermediate default assignments cannot form a delta-event loop.
    # LiteX rejects actual combinatorial cycles before generating this block.
    original_groups=verilog.group_by_targets
    try:
        verilog.group_by_targets=lambda statements:[(set(list_targets(statements)),statements)]
        rtl=str(verilog.convert(top,ios=ios,name='dut',comb_cycle_policy='error'))
    finally:
        verilog.group_by_targets=original_groups
    (tmp/'dut.v').write_text('`timescale 1ns/1ps\n'+rtl.replace('always @(*)','always_comb'))
    (tmp/'tb.sv').write_text(TB)
    for name,values,digits in (('words',words,10),('wire',wire,2),('rx',received,2),
                               ('wire_ends',wire_ends,8),('rx_ends',rx_ends,8)):
        (tmp/(name+'.hex')).write_text(''.join(format(value,'0%dx'%digits)+'\n' for value in values))
    subprocess.run(['iverilog','-g2012','-s','tb','-o','sim','dut.v','tb.sv'],cwd=tmp,check=True,capture_output=True,text=True)
    try:
        result=subprocess.run(['vvp','sim'],cwd=tmp,capture_output=True,text=True,timeout=30)
    except subprocess.TimeoutExpired as error:
        print(error.stdout.decode() if isinstance(error.stdout,bytes) else error.stdout)
        print(error.stderr.decode() if isinstance(error.stderr,bytes) else error.stderr)
        debug=Path(tempfile.mkdtemp(prefix='ae350-mac-debug-'))
        for source in tmp.iterdir():shutil.copy(source,debug/source.name)
        print('Failed simulation files:',debug)
        raise
    print(result.stdout.strip());print(result.stderr.strip()) if result.stderr else None
    result.check_returncode()
