`timescale 1ns/1ps

module pe_array #(
    parameter M = 16,
    parameter N = 16,
    parameter NUM_PE = M * N
)(
    input                       clk,
    input                       rst_n,

    input       [3:0]           sram_addr,
    input       [M-1:0]         sram_ce,
    input       [M-1:0]         sram_we,
    input       [N*8-1:0]       weight_in,

    input       [M*32-1:0]      data_in,
    input       [N*128-1:0]     psum_in,
    
    output      [N*128-1:0]     psum_out
);


wire [NUM_PE*32-1:0] data_wire;

wire [NUM_PE*128-1:0] psum_wire;


genvar row;
genvar col;

generate

    for (row = 0; row < M; row = row + 1) begin : GEN_ROW

        for (col = 0; col < N; col = col + 1) begin : GEN_COL

            pe_1sram_4mul u_pe (

                .clk        (clk),
                .rst_n      (rst_n),

                .sram_ce    (sram_ce[row]),
                .sram_we    (sram_we[row]),
                .sram_addr  (sram_addr),
                .sram_wdata (weight_in[col*8 +: 8]),

                .data_in ((col == 0) ? data_in[row*32 +: 32] : data_wire[(row*N + col - 1)*32 +: 32]),
                .psum_in ((row == 0) ? psum_in[col*128 +: 128] : psum_wire[((row-1)*N + col)*128 +: 128]),
                
                .data_out (data_wire[(row*N + col)*32 +: 32]),
                .psum_out (psum_wire[(row*N + col)*128 +: 128])

            );

        end

    end

endgenerate

generate

    for (col = 0; col < N; col = col + 1) begin : GEN_PSUM_OUT
        assign psum_out[col*128 +: 128] = psum_wire[((M-1)*N + col)*128 +: 128];
    end

endgenerate


endmodule