`timescale 1ns/1ps
// Independent NRZI source verifies RX sampling at every phase; a second
// receiver checks TX sync, stuffing, byte handshake and EOP. The DUT hears its
// own drive, as on the board; the EOP must be two bit times of SE0 then one
// driven J, and a reply at the earliest legal turnaround must be received.
module usb_fs_phy_tb;
parameter FREQ=60000000;
parameter real JITTER_NS=0;
localparam RATIO=FREQ/12000000;
localparam real CLOCK_NS=1000000000.0/FREQ;
localparam real BIT_NS=1000000000.0/12000000;
reg clk=0;always #(CLOCK_NS/2) clk=~clk;
reg rst=1,valid=0,rxdp=1,rxdn=0,rcv=1;
reg [7:0] txdata;
wire dp,dn,oen,ready;
wire [7:0] received,loop_data;
wire rxvalid,rxactive,rxerror,loop_valid,loop_error;
integer txindex,rxindex,loop_index,phase,ones,i,j;
integer symbol_index=0;
integer se0_clocks=0,j_clocks=0,eops=0;
reg line,oen_q=1;
reg [7:0] payload[0:4];
usb_fs_phy #(.USB_CLK_FREQ(FREQ)) dut(.clk_i(clk),.rst_i(rst),
 .utmi_data_out_i(txdata),.utmi_txvalid_i(valid),.utmi_op_mode_i(2'b00),
 .utmi_xcvrselect_i(2'b01),.utmi_termselect_i(1'b1),.utmi_dppulldown_i(1'b1),.utmi_dmpulldown_i(1'b1),
 .usb_rx_dp_i(oen?rxdp:dp),.usb_rx_dn_i(oen?rxdn:dn),.usb_rx_rcv_i(oen?rcv:dp),.usb_reset_assert_i(1'b0),
 .usb_tx_dp_o(dp),.usb_tx_dn_o(dn),.usb_tx_oen_o(oen),.utmi_txready_o(ready),
 .utmi_data_in_o(received),.utmi_rxvalid_o(rxvalid),.utmi_rxactive_o(rxactive),.utmi_rxerror_o(rxerror));
usb_fs_phy #(.USB_CLK_FREQ(FREQ)) loopback(.clk_i(clk),.rst_i(rst),
 .utmi_data_out_i(8'b0),.utmi_txvalid_i(1'b0),.utmi_op_mode_i(2'b00),
 .utmi_xcvrselect_i(2'b01),.utmi_termselect_i(1'b1),.utmi_dppulldown_i(1'b1),.utmi_dmpulldown_i(1'b1),
 .usb_rx_dp_i(oen?1'b1:dp),.usb_rx_dn_i(oen?1'b0:dn),.usb_rx_rcv_i(oen?1'b1:dp),.usb_reset_assert_i(1'b0),
 .utmi_data_in_o(loop_data),.utmi_rxvalid_o(loop_valid),.utmi_rxerror_o(loop_error));
always @(posedge clk) if(!rst) begin
 if(!oen && !dp && !dn) begin se0_clocks=se0_clocks+1; j_clocks=0; end
 else if(!oen && se0_clocks!=0 && dp && !dn) j_clocks=j_clocks+1;
 if(oen && !oen_q) begin
   if(se0_clocks!=2*RATIO || j_clocks!=RATIO)$fatal(1,"EOP SE0 %0d / driven J %0d clocks",se0_clocks,j_clocks);
   eops=eops+1; se0_clocks=0; j_clocks=0;
 end
 oen_q=oen;
 if(ready && valid) begin
   txindex=txindex+1;
   if(txindex==5)begin valid<=0;end else txdata<=payload[txindex];
 end
 if(rxerror || loop_error)$fatal(1,"PHY decode error");
 if(rxvalid)begin
   if(rxindex>=5 || received!==payload[rxindex])$fatal(1,"RX byte %d got %x",rxindex,received);
   rxindex=rxindex+1;
 end
 if(loop_valid)begin
   if(loop_index>=5 || loop_data!==payload[loop_index])$fatal(1,"TX loop byte %d got %x",loop_index,loop_data);
   loop_index=loop_index+1;
 end
end
task symbol(input bit value);
begin
 if(!value)line=~line;
 rxdp=line;rxdn=~line;rcv=line;
 // Alternate edge displacement without changing the average bit rate.
 #(BIT_NS + ((symbol_index%2)?JITTER_NS:-JITTER_NS));
 symbol_index=symbol_index+1;
end endtask
task send_byte(input [7:0] value,input bit stuff);
integer b;
begin
 for(b=0;b<8;b=b+1)begin
   symbol(value[b]);
   if(value[b])ones=ones+1;else ones=0;
   if(stuff && ones==6)begin symbol(0);ones=0;end
 end
end endtask
initial begin
 payload[0]=8'hc3;payload[1]=8'hff;payload[2]=8'h00;payload[3]=8'h7e;payload[4]=8'h55;
 txindex=0;rxindex=0;loop_index=0;txdata=payload[0];
 repeat(10)@(negedge clk);rst=0;
 // Sweep the entire bit cell in 1ns steps, including clock-edge boundaries.
 for(phase=0;phase<84;phase=phase+1)begin
   repeat(30)@(negedge clk);#(1+phase);
   rxindex=0;line=1;ones=0;send_byte(8'h80,0);ones=0;
   for(i=0;i<5;i=i+1)send_byte(payload[i],1);
   rxdp=0;rxdn=0;rcv=0;#(2*BIT_NS);rxdp=1;rxdn=0;rcv=1;#(BIT_NS);
   repeat(20)@(negedge clk);
   if(rxindex!=5 || rxactive)$fatal(1,"RX phase %d incomplete: %d",phase,rxindex);
 end
 @(negedge clk);valid=1;
 @(posedge oen);
 // The release follows one driven J bit; a reply may start one bit later.
 #(BIT_NS);rxindex=0;line=1;ones=0;send_byte(8'h80,0);ones=0;send_byte(payload[0],1);
 rxdp=0;rxdn=0;rcv=0;#(2*BIT_NS);rxdp=1;rxdn=0;rcv=1;#(BIT_NS);
 repeat(20)@(negedge clk);
 if(txindex!=5 || loop_index!=5 || !oen || eops!=1)$fatal(1,"TX incomplete %d %d eop %d",txindex,loop_index,eops);
 if(rxindex!=1 || rxactive)$fatal(1,"Reply after host EOP not received: %d",rxindex);
 $display("PASS USB FS PHY %d MHz, %d samples/bit, jitter %f ns, RX all phases, TX stuffing/EOP with driven J, turnaround reply",FREQ/1000000,RATIO,JITTER_NS);$finish;
end
initial begin #1000000;$fatal(1,"watchdog");end
endmodule
