"""Real OHCI multi-packet IN retirement through the shared DDR adapter."""
import re
import runpy
import subprocess
import tempfile
from pathlib import Path

# Reuse the real-core/DDR wiring and its independent masked DDR RAM model.
context=runpy.run_path(str(Path(__file__).with_name('ohci_dma_integration_test.py')))
bench=context['bench']
bench=bench.replace("access(1,'h34,'h2edf);", "access(1,'h34,'ha7782edf);access(1,'h40,'h2a2f);")
bench=bench.replace("ram['h230/4]='he2040001;", "ram['h230/4]='he3140001;")
bench=bench.replace("ram['h23c/4]='h307;", "ram['h23c/4]='h308;")
bench=re.sub(r'force dut.front_ohci.endpoint_stateReg=.*?repeat\(2000\)',
             "force dut.back_phy.ports_0_fsm_stateReg=4'b0111;\n"
             "force dut.back_phy.ports_0_portLowSpeed=1'b0;\nrepeat(200000)",bench,flags=re.S)
bench=bench.replace('integer dma_writes=0;',r'''
reg rxv=0,rxa=0;reg[7:0]rxdata=0;
task byte_rx(input[7:0]v);begin
 @(negedge clk);rxv=1;rxdata=v;
 @(negedge clk);rxv=0;repeat(31)@(negedge clk);
end endtask
task packet_rx(input[7:0]pid,input integer offset,input integer length);
 integer j,k;reg[15:0]crc;reg[7:0]v;begin
 wait(dut.front_ohci.dataRx_stateReg==3'd1);
 @(negedge clk);rxa=1;repeat(8)@(negedge clk);
 byte_rx(pid);crc=16'hffff;
 for(j=0;j<length;j=j+1)begin
  v=offset+j;byte_rx(v);crc=crc^v;
  for(k=0;k<8;k=k+1)crc=(crc>>1)^(crc[0]?16'ha001:16'h0);
 end
 crc=~crc;byte_rx(crc[7:0]);byte_rx(crc[15:8]);
 @(negedge clk);rxa=0;
 wait(dut.front_ohci.dataRx_stateReg!=3'd1);
end endtask
initial begin
 force dut.front_ohci.io_phy_rx_active=rxa;
 force dut.front_ohci.io_phy_rx_flow_valid=rxv;
 force dut.front_ohci.io_phy_rx_flow_payload_data=rxdata;
 force dut.front_ohci.io_phy_rx_flow_payload_stuffingError=1'b0;
 wait(!reset);
 packet_rx(8'h4b,16,8);packet_rx(8'hc3,24,1);
end
integer dma_writes=0;''')
bench=bench.replace("if(ram['h228/4]!==32'd2)",r'''
if(ram['h230/4][31:28]!==0)$fatal(1,"IN TD condition code %h endpoint=%d rx=%d control=%h",ram['h230/4],dut.front_ohci.endpoint_stateReg,dut.front_ohci.dataRx_stateReg,dut.front_ohci.reg_hcControl_HCFS);
if(ram['h234/4]!==0)$fatal(1,"IN TD current buffer not retired %h",ram['h234/4]);
for(i=0;i<TRANSFER_BYTES;i=i+1)
 if(((ram[('h300+i)/4]>>(8*(i%4)))&255)!==(16+i))$fatal(1,"IN DMA byte mismatch at %d",i);
for(i=TRANSFER_BYTES;i<32;i=i+1)
 if(((ram[('h300+i)/4]>>(8*(i%4)))&255)!==0)$fatal(1,"IN DMA overwrote guard byte %d",i);
if(ram['h228/4]!==32'd2)''')
bench=bench.replace('PASS OHCI ED burst writeback: header preserved, head updated at word 2',
                    'PASS OHCI multi-packet IN: TRANSFER_BYTES bytes, toggle, CRC, DDR masks and TD retirement')
reference=context['reference']
for length in (9,13,25):
    calls=''.join(f"packet_rx(8'h{0x4b if n%2==0 else 0xc3:02x},{16+offset},{min(8,length-offset)});"
                  for n,offset in enumerate(range(0,length,8)))
    case=bench.replace("packet_rx(8'h4b,16,8);packet_rx(8'hc3,24,1);",calls)
    case=case.replace("ram['h23c/4]='h308;",f"ram['h23c/4]='h{0x300+length-1:x};")
    case=case.replace('TRANSFER_BYTES',str(length))
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);(root/'adapter.v').write_text(context['rtl']);(root/'tb.v').write_text(case)
        result=subprocess.run([reference['iverilog'],'-g2012','-s','tb','-o',str(root/'sim'),str(root/'tb.v'),str(root/'adapter.v'),str(reference['source'])],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)
        subprocess.run([reference['vvp'],str(root/'sim')],check=True,timeout=60)
