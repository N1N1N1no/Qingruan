// Standalone measurement composition. The controller's external arithmetic
// interface also permits pooling the same ALU with reduction engines later.
module fp32_vector_unit(
 input wire clk,rst_n,
 input wire in_valid,output wire in_ready,input wire[3:0]in_op,
 input wire[31:0]in_a,in_b,in_c,in_d,in_zero,
 input wire cancel,output wire busy,
 output wire out_valid,input wire out_ready,output wire[31:0]out_bits,
 output wire[7:0]out_error,output wire[4:0]out_flags
);
 wire alu_valid,alu_ready,alu_out_valid,alu_out_ready;
 wire[3:0]alu_op;wire[31:0]alu_a,alu_b,alu_result;wire[4:0]alu_flags;
 fp32_vector u_core(.*);
 fp32_alu u_alu(.clk(clk),.rst_n(rst_n),.in_valid(alu_valid),.in_ready(alu_ready),.op(alu_op),.a(alu_a),.b(alu_b),
  .out_valid(alu_out_valid),.out_ready(alu_out_ready),.result(alu_result),.flags(alu_flags));
endmodule
