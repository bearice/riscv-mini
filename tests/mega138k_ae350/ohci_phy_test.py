"""Exercise the locked OHCI PHY with short and bit-stuffed FS responses."""
from ohci_core_test import run_bench


def packet(payload):
    crc = 0xffff
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    crc ^= 0xffff
    data = bytes([0x4b]) + payload + bytes([crc & 255, crc >> 8])
    bits = [0, 0, 0, 0, 0, 0, 0, 1]
    ones = 0
    for byte in data:
        for bit in range(8):
            value = (byte >> bit) & 1
            bits.append(value)
            ones = ones + 1 if value else 0
            if ones == 6:
                bits.append(0)
                ones = 0
    return data, bits


commands = []
for phase, ppm, skew in ((1, 0, 0), (7, -2000, 0), (14, 2000, 0),
                         (20, 0, 15), (7, -2000, 15), (14, 2000, 15)):
    for length in (0, 1, 2, 7, 8, 9, 16, 32, 64):
        for pattern in (0xff, 0x00, 0x55):
            data, bits = packet(bytes([pattern]) * length)
            commands += ['repeat(40) @(negedge clk); count=0; errors=0;',
                         *[f'expected[{i}]=8\'h{b:02x};' for i, b in enumerate(data)],
                         f'#{phase};']
            state = 1
            for index, bit in enumerate(bits):
                if not bit:
                    state ^= 1
                # Alternating durations place edges at +/-8 ns around the
                # nominal phase without accumulating frequency drift.
                period = 83.333 * (1 + ppm / 1000000) + (16 if index % 2 else -16)
                commands += [f'dp={state};#{skew};dm={1-state};#{period-skew:.3f};']
            commands += ['dp=0;dm=0;#166.666;dp=1;dm=0;#83.333;',
                         'repeat(40) @(negedge clk);',
                         f'if(count!={len(data)} || errors) $fatal(1,"RX length={length} phase={phase} ppm={ppm} skew={skew} pattern={pattern} count=%d errors=%d",count,errors);']

bench = r'''
`timescale 1ns/1ps
module tb;
reg clk=0,reset=1,dp=1,dm=0;always #10.416 clk=~clk;
wire valid,active,error;wire[7:0]data;integer count=0,errors=0;
reg[7:0]expected[0:66];
UsbOhciWishbone_Dw32_Pc1_Pf48000000_UsbLsFsPhy dut(
 .phy_clk(clk),.phy_reset(reset),.io_ctrl_lowSpeed(1'b0),
 .io_ctrl_tx_valid(1'b0),.io_ctrl_tx_payload_last(1'b0),.io_ctrl_tx_payload_fragment(8'b0),
 .io_ctrl_usbReset(1'b0),.io_ctrl_usbResume(1'b0),
 .io_ctrl_ports_0_disable_valid(1'b0),.io_ctrl_ports_0_removable(1'b1),
 .io_ctrl_ports_0_power(1'b1),.io_ctrl_ports_0_reset_valid(1'b0),
 .io_ctrl_ports_0_suspend_valid(1'b0),.io_ctrl_ports_0_resume_valid(1'b0),
 .io_usb_0_overcurrent(1'b0),.io_usb_0_rx_dp(dp),.io_usb_0_rx_dm(dm),
 .io_ctrl_rx_flow_valid(valid),.io_ctrl_rx_flow_payload_data(data),
 .io_ctrl_rx_flow_payload_stuffingError(error),.io_ctrl_rx_active(active));
always @(posedge clk)if(!reset&&valid)begin
 if(error || data!==expected[count])begin
  $display("RX mismatch byte=%d got=%h expected=%h stuffing=%b",count,data,expected[count],error);
  errors=errors+1;
 end
 count=count+1;
end
initial begin
 repeat(10)@(negedge clk);reset=0;repeat(10)@(negedge clk);
 // Isolate receive decoding from the 50 ms port-reset state machine.
 force dut.ports_0_fsm_stateReg=4'b0111;
 force dut.ports_0_portLowSpeed=1'b0;
 COMMANDS
 $display("PASS OHCI FS RX short packets, stuffing and sampling phases");$finish;
end
initial begin #30000000;$fatal(1,"PHY simulation timeout");end
endmodule
'''.replace('COMMANDS', '\n'.join(commands))

if __name__ == '__main__':
    run_bench(bench)
