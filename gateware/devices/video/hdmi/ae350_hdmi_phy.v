/*
 *  SVO - Simple Video Out FPGA Core
 *
 *  Copyright (C) 2014  Clifford Wolf <clifford@clifford.at>
 *
 *  Permission to use, copy, modify, and/or distribute this software for any
 *  purpose with or without fee is hereby granted, provided that the above
 *  copyright notice and this permission notice appear in all copies.
 *
 *  THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
 *  WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
 *  MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
 *  ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
 *  WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
 *  ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
 *  OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
 *
 */

`timescale 1ns/1ps
// Local AE350 physical output; input pixels are packed {B,G,R}.
module ae350_hdmi_phy (
    input resetn, locked, clk_pixel, clk_5x_pixel, clk_reset,
    input [23:0] rgb,
    input de, hsync, vsync,
    output tmds_clk_n, tmds_clk_p,
    output [2:0] tmds_d_n, tmds_d_p
);
    reg [3:0] pixel_resetn_q=0;
    reg [2:0] serializer_resetn_q=0;
    (* syn_keep=1, syn_preserve=1 *) reg [2:0] lane_resetn_q=0;
    always @(posedge clk_pixel or negedge resetn or negedge locked)
        if(!resetn || !locked)pixel_resetn_q<=0;
        else pixel_resetn_q<={pixel_resetn_q[2:0],1'b1};
    // Release uses the phase-shifted 25MHz clock, never fabric FFs on FCLK.
    always @(posedge clk_reset or negedge resetn or negedge locked)
        if(!resetn || !locked)serializer_resetn_q<=0;
        else serializer_resetn_q<={serializer_resetn_q[1:0],1'b1};
    always @(posedge clk_reset or negedge resetn or negedge locked)
        if(!resetn || !locked)lane_resetn_q<=0;
        else lane_resetn_q<={3{serializer_resetn_q[2]}};
    wire serializer_resetn=lane_resetn_q[0];
    wire [2:0] serial_data;
    genvar lane;
    generate for(lane=0;lane<3;lane=lane+1)begin: lanes
        wire [9:0] symbol;
        svo_tmds encoder(
            .clk(clk_pixel),.resetn(pixel_resetn_q[3]),.de(de),
            .ctrl(lane==0 ? {vsync,hsync}:2'b00),
            .din(rgb[23-8*lane -: 8]),.dout(symbol));
        OSER10 serializer(
            .Q(serial_data[lane]),.D0(symbol[0]),.D1(symbol[1]),.D2(symbol[2]),
            .D3(symbol[3]),.D4(symbol[4]),.D5(symbol[5]),.D6(symbol[6]),
            .D7(symbol[7]),.D8(symbol[8]),.D9(symbol[9]),
            .PCLK(clk_pixel),.FCLK(clk_5x_pixel),.RESET(~lane_resetn_q[lane]));
    end endgenerate
    ELVDS_OBUF output_buffers[3:0](.I({clk_pixel,serial_data}),
        .O({tmds_clk_p,tmds_d_p}),.OB({tmds_clk_n,tmds_d_n}));
endmodule
