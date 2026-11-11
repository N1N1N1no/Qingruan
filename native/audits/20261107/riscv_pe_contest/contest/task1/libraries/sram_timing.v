`timescale 1ns/1fs
// Project-authored engineering timing shell. Functional contract follows reference SRAM.
// Unconditional checks intentionally match CURRENT Liberty; do not invent mode gating.
// All specify defaults are zero placeholders; SDF is required for timing acceptance.
module SRAM_6T_CORE_16x8_MC_TB(input [7:0] wd_in, output [7:0] rd_out,
 input [3:0] addr_in, input ce_in,we_in,clk);
reg [7:0] mem[0:15];
reg [7:0] rd_value;
reg notifier=0;
integer k;
assign rd_out=rd_value;
initial $display("REFERENCE_SRAM_BEHAVIORAL_MODEL instance=%m");
always @(posedge clk) begin
 if (!ce_in) begin
  if (!we_in) mem[addr_in] <= wd_in;
  else rd_value <= mem[addr_in];
 end
end
always @(notifier) if ($realtime>0) begin
 $display("SRAM_TIMING_VIOLATION instance=%m time_ns=%0.9f",$realtime);
 // Conservative corruption: hold violations may refer to an earlier address.
 for (k=0;k<16;k=k+1) mem[k] <= 8'hxx;
 rd_value <= 8'hxx;
end
specify
 (posedge clk => (rd_out[0] +: rd_value[0])) = (0,0);
 (posedge clk => (rd_out[1] +: rd_value[1])) = (0,0);
 (posedge clk => (rd_out[2] +: rd_value[2])) = (0,0);
 (posedge clk => (rd_out[3] +: rd_value[3])) = (0,0);
 (posedge clk => (rd_out[4] +: rd_value[4])) = (0,0);
 (posedge clk => (rd_out[5] +: rd_value[5])) = (0,0);
 (posedge clk => (rd_out[6] +: rd_value[6])) = (0,0);
 (posedge clk => (rd_out[7] +: rd_value[7])) = (0,0);
 $setup(ce_in,posedge clk,0,notifier);
 $hold(posedge clk,ce_in,0,notifier);
 $setup(we_in,posedge clk,0,notifier);
 $hold(posedge clk,we_in,0,notifier);
 $setup(addr_in[0],posedge clk,0,notifier);
 $hold(posedge clk,addr_in[0],0,notifier);
 $setup(addr_in[1],posedge clk,0,notifier);
 $hold(posedge clk,addr_in[1],0,notifier);
 $setup(addr_in[2],posedge clk,0,notifier);
 $hold(posedge clk,addr_in[2],0,notifier);
 $setup(addr_in[3],posedge clk,0,notifier);
 $hold(posedge clk,addr_in[3],0,notifier);
 $setup(wd_in[0],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[0],0,notifier);
 $setup(wd_in[1],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[1],0,notifier);
 $setup(wd_in[2],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[2],0,notifier);
 $setup(wd_in[3],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[3],0,notifier);
 $setup(wd_in[4],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[4],0,notifier);
 $setup(wd_in[5],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[5],0,notifier);
 $setup(wd_in[6],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[6],0,notifier);
 $setup(wd_in[7],posedge clk,0,notifier);
 $hold(posedge clk,wd_in[7],0,notifier);
 $width(posedge clk,0,0,notifier);
 $width(negedge clk,0,0,notifier);
 $period(posedge clk,0,notifier);
endspecify
endmodule
