"""Standard OHCI MMIO and HCCA DMA against the locked SpinalHDL core."""
import shutil
import subprocess
import tempfile
from pathlib import Path
from litex import get_data_mod

iverilog = shutil.which('iverilog')
if not iverilog:
    iverilog = str(next(Path('/nix/store').glob('*iverilog*/bin/iverilog')))
vvp = str(Path(iverilog).with_name('vvp'))
source = Path(get_data_mod('misc', 'usb_ohci').data_location) / 'UsbOhciWishbone_Dw32_Pc1_Pf48000000.v'
bench = r'''
module tb;
reg clk=0,reset=1; always #10 clk=~clk;
reg cyc=0,stb=0,we=0;reg[9:0]adr=0;reg[31:0]data=0;
wire ack;wire[31:0]result;wire dc,ds,dw;wire[29:0]da;
integer dma_writes=0;reg[31:0]value;
always @(posedge clk)if(!reset&&dc&&ds&&dw)dma_writes<=dma_writes+1;
UsbOhciWishbone_Dw32_Pc1_Pf48000000 dut(
.phy_clk(clk),.phy_reset(reset),.ctrl_clk(clk),.ctrl_reset(reset),
.io_ctrl_CYC(cyc),.io_ctrl_STB(stb),.io_ctrl_WE(we),.io_ctrl_ADR(adr),
.io_ctrl_DAT_MOSI(data),.io_ctrl_SEL(4'hf),.io_ctrl_ACK(ack),.io_ctrl_DAT_MISO(result),
.io_dma_CYC(dc),.io_dma_STB(ds),.io_dma_WE(dw),.io_dma_ADR(da),
.io_dma_ACK(dc&&ds),.io_dma_ERR(1'b0),.io_dma_DAT_MISO(32'b0),
.io_usb_0_dp_read(1'b1),.io_usb_0_dm_read(1'b0));
task access(input wr,input[9:0]off,input[31:0]d);integer n;begin
@(negedge clk);cyc=1;stb=1;we=wr;adr=off>>2;data=d;
n=0;while(!ack&&n<100)begin @(negedge clk);n=n+1;end
if(!ack)$fatal(1,"OHCI control timeout");value=result;
@(negedge clk);cyc=0;stb=0;repeat(2)@(negedge clk);
end endtask
initial begin
repeat(10)@(negedge clk);reset=0;repeat(20)@(negedge clk);
access(0,0,0);if(value!==32'h10)$fatal(1,"OHCI revision %h",value);
access(0,'h48,0);if(value[7:0]!=1)$fatal(1,"root port count %h",value);
access(1,'h08,1);repeat(100)@(negedge clk);
access(0,'h08,0);if(value[0])$fatal(1,"soft reset stuck");
access(1,'h18,'h100);access(0,'h18,0);if(value!='h100)$fatal(1,"HCCA readback");
access(1,'h34,'h2edf);access(1,'h04,'h80);
repeat(150000)@(negedge clk);
access(0,'h3c,0);if(value[15:0]<2)$fatal(1,"frame counter stopped %h",value);
if(dma_writes==0)$fatal(1,"no HCCA DMA writes");
$display("PASS OHCI revision, root port, soft reset, frame timer and HCCA DMA");$finish;
end
initial begin #10000000;$fatal(1,"simulation timeout");end
endmodule
'''
def run_bench(bench):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root/'tb.v').write_text(bench)
        subprocess.run([iverilog,'-g2012','-s','tb','-o',str(root/'sim'),str(root/'tb.v'),str(source)],check=True,capture_output=True)
        subprocess.run([vvp,str(root/'sim')],check=True,timeout=60)

if __name__ == "__main__":
    run_bench(bench)
