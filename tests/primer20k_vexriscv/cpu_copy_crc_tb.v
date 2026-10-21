`timescale 1ns/1ps
module cpu_copy_crc_tb;
parameter ACK_DELAY=9;
parameter UART_RXTX=32'hf0000000;
parameter UART_RXEMPTY=32'hf0000008;
reg clk=0,reset=1;
always #5 clk=~clk;
wire ic,istb,iw,dc,dstb,dw;
wire [29:0] ia,da;
wire [31:0] idw,ddw;
wire [3:0] isel,dsel;
wire [2:0] icti,dcti;
wire [1:0] ibte,dbte;
reg iack=0,dack=0;
reg [31:0] idr=0,ddr=0;
reg [31:0] image[0:4095],stack[0:4095],scratch[0:16383];
integer i,lane,cycles=0,wait_d=0,writes=0,want;
reg [2047:0] line=0;
reg [207:0] prefix;
integer line_len=0;
VexRiscv cpu(.clk(clk),.reset(reset),.externalResetVector(32'h00800000),
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
 for(i=0;i<4096;i=i+1)begin image[i]=0;stack[i]=0;end
 for(i=0;i<16384;i=i+1)scratch[i]=0;
 $readmemh("program.hex",image);
 repeat(12)@(negedge clk);
 reset=0;
end
always @(negedge clk)begin
 cycles=cycles+1;
 if(cycles>2000000)$fatal(1,"Copy/CRC timeout da=%h dw=%b writes=%0d",{da,2'b0},dw,writes);
 iack=0;dack=0;
 if(!reset)begin
  if(ic && istb)begin
   if(ia<30'h00200000 || ia>=30'h00201000)$fatal(1,"Unexpected PC %h",{ia,2'b0});
   idr=image[ia & 4095];iack=1;
  end
  if(dc && dstb)begin
   wait_d=wait_d+1;
   if(wait_d==ACK_DELAY)begin
    if(da>=30'h3c000000)begin
     ddr=({da,2'b0}==UART_RXEMPTY)?1:0;
     if(dw && {da,2'b0}==UART_RXTX)begin
      $write("%c",ddw[7:0]);
      if(ddw[7:0]==10)begin
       prefix=line >> ((line_len-26)*8);
       if(line_len>=26 && prefix=="COPYCRC byte size=00000400")begin
        if(line[31:0]!="PASS")$fatal(1,"Guest copy/CRC check failed");
        for(i=0;i<1024;i=i+1)begin
         want=(((i & 127)*37)^((i & 127)>>3)^8'ha5)&255;
         if(scratch[i/4][8*(i%4)+:8]!=want)$fatal(1,"ACK-visible byte mismatch at %0d",i);
        end
        $display("CPU copy/CRC PASS: 1 KiB byte copy, golden CRC and ACK-visible bytes; cycles=%0d ack_delay=%0d",cycles,ACK_DELAY);
        $finish;
       end
       line=0;line_len=0;
      end else if(ddw[7:0]!=13)begin line={line[2039:0],ddw[7:0]};line_len=line_len+1;end
     end
    end else if(da>=30'h00200000 && da<30'h00201000)begin
     if(dw)begin
      for(lane=0;lane<4;lane=lane+1)if(dsel[lane])image[da & 4095][lane*8+:8]=ddw[lane*8+:8];
     end else ddr=image[da & 4095];
    end else if(da>=30'h002ff000 && da<30'h00300000)begin
     if(dw)begin
      for(lane=0;lane<4;lane=lane+1)if(dsel[lane])stack[da & 4095][lane*8+:8]=ddw[lane*8+:8];
     end else ddr=stack[da & 4095];
    end else if(da>=30'h00800000 && da<30'h00804000)begin
     if(dw)begin
      writes=writes+1;
      for(lane=0;lane<4;lane=lane+1)if(dsel[lane])scratch[da & 16383][lane*8+:8]=ddw[lane*8+:8];
     end else ddr=scratch[da & 16383];
    end else $fatal(1,"Unexpected data address %h",{da,2'b0});
    dack=1;wait_d=0;
   end
  end else wait_d=0;
 end
end
endmodule
