// Ordered pointwise pipeline. Every original FP32 rounding point is retained.
// DIV keeps the already-verified scalar/shared-ALU path. No FMA contraction.
module vecpipe_binary32(
 input wire is_mul,input wire[31:0]a,b,
 output wire[31:0]result,output wire[4:0]flags
);
 wire[32:0]ra,rb,added,multiplied;wire[31:0]add_bits,mul_bits;
 wire[4:0]add_flags,mul_flags;
 fNToRecFN #(8,24) ca(a,ra);
 fNToRecFN #(8,24) cb(b,rb);
 addRecFN #(8,24) add_u(.control(1'b1),.subOp(1'b0),.a(ra),.b(rb),
  .roundingMode(3'b000),.out(added),.exceptionFlags(add_flags));
 mulRecFN #(8,24) mul_u(.control(1'b1),.a(ra),.b(rb),
  .roundingMode(3'b000),.out(multiplied),.exceptionFlags(mul_flags));
 recFNToFN #(8,24) oa(added,add_bits);
 recFNToFN #(8,24) om(multiplied,mul_bits);
 assign result=is_mul?mul_bits:add_bits;
 assign flags=is_mul?mul_flags:add_flags;
endmodule

module fp32_vector_pipeline(
 input wire clk,rst_n,
 input wire in_valid,output wire in_ready,input wire[3:0]in_op,
 input wire[31:0]in_a,in_b,in_c,in_d,in_zero,
 input wire cancel,output wire busy,
 output wire out_valid,input wire out_ready,output wire[31:0]out_bits,
 output wire[7:0]out_error,output wire[4:0]out_flags,
 output wire alu_valid,input wire alu_ready,output wire[3:0]alu_op,output wire[31:0]alu_a,alu_b,
 input wire alu_out_valid,output wire alu_out_ready,input wire[31:0]alu_result,input wire[4:0]alu_flags
);
 reg[3:0]valid;
 reg[31:0]x[0:3],b[0:3],c[0:3],d[0:3];
 reg[3:0]operation[0:3];reg[7:0]error[0:3];reg[4:0]flags[0:3];
 reg cancel_pending;reg[4:0]cancel_flags;
 wire slow_ready,slow_busy,slow_valid;wire[31:0]slow_bits;wire[7:0]slow_error;wire[4:0]slow_flags;
 wire fast_empty=valid==0;
 wire select_slow=in_op==2;
 wire r3=!valid[3]||out_ready;
 wire r2=!valid[2]||r3;
 wire r1=!valid[1]||r2;
 wire r0=!valid[0]||r1;
 assign in_ready=!cancel&&!cancel_pending&&(select_slow?(fast_empty&&slow_ready):(!slow_busy&&r0));
 wire fast_fire=in_valid&&in_ready&&!select_slow;
 assign busy=slow_busy||!fast_empty||cancel_pending;
 assign out_valid=valid[3]||slow_valid||cancel_pending;
 // Once visible, a result is retained through backpressure and cancellation.
 assign out_bits=valid[3]?x[3]:slow_valid?slow_bits:32'b0;
 assign out_error=valid[3]?error[3]:slow_valid?slow_error:8'd4;
 assign out_flags=valid[3]?flags[3]:slow_valid?slow_flags:cancel_flags;
 fp32_vector scalar_div(
  .clk(clk),.rst_n(rst_n),.in_valid(in_valid&&select_slow&&fast_empty&&!cancel_pending&&!cancel),
  .in_ready(slow_ready),.in_op(in_op),.in_a(in_a),.in_b(in_b),.in_c(in_c),.in_d(in_d),.in_zero(in_zero),
  .cancel(cancel),.busy(slow_busy),.out_valid(slow_valid),.out_ready(out_ready&&!valid[3]),
  .out_bits(slow_bits),.out_error(slow_error),.out_flags(slow_flags),
  .alu_valid(alu_valid),.alu_ready(alu_ready),.alu_op(alu_op),.alu_a(alu_a),.alu_b(alu_b),
  .alu_out_valid(alu_out_valid),.alu_out_ready(alu_out_ready),.alu_result(alu_result),.alu_flags(alu_flags));

 wire signed[8:0]signed_difference=$signed({in_a[7],in_a[7:0]})-$signed({in_zero[7],in_zero[7:0]});
 wire signed[8:0]unsigned_difference=$signed({1'b0,in_a[7:0]})-$signed({1'b0,in_zero[7:0]});
 wire[31:0]integer_a=in_op==3?{{23{signed_difference[8]}},signed_difference}:
                         in_op==4?{{23{unsigned_difference[8]}},unsigned_difference}:in_a;
 wire[32:0]converted;wire[31:0]converted_bits;wire[4:0]converted_flags;
 iNToRecFN #(32,8,24) cv(.control(1'b1),.signedIn(1'b1),.in(integer_a),
  .roundingMode(3'b000),.out(converted),.exceptionFlags(converted_flags));
 recFNToFN #(8,24) cv_out(converted,converted_bits);
 wire bad_input=in_op>7||(in_op==5&&in_zero!=0);
 wire[31:0]stage1,stage2,stage3;wire[4:0]f1,f2,f3;
 vecpipe_binary32 first(.is_mul(operation[0]!=0),.a(x[0]),.b(b[0]),.result(stage1),.flags(f1));
 vecpipe_binary32 second(.is_mul(1'b1),.a(x[1]),.b(c[1]),.result(stage2),.flags(f2));
 vecpipe_binary32 third(.is_mul(1'b0),.a(x[2]),.b(d[2]),.result(stage3),.flags(f3));
 integer i;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   valid<=0;cancel_pending<=0;cancel_flags<=0;
   for(i=0;i<4;i=i+1)begin x[i]<=0;b[i]<=0;c[i]<=0;d[i]<=0;operation[i]<=0;error[i]<=0;flags[i]<=0;end
  end else if(cancel)begin
   valid[2:0]<=0;
   if(valid[3]&&out_ready)valid[3]<=0;
   if(|valid[2:0])begin
    cancel_pending<=1;
    cancel_flags<=(valid[0]?flags[0]:5'b0)|(valid[1]?flags[1]:5'b0)|(valid[2]?flags[2]:5'b0);
   end else if(cancel_pending&&!valid[3]&&!slow_valid&&out_ready)cancel_pending<=0;
  end else begin
   if(cancel_pending&&!valid[3]&&!slow_valid&&out_ready)cancel_pending<=0;
   if(r3)begin
    valid[3]<=valid[2];
    if(valid[2])begin
     operation[3]<=operation[2];error[3]<=error[2];b[3]<=b[2];c[3]<=c[2];d[3]<=d[2];
     x[3]<=error[2]!=0?32'b0:operation[2]==7?stage3:x[2];
     flags[3]<=error[2]!=0?5'b0:flags[2]|(operation[2]==7?f3:5'b0);
    end
   end
   if(r2)begin
    valid[2]<=valid[1];
    if(valid[1])begin
     operation[2]<=operation[1];error[2]<=error[1];b[2]<=b[1];c[2]<=c[1];d[2]<=d[1];
     x[2]<=error[1]!=0?32'b0:operation[1]>=6?stage2:x[1];
     flags[2]<=error[1]!=0?5'b0:flags[1]|(operation[1]>=6?f2:5'b0);
    end
   end
   if(r1)begin
    valid[1]<=valid[0];
    if(valid[0])begin
     operation[1]<=operation[0];error[1]<=error[0];b[1]<=b[0];c[1]<=c[0];d[1]<=d[0];
     x[1]<=error[0]!=0?32'b0:stage1;
     flags[1]<=error[0]!=0?5'b0:flags[0]|f1;
    end
   end
   if(r0)begin
    valid[0]<=fast_fire;
    if(fast_fire)begin
     operation[0]<=in_op;error[0]<=bad_input?8'd1:8'd0;b[0]<=in_b;c[0]<=in_c;d[0]<=in_d;
     x[0]<=bad_input?32'b0:in_op>=3?converted_bits:in_a;
     flags[0]<=bad_input?5'b0:in_op>=3?converted_flags:5'b0;
    end
   end
  end
 end
endmodule
