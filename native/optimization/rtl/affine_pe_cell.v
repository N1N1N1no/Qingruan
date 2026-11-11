// Candidate OpenACM PE extension: keep one raw INT8 SRAM shared across lanes.
// Centered A is formed once per array row, outside the column replication.
// This is an exploration candidate; it is not yet selected for the daily SoC.
module affine_pe_cell #(parameter LANES=4)(
    input clk,rst_n,ce,we,
    input [3:0] addr,
    input [7:0] weight_in,weight_zero,
    input [LANES*9-1:0] a_centered,
    input [LANES*32-1:0] psum_in,
    output [LANES*9-1:0] data_out,
    output [LANES*32-1:0] psum_out
);
    wire [7:0] raw;
    rfsp16x8 u_sram(.Q(raw),.CLK(clk),.CEN(~ce),.GWEN(~we),.A(addr),.D(weight_in),
        .STOV(1'b0),.EMA(3'b111),.EMAW(2'b11),.EMAS(1'b1),.RET(1'b0),.QNAP(1'b0));
    reg signed [8:0] b_reg;
    reg [LANES*9-1:0] a_reg;
    assign data_out=a_reg;
    wire signed [8:0] b_value=$signed({raw[7],raw})-$signed({weight_zero[7],weight_zero});
    always @(posedge clk or negedge rst_n)begin
        if(!rst_n)begin b_reg<=0;a_reg<=0;end
        else begin b_reg<=b_value;a_reg<=a_centered;end
    end
    genvar lane;
    generate for(lane=0;lane<LANES;lane=lane+1)begin:L
        wire signed [8:0] av=a_reg[9*lane+:9];
        wire signed [17:0] product=av*b_reg;
        reg signed [17:0] product_reg;
        reg [31:0] psum_reg;
        always @(posedge clk or negedge rst_n)begin
            if(!rst_n)begin product_reg<=0;psum_reg<=0;end
            else begin product_reg<=product;psum_reg<=psum_in[32*lane+:32];end
        end
        assign psum_out[32*lane+:32]=psum_reg+{{14{product_reg[17]}},product_reg};
    end endgenerate
endmodule
