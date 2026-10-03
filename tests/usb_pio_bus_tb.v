`timescale 1ns/1ps
module usb_pio_bus_tb;
reg clk=0;always #5 clk=~clk;reg rst=1;
reg [29:0] adr=0;reg [31:0] dw=0;wire [31:0] dr;
reg cyc=0,stb=0,we=0;wire ack,err;
reg core_reset=0;
usb_pio_bus dut(.sys_clk(clk),.sys_rst(rst),.core_reset(core_reset),.wb_adr(adr),.wb_dat_w(dw),.wb_dat_r(dr),
 .wb_sel(4'hf),.wb_cyc(cyc),.wb_stb(stb),.wb_we(we),.wb_ack(ack),.wb_err(err));
task access(input bit write,input [31:0] address,input [31:0] value,input [31:0] expected);
integer n;
begin
 @(negedge clk);adr=address>>2;dw=value;we=write;cyc=1;stb=1;n=0;
 while(!ack && n<30)begin @(negedge clk);n=n+1;end
 if(!ack || err)$fatal(1,"bus timeout/error %x",address);
 if(!write && dr!==expected)$fatal(1,"read got %x expected %x",dr,expected);
 cyc=0;stb=0;@(negedge clk);
end endtask
integer i;
initial begin
 repeat(10)@(negedge clk);rst=0;
 for(i=0;i<16;i=i+1)begin
   access(1,32'hb1000000,32'he8,0);access(0,32'hb1000000,0,32'he8);
   access(1,32'hb1000010,i&7,0);access(0,32'hb1000010,0,i&7);
 end
 // Reset after accepting a request: the next request must not inherit it.
 @(negedge clk);adr=32'hb1000000>>2;cyc=1;stb=1;we=0;
 @(negedge clk);core_reset=1;cyc=0;stb=0;
 repeat(5)@(negedge clk);core_reset=0;
 repeat(5)@(negedge clk);
 access(1,32'hb1000000,32'he8,0);access(0,32'hb1000000,0,32'he8);
 $display("PASS USB PIO registered Wishbone/AXI Host registers: repeated read/write, reset recovery");$finish;
end
initial begin #100000;$fatal(1,"watchdog");end
endmodule
