`timescale 1ns/1ps
module tb_fp32_vector;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,in_valid=0,cancel=0,out_ready=0;reg[3:0]in_op=0;reg[31:0]in_a=0,in_b=0,in_c=0,in_d=0,in_zero=0;
wire in_ready,busy,out_valid;wire[31:0]out_bits;wire[7:0]out_error;wire[4:0]out_flags;
wire alu_valid,alu_ready,alu_out_valid,alu_out_ready;wire[3:0]alu_op;wire[31:0]alu_a,alu_b,alu_result;wire[4:0]alu_flags;
fp32_vector dut(.*);
fp32_alu u_alu(.clk(clk),.rst_n(rst_n),.in_valid(alu_valid),.in_ready(alu_ready),.op(alu_op),.a(alu_a),.b(alu_b),
 .out_valid(alu_out_valid),.out_ready(alu_out_ready),.result(alu_result),.flags(alu_flags));
integer checked=0,vectors=0;
task automatic send(input[3:0]op,input[31:0]a,b,c,d,zero);
begin
 @(negedge clk);while(!in_ready)@(negedge clk);
 in_op=op;in_a=a;in_b=b;in_c=c;in_d=d;in_zero=zero;in_valid=1;@(negedge clk);in_valid=0;
end endtask
task automatic finish_check(input[31:0]want,input[7:0]want_error);
 integer waited;reg[31:0]held;reg[4:0]held_flags;begin
 waited=0;while(!out_valid)begin @(negedge clk);waited=waited+1;if(waited>1000)$fatal(1,"VECTOR_FAIL timeout state=%0d pc=%0d",dut.state,dut.pc);end
 if(out_error!==want_error)$fatal(1,"VECTOR_FAIL error=%0d expected=%0d",out_error,want_error);
 if(!want_error)begin
  if((want&32'h7fffffff)>32'h7f800000)begin if((out_bits&32'h7fffffff)<=32'h7f800000||^out_bits===1'bx)$fatal(1,"VECTOR_FAIL NaN");end
  else if(out_bits!==want)$fatal(1,"VECTOR_FAIL op=%0d a=%08x b=%08x got=%08x expected=%08x",in_op,in_a,in_b,out_bits,want);
 end
 if(u_alu.busy||alu_out_valid)$fatal(1,"VECTOR_FAIL undrained ALU");
 held=out_bits;held_flags=out_flags;
 repeat(checked%5)begin @(negedge clk);if(!out_valid||out_bits!==held||out_flags!==held_flags||out_error!==want_error)$fatal(1,"VECTOR_FAIL stalled result");end
 out_ready=1;@(negedge clk);out_ready=0;checked=checked+1;
end endtask
task automatic abort_case(input integer mode);
 integer waited;begin
 if(mode==2)send(7,32'h12345678,32'h3f800000,32'h3f000000,32'h3f800000,0);
 else send(2,32'h3f800000,32'h40400000,0,0,0);
 waited=0;
 if(mode==1)while(!u_alu.busy)begin @(negedge clk);waited=waited+1;if(waited>1000)$fatal(1,"VECTOR_FAIL cancel divide");end
 if(mode==2)while(!(dut.state==2&&dut.pc==3))begin @(negedge clk);waited=waited+1;if(waited>1000)$fatal(1,"VECTOR_FAIL cancel postscale");end
 cancel=1;@(negedge clk);cancel=0;finish_check(0,4);
 send(0,0,0,0,0,0);finish_check(0,0);
end endtask
string vector_path;integer fd,rc;reg[3:0]op;reg[31:0]a,b,c,d,zero,want;reg[7:0]error;
initial begin
 if(!$value$plusargs("VECTORS=%s",vector_path))$fatal(1,"VECTOR_FAIL argument");
 fd=$fopen(vector_path,"r");if(!fd)$fatal(1,"VECTOR_FAIL file");
 repeat(4)@(negedge clk);rst_n=1;
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%h %h %h %h %h %h %h %h\n",op,a,b,c,d,zero,want,error);if(rc!=8)$fatal(1,"VECTOR_FAIL vector format");
  send(op,a,b,c,d,zero);finish_check(want,error);vectors=vectors+1;
 end
 $fclose(fd);abort_case(0);abort_case(1);abort_case(2);
 send(2,32'h3f800000,32'h40400000,0,0,0);while(!u_alu.busy)@(negedge clk);
 rst_n=0;repeat(3)@(negedge clk);rst_n=1;
 send(2,32'h3f800000,32'h3f800000,0,0,0);finish_check(32'h3f800000,0);
 $display("FP32_VECTOR_PASS vectors=%0d cancellation_cases=3 reset_recovery=1",vectors);$finish;
end
endmodule
