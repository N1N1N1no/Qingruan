`timescale 1ns/1ps
module tb_fp32_exp;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,in_valid=0,cancel=0,out_ready=0;
reg[31:0]in_bits=0;
wire in_ready,busy,out_valid,out_cancelled;
wire[31:0]out_bits;wire[4:0]out_flags;
wire alu_valid,alu_ready,alu_out_valid,alu_out_ready;
wire[3:0]alu_op;wire[31:0]alu_a,alu_b,alu_result;wire[4:0]alu_flags;
fp32_exp dut(.*);
fp32_alu u_alu(.clk(clk),.rst_n(rst_n),.in_valid(alu_valid),.in_ready(alu_ready),.op(alu_op),.a(alu_a),.b(alu_b),
 .out_valid(alu_out_valid),.out_ready(alu_out_ready),.result(alu_result),.flags(alu_flags));
integer tick=0,checked=0,vectors=0,fd,rc,j;
always@(posedge clk)tick=tick+1;
task automatic send(input[31:0]raw);
 begin
  @(negedge clk);while(!in_ready)@(negedge clk);
  in_bits=raw;in_valid=1;@(negedge clk);in_valid=0;
 end
endtask
task automatic finish_check(input[31:0]raw,want,input bit want_nan,want_cancel);
 integer waited,stall;reg[31:0]held;reg[4:0]held_flags;begin
  waited=0;
  while(!out_valid)begin @(negedge clk);waited=waited+1;if(waited>2000)$fatal(1,"FP32_EXP_FAIL timeout raw=%08x pc=%0d state=%0d",raw,dut.pc,dut.state);end
  if(out_cancelled!==want_cancel)$fatal(1,"FP32_EXP_FAIL cancelled");
  if(!want_cancel)begin
   if(want_nan)begin if(out_bits[30:23]!==8'hff||out_bits[22:0]===0)$fatal(1,"FP32_EXP_FAIL NaN");end
   else if(out_bits!==want)$fatal(1,"FP32_EXP_FAIL bits raw=%08x actual=%08x expected=%08x",raw,out_bits,want);
   case(raw)
    32'h00000000,32'h80000000,32'h7f800000,32'hff800000:if(out_flags!==0)$fatal(1,"FP32_EXP_FAIL exact flags");
    32'h7f7fffff:if(out_flags!==5)$fatal(1,"FP32_EXP_FAIL overflow flags");
    32'hff7fffff:if(out_flags!==3)$fatal(1,"FP32_EXP_FAIL underflow flags");
    32'h7f800001:if(out_flags[4]!==1)$fatal(1,"FP32_EXP_FAIL signaling NaN flag");
   endcase
  end
  held=out_bits;held_flags=out_flags;stall=checked%4;
  repeat(stall)begin
   @(negedge clk);if(!out_valid||out_bits!==held||out_flags!==held_flags||out_cancelled!==want_cancel)$fatal(1,"FP32_EXP_FAIL backpressure");
  end
  out_ready=1;@(negedge clk);out_ready=0;checked=checked+1;
 end
endtask
task automatic one(input[31:0]raw,want,input bit want_nan);begin send(raw);finish_check(raw,want,want_nan,0);end endtask
task automatic abort_case(input integer mode);
 integer waited;begin
  send(32'hc0400000);waited=0;
  if(mode==1)while(dut.state!=2)begin @(negedge clk);waited=waited+1;if(waited>2000)$fatal(1,"FP32_EXP_FAIL issue wait");end
  if(mode==2)while(!u_alu.busy)begin @(negedge clk);waited=waited+1;if(waited>2000)$fatal(1,"FP32_EXP_FAIL divide wait");end
  cancel=1;@(negedge clk);cancel=0;
  finish_check(0,0,0,1);one(0,32'h3f800000,0);
 end
endtask
string vector_path;reg[31:0]raw,want;reg[3:0]want_nan;
initial begin
 if(!$value$plusargs("VECTORS=%s",vector_path))$fatal(1,"FP32_EXP_FAIL vectors argument");
 fd=$fopen(vector_path,"r");if(!fd)$fatal(1,"FP32_EXP_FAIL vectors file");
 repeat(4)@(negedge clk);rst_n=1;
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%h %h %h\n",raw,want,want_nan);
  if(rc!=3)$fatal(1,"FP32_EXP_FAIL vector format");one(raw,want,want_nan!=0);vectors=vectors+1;
 end
 $fclose(fd);
 abort_case(0);abort_case(1);abort_case(2);
 send(32'hc0400000);while(!u_alu.busy)@(negedge clk);rst_n=0;repeat(3)@(negedge clk);rst_n=1;
 if(out_valid||busy||alu_out_valid)$fatal(1,"FP32_EXP_FAIL reset flush");one(0,32'h3f800000,0);
 $display("FP32_EXP_PASS vectors=%0d cancellation_cases=3 reset_recovery=1",vectors);$finish;
end
endmodule
