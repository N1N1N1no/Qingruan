`timescale 1ns/1ps
module tb_vecpipe_protocol;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,in_valid=0,cancel=0,out_ready=0;
reg[3:0]in_op=0;reg[31:0]in_a=0,in_b=0,in_c=0,in_d=0,in_zero=0;
wire in_ready,busy,out_valid;wire[31:0]out_bits;wire[7:0]out_error;wire[4:0]out_flags;
wire alu_valid,alu_ready,alu_out_valid,alu_out_ready;wire[3:0]alu_op;
wire[31:0]alu_a,alu_b,alu_result;wire[4:0]alu_flags;
fp32_vector_pipeline dut(.*);
fp32_alu u_alu(.clk(clk),.rst_n(rst_n),.in_valid(alu_valid),.in_ready(alu_ready),.op(alu_op),.a(alu_a),.b(alu_b),
 .out_valid(alu_out_valid),.out_ready(alu_out_ready),.result(alu_result),.flags(alu_flags));
integer cycles=0,checked=0;
always@(posedge clk)begin cycles=cycles+1;if(cycles>10000)$fatal(1,"VECPIPE_PROTOCOL_FAIL timeout");end
task automatic send(input[3:0]op,input[31:0]a,b,c,d,zero);
begin
 @(negedge clk);while(!in_ready)@(negedge clk);
 in_op=op;in_a=a;in_b=b;in_c=c;in_d=d;in_zero=zero;in_valid=1;
 @(negedge clk);in_valid=0;
end endtask
task automatic finish_check(input[31:0]want,input[7:0]error);
 reg[44:0]held;begin
 while(!out_valid)@(negedge clk);
 if(out_bits!==want||out_error!==error)$fatal(1,"VECPIPE_PROTOCOL_FAIL result=%08x error=%02x expected=%08x/%02x",out_bits,out_error,want,error);
 held={out_bits,out_error,out_flags};
 repeat(3)begin @(negedge clk);if(!out_valid||{out_bits,out_error,out_flags}!==held)$fatal(1,"VECPIPE_PROTOCOL_FAIL stalled output");end
 out_ready=1;@(negedge clk);out_ready=0;checked=checked+1;
end endtask
task automatic drained;
 begin
 @(negedge clk);
 if(busy||out_valid||u_alu.busy||alu_out_valid)$fatal(1,"VECPIPE_PROTOCOL_FAIL undrained arithmetic");
 end
endtask
task automatic recover;
 begin send(0,32'h3f800000,32'h3f800000,0,0,0);finish_check(32'h40000000,0);drained();end
endtask
task automatic burst3;
 integer i;begin
 @(negedge clk);in_op=6;in_b=32'h3f800000;in_c=32'h3f800000;in_d=0;in_zero=0;in_valid=1;
 for(i=1;i<=3;i=i+1)begin
  in_a=i;if(!in_ready)$fatal(1,"VECPIPE_PROTOCOL_FAIL initial burst backpressure");
  @(negedge clk);
 end
 in_valid=0;
end endtask
task automatic reset_and_recover;
 begin
 rst_n=0;in_valid=0;cancel=0;out_ready=0;
 repeat(4)@(negedge clk);rst_n=1;
 @(negedge clk);if(out_valid||busy)$fatal(1,"VECPIPE_PROTOCOL_FAIL reset retained transaction");
 recover();
 end
endtask
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 // Cancel one fast request before it reaches the visible output.
 send(7,32'h12345678,32'h3f000000,32'h3f800000,32'h3f800000,0);
 cancel=1;@(negedge clk);cancel=0;finish_check(0,4);drained();recover();
 // Abort an entire in-flight group with one cancellation response.
 burst3();if(out_valid)$fatal(1,"VECPIPE_PROTOCOL_FAIL cancel stimulus late");
 cancel=1;@(negedge clk);cancel=0;finish_check(0,4);drained();recover();
 // A visible ready/valid result must survive cancel while later work is flushed.
 burst3();while(!out_valid)@(negedge clk);
 if(out_bits!==32'h3f800000||out_error!==0)$fatal(1,"VECPIPE_PROTOCOL_FAIL visible first value");
 cancel=1;repeat(2)@(negedge clk);
 if(!out_valid||out_bits!==32'h3f800000||out_error!==0)$fatal(1,"VECPIPE_PROTOCOL_FAIL cancel changed visible result");
 cancel=0;finish_check(32'h3f800000,0);finish_check(0,4);drained();recover();
 // The scalar iterative divider must drain its shared ALU before recovery.
 send(2,32'h3f800000,32'h40400000,0,0,0);
 while(!u_alu.busy)@(negedge clk);
 cancel=1;@(negedge clk);cancel=0;finish_check(0,4);drained();recover();
 // Reset is allowed to discard fast or iterative in-flight state.
 burst3();reset_and_recover();
 send(2,32'h3f800000,32'h40400000,0,0,0);
 while(!u_alu.busy)@(negedge clk);
 reset_and_recover();
 send(2,32'h3f800000,32'h3f800000,0,0,0);finish_check(32'h3f800000,0);drained();
 $display("VECPIPE_PROTOCOL_PASS checked=%0d cancel_single=1 cancel_group=1 visible_output_preserved=1 cancel_DIV_drained=1 fast_reset_recovery=1 DIV_reset_recovery=1",checked);
 $finish;
end
endmodule
