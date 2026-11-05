"""Compare real OHCI PHY TX against independent USB NRZI/stuffing/EOP."""
import subprocess
import tempfile
from pathlib import Path
from ohci_core_test import iverilog, vvp, source
from ohci_phy_test import packet

cases=[]
for length in (0,1,2,7,8,9,16,31,32,64):
    for pattern in (0,0x55,0xff):
        cases.append(packet(bytes([pattern])*length))
commands=[]
for index,(data,bits) in enumerate(cases):
    commands += [f'$display("CASE {index}"); capture=1;',
                 *[f'send(8\'h{b:02x},1\'b{int(i==len(data)-1)});' for i,b in enumerate(data)],
                 'txvalid=0;repeat(60)@(negedge clk);capture=0;']
bench=r'''
`timescale 1ns/1ps
module tb;
reg clk=0,reset=1,txvalid=0,last=0,capture=0;always #10.416 clk=~clk;
reg[7:0]txdata=0;wire ready,raw_oe,raw_dp,raw_se0;
reg oe1=0,oe=0,dp1=1,dp=1,se01=0,se0=0;
// Match the locked top-level's two registered pad-output stages.
always @(posedge clk)begin
 oe1<=raw_oe;oe<=oe1;dp1<=raw_dp;dp<=dp1;se01<=raw_se0;se0<=se01;
end
UsbOhciWishbone_Dw32_Pc1_Pf48000000_UsbLsFsPhy dut(
 .phy_clk(clk),.phy_reset(reset),.io_ctrl_lowSpeed(1'b0),
 .io_ctrl_tx_valid(txvalid),.io_ctrl_tx_payload_last(last),.io_ctrl_tx_payload_fragment(txdata),
 .io_ctrl_tx_ready(ready),.io_ctrl_usbReset(1'b0),.io_ctrl_usbResume(1'b0),
 .io_ctrl_ports_0_disable_valid(1'b0),.io_ctrl_ports_0_removable(1'b1),
 .io_ctrl_ports_0_power(1'b1),.io_ctrl_ports_0_reset_valid(1'b0),
 .io_ctrl_ports_0_suspend_valid(1'b0),.io_ctrl_ports_0_resume_valid(1'b0),
 .io_usb_0_overcurrent(1'b0),.io_usb_0_rx_dp(1'b1),.io_usb_0_rx_dm(1'b0),
 .io_usb_0_tx_enable(raw_oe),.io_usb_0_tx_data(raw_dp),.io_usb_0_tx_se0(raw_se0));
always @(posedge clk)begin #1;if(capture)$display("T %d",!oe?3:se0?0:dp?1:2);end
task send(input[7:0]value,input finalbyte);integer n;begin
 txvalid=1;txdata=value;last=finalbyte;n=0;
 @(posedge clk);
 while(!ready&&n<1000)begin @(posedge clk);n=n+1;end
 if(!ready)$fatal(1,"TX stream timeout");
 @(negedge clk);
end endtask
initial begin
 repeat(10)@(negedge clk);reset=0;repeat(10)@(negedge clk);
 force dut.ports_0_fsm_stateReg=4'b0111;
 force dut.ports_0_portLowSpeed=1'b0;
 COMMANDS
 $finish;
end
initial begin #30000000;$fatal(1,"PHY TX timeout");end
endmodule
'''.replace('COMMANDS','\n'.join(commands))

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);(root/'tb.v').write_text(bench)
    subprocess.run([iverilog,'-g2012','-s','tb','-o',str(root/'sim'),str(root/'tb.v'),str(source)],check=True,capture_output=True)
    result=subprocess.run([vvp,str(root/'sim')],check=True,timeout=60,capture_output=True,text=True)
    traces={};current=None
    for line in result.stdout.splitlines():
        if line.startswith('CASE '):
            current=int(line.split()[1]);traces[current]=[]
        elif line.startswith('T '):traces[current].append(int(line.split()[1]))
    for index,(_,bits) in enumerate(cases):
        states=traces[index];start=states.index(2);expected=[];state=1
        for bit in bits:
            if not bit:state^=1
            expected += [1 if state else 2]*4
        expected += [0]*8+[1]*4+[3]*4
        actual=states[start:start+len(expected)]
        if actual!=expected:
            mismatch=next(i for i,(a,b) in enumerate(zip(actual,expected)) if a!=b)
            raise AssertionError(f'OHCI PHY TX case={index} cycle={mismatch} actual={actual[max(0,mismatch-8):mismatch+16]} expected={expected[max(0,mismatch-8):mismatch+16]}')
print('PASS OHCI PHY TX NRZI, stuffing, short/31-byte packets and driven EOP J')
