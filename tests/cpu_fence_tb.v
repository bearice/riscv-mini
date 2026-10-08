`timescale 1ns/1ps
module cpu_fence_tb;
parameter ACK_DELAY=9;
reg clk=0;
always #5 clk=~clk;
reg reset=1;
wire ic,istb,iw,dc,dstb,dw;
wire [29:0] ia,da;
wire [31:0] idw,ddw;
wire [3:0] isel,dsel;
wire [2:0] icti,dcti;
wire [1:0] ibte,dbte;
reg iack=0,dack=0;
reg [31:0] idr=0,ddr=0;
reg [31:0] rom [0:4095];
reg [31:0] mem [0:255];
integer i,cycles=0,wait_d=0,writes=0,phase=0;
VexRiscv cpu(.clk(clk),.reset(reset),.externalResetVector(32'b0),
 .timerInterrupt(1'b0),.softwareInterrupt(1'b0),.externalInterruptArray(32'b0),
 .iBusWishbone_CYC(ic),.iBusWishbone_STB(istb),.iBusWishbone_WE(iw),
 .iBusWishbone_ADR(ia),.iBusWishbone_DAT_MOSI(idw),.iBusWishbone_SEL(isel),
 .iBusWishbone_CTI(icti),.iBusWishbone_BTE(ibte),.iBusWishbone_ACK(iack),
 .iBusWishbone_DAT_MISO(idr),.iBusWishbone_ERR(1'b0),
 .dBusWishbone_CYC(dc),.dBusWishbone_STB(dstb),.dBusWishbone_WE(dw),
 .dBusWishbone_ADR(da),.dBusWishbone_DAT_MOSI(ddw),.dBusWishbone_SEL(dsel),
 .dBusWishbone_CTI(dcti),.dBusWishbone_BTE(dbte),.dBusWishbone_ACK(dack),
 .dBusWishbone_DAT_MISO(ddr),.dBusWishbone_ERR(1'b0));
initial begin
 for(i=0;i<4096;i=i+1)rom[i]=32'h00000013;
 for(i=0;i<256;i=i+1)mem[i]=0;
 $readmemh("program.hex",rom);
 repeat(12)@(negedge clk);
 reset=0;
end
always @(negedge clk)begin
 cycles=cycles+1;
 if(cycles>200000)$fatal(1,"CPU fence timeout phase=%0d writes=%0d",phase,writes);
 iack=0;dack=0;
 if(!reset)begin
  if(ic && istb)begin
   idr=ia>=30'h00200000 ? mem[ia & 255] : rom[ia & 4095];iack=1;
  end
  if(dc && dstb)begin
   wait_d=wait_d+1;
   if(wait_d==ACK_DELAY)begin
    if(da>=30'h3c000000)begin
     if(ddw==255)$fatal(1,"Guest check failed");
     if(ddw!=phase+1)$fatal(1,"MMIO phase ordering");
     phase=ddw;
     if(phase==1 && (mem[0]!=32'h11223344 || mem[3]!=32'h11223344))$fatal(1,"FENCE visibility");
     if(phase==2 && mem[4]!=32'h55667788)$fatal(1,"FENCE.I visibility");
     if(phase==3 && mem[0]!=32'h1122334b)$fatal(1,"AMO result not visible before MMIO");
     if(phase==4)begin
      if(mem[8]!=100 || mem[0]!=32'h1122334d)$fatal(1,"Final fence/LRSC missing");
      $display("CPU RTL fence PASS: ACK-committed writes, modified code/FENCE.I, AMO, LR/SC and MMIO; cycles=%0d ack_delay=%0d",cycles,ACK_DELAY);
      $finish;
     end
    end else if(dw)begin
     writes=writes+1;
     // Match SharedL2: an acknowledged store is visible to every L2 client.
     mem[da & 255]=ddw;
    end else begin
     ddr=mem[da & 255];
    end
    dack=1;wait_d=0;
   end
  end else wait_d=0;
 end
end
endmodule
