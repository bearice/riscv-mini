"""Real OHCI burst writebacks through the asynchronous DDR word adapter."""
import runpy
import sys
import tempfile
import subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from migen.fhdl import verilog
from litex.soc.interconnect import wishbone
from gateware.machines.mega138k_ae350.peripherals.usb_ohci import OHCIDDRPort
reference=runpy.run_path(str(Path(__file__).with_name('ohci_core_test.py')))
b=wishbone.Interface(data_width=32,address_width=32,addressing='word')
m=OHCIDDRPort(b);ios=set()
for n in ('cyc','stb','we','adr','dat_w','sel','ack','err','dat_r'):
 s=getattr(b,n);s.name_override='wb_'+n;ios.add(s)
for n in ('addr','cmd','cmd_en','cmd_ready','wr_data','wr_data_mask','wr_data_en','wr_data_end','wr_data_rdy','rd_data','rd_data_valid','burst'):
 s=getattr(m.memory.ui,n);s.name_override='ui_'+n;ios.add(s)
rtl=str(verilog.convert(m,ios=ios,name='adapter'))
bench=reference['bench']
bench=bench.replace('integer dma_writes=0;',r'''
wire dack,derr;wire[31:0]dread,dwrite;wire[3:0]dsel;
wire[28:0]ua;wire[2:0]uc;wire uce,uwe;wire[255:0]uwd;wire[31:0]umask;
reg ddr=0;always #5 ddr=~ddr;
reg[255:0]urd=0;reg urv=0;reg[31:0]ram[0:4095];integer i,waitread=0;reg[28:0]raddr;
adapter bridge(.sys_clk(clk),.sys_rst(reset),.ddr_clk(ddr),.ddr_rst(reset),
.wb_cyc(dc),.wb_stb(ds),.wb_we(dw),.wb_adr(da),.wb_sel(dsel),.wb_dat_w(dwrite),
.wb_dat_r(dread),.wb_ack(dack),.wb_err(derr),
.ui_cmd_ready(1'b1),.ui_wr_data_rdy(1'b1),.ui_cmd(uc),.ui_cmd_en(uce),
.ui_addr(ua),.ui_wr_data(uwd),.ui_wr_data_mask(umask),.ui_wr_data_en(uwe),
.ui_rd_data(urd),.ui_rd_data_valid(urv));
always @(posedge ddr)begin
 urv<=0;
 if(waitread>0)begin
  waitread<=waitread-1;
  if(waitread==1)begin
   for(i=0;i<8;i=i+1)urd[i*32+:32]<=ram[(raddr&~7)+i];
   urv<=1;
  end
 end
 if(!reset&&uce)begin
  if(uc==1)begin raddr<=ua;waitread<=3;end
  else if(uwe)begin
   for(i=0;i<32;i=i+1)if(!umask[i])ram[(ua&~7)+i/4][8*(i%4)+:8]<=uwd[8*i+:8];
  end
 end
end
integer dma_writes=0;''')
bench=bench.replace('.io_dma_ACK(dc&&ds),.io_dma_ERR(1\'b0),.io_dma_DAT_MISO(32\'b0),',
'.io_dma_ACK(dack),.io_dma_ERR(derr),.io_dma_DAT_MISO(dread),.io_dma_DAT_MOSI(dwrite),.io_dma_SEL(dsel),')
bench=bench.replace('initial begin\nrepeat(10)',r'''initial begin
for(i=0;i<4096;i=i+1)ram[i]=0;
ram['h220/4]='h08080000;
ram['h228/4]='h230;
ram['h230/4]='he2040001;
ram['h234/4]='h300;
ram['h23c/4]='h307;
repeat(10)''')
bench=bench.replace("access(1,'h34,'h2edf);access(1,'h04,'h80);", "access(1,'h20,'h220);access(1,'h34,'h2edf);access(1,'h04,'h90);access(1,'h08,2);")
bench=bench.replace('repeat(150000)', r"""force dut.front_ohci.endpoint_stateReg=5'd21;
force dut.front_ohci.endpoint_ED_address=32'h220;
force dut.front_ohci.endpoint_TD_retire=1'b1;
force dut.front_ohci.endpoint_TD_nextTD=30'b0;
force dut.front_ohci.endpoint_TD_dataPhaseNext=1'b1;
force dut.front_ohci.endpoint_ED_H=1'b0;
repeat(2000)""")
bench=bench.replace('if(value[15:0]<2)$fatal(1,\"frame counter stopped %h\",value);','')
bench=bench.replace('if(dma_writes==0)', '''if(ram['h228/4]!==32'd2)$fatal(1,"ED head was not updated at word 2");
if(ram['h220/4]!=='h08080000)$fatal(1,"ED header corrupted: %h",ram['h220/4]);
if(dma_writes==0)''')
bench=bench.replace('PASS OHCI revision, root port, soft reset, frame timer and HCCA DMA','PASS OHCI ED burst writeback: header preserved, head updated at word 2')
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp);(root/'adapter.v').write_text(rtl);(root/'tb.v').write_text(bench)
 subprocess.run([reference['iverilog'],'-g2012','-s','tb','-o',str(root/'sim'),str(root/'tb.v'),str(root/'adapter.v'),str(reference['source'])],check=True,capture_output=True)
 subprocess.run([reference['vvp'],str(root/'sim')],check=True,timeout=60)
