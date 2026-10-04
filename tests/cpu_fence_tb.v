`timescale 1ns/1ps
module cpu_fence_tb;
reg clk=0;
always #5 clk=~clk;
reg reset=1;
wire req,atomic;
reg done=0;
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
reg [31:0] posted [0:255];
reg dirty [0:255];
integer i,cycles=0,wait_d=0,fence_wait=0,fences=0,writes=0,phase=0;
reg previous_req=0;
VexRiscv cpu(.clk(clk),.reset(reset),.externalResetVector(32'b0),
 .timerInterrupt(1'b0),.softwareInterrupt(1'b0),.externalInterruptArray(32'b0),
 .externalFenceRequest(req),.externalFenceDone(done),.externalAtomic(atomic),
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
 for(i=0;i<256;i=i+1)begin mem[i]=0;posted[i]=0;dirty[i]=0;end
 $readmemh("program.hex",rom);
 repeat(12)@(negedge clk);
 reset=0;
end
always @(negedge clk)begin
 cycles=cycles+1;
 if(cycles>200000)$fatal(1,"CPU fence timeout phase=%0d fences=%0d writes=%0d",phase,fences,writes);
 iack=0;dack=0;
 if(!reset)begin
  if(cpu.IBusCachedPlugin_cache_io_flush && !done)$fatal(1,"I-cache invalidated before external fence completion");
  if(ic && istb)begin
   idr=ia>=30'h10000000 ? mem[ia & 255] : rom[ia & 4095];iack=1;
  end
  if(req && !previous_req)begin
   if(dc)$fatal(1,"Fence raced queued CPU command");
   if(fences==0 && writes!=4)$fatal(1,"Fence did not wait for older writes: %0d",writes);
   fences=fences+1;fence_wait=20;
  end
  previous_req=req;
  if(fence_wait>0)begin
   if(!req)$fatal(1,"Fence request vanished before completion");
   fence_wait=fence_wait-1;
   if(fence_wait==0)begin
    for(i=0;i<256;i=i+1)if(dirty[i])begin mem[i]=posted[i];dirty[i]=0;end
    done=1;
   end
  end
  if(!req)done=0;
  if(dc && dstb)begin
   wait_d=wait_d+1;
   if(wait_d==9)begin
    if(da>=30'h20000000)begin
     if(fence_wait!=0)$fatal(1,"Younger MMIO escaped stalled fence");
     if(ddw==255)$fatal(1,"Guest check failed");
     if(ddw!=phase+1)$fatal(1,"MMIO phase ordering");
     phase=ddw;
     if(phase==1 && (mem[0]!=32'h11223344 || mem[3]!=32'h11223344))$fatal(1,"FENCE visibility");
     if(phase==2 && mem[4]!=32'h55667788)$fatal(1,"FENCE.I visibility");
     if(phase==3 && mem[0]!=32'h1122334b)$fatal(1,"Atomic store was posted");
     if(phase==4)begin
      if(fences<8 || mem[8]!=100 || mem[0]!=32'h1122334d)$fatal(1,"Final fence/LRSC missing");
      $display("CPU RTL fence PASS: queued writes, delayed completion, modified code/FENCE.I, AMO, LR/SC and younger MMIO; cycles=%0d fences=%0d",cycles,fences);
      $finish;
     end
    end else if(dw)begin
     writes=writes+1;
     if(atomic)mem[da & 255]=ddw;
     else begin posted[da & 255]=ddw;dirty[da & 255]=1;end
    end else begin
     // External reads conservatively drain prior stores, as the SoC does.
     for(i=0;i<256;i=i+1)if(dirty[i])begin mem[i]=posted[i];dirty[i]=0;end
     ddr=mem[da & 255];
    end
    dack=1;wait_d=0;
   end
  end else wait_d=0;
 end
end
endmodule
