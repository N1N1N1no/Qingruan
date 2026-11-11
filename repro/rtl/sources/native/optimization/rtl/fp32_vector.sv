// One element of the shared FP32 vector engine. All operations have explicit
// rounding points; POST is float(int32)*scaleA*scaleB, optionally plus bias.
// DQ_S32 supports the model's zero=0; reject other offsets instead of wrapping
// a potentially 33-bit integer difference into an incorrect 32-bit conversion.
module fp32_vector(
 input wire clk,rst_n,
 input wire in_valid,output wire in_ready,input wire[3:0]in_op,
 input wire[31:0]in_a,in_b,in_c,in_d,in_zero,
 input wire cancel,output wire busy,
 output wire out_valid,input wire out_ready,output reg[31:0]out_bits,
 output reg[7:0]out_error,output reg[4:0]out_flags,
 output wire alu_valid,input wire alu_ready,output reg[3:0]alu_op,output reg[31:0]alu_a,alu_b,
 input wire alu_out_valid,output wire alu_out_ready,input wire[31:0]alu_result,input wire[4:0]alu_flags
);
 localparam IDLE=0,CHECK=1,ISSUE=2,WAIT=3,OUTPUT=4,DRAIN=5;
 reg[2:0]state,pc;reg[3:0]operation;reg[31:0]a,b,c,d,zero,temporary;
 wire signed[8:0]signed_difference=$signed({a[7],a[7:0]})-$signed({zero[7],zero[7:0]});
 wire signed[8:0]unsigned_difference=$signed({1'b0,a[7:0]})-$signed({1'b0,zero[7:0]});
 assign in_ready=state==IDLE&&!cancel;
 assign busy=state!=IDLE;assign out_valid=state==OUTPUT;
 assign alu_valid=state==ISSUE&&!cancel;
 assign alu_out_ready=state==WAIT||state==DRAIN;
 always @*begin
  alu_op=0;alu_a=0;alu_b=0;
  case(pc)
   0:begin alu_op=operation==0?4'd0:operation==1?4'd2:4'd3;alu_a=a;alu_b=b;end
   1:begin
    alu_op=5;
    alu_a=operation==3?{{23{signed_difference[8]}},signed_difference}:
          operation==4?{{23{unsigned_difference[8]}},unsigned_difference}:a;
   end
   2:begin alu_op=2;alu_a=temporary;alu_b=b;end
   3:begin alu_op=2;alu_a=temporary;alu_b=c;end
   4:begin alu_a=temporary;alu_b=d;end
  endcase
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;pc<=0;operation<=0;a<=0;b<=0;c<=0;d<=0;zero<=0;temporary<=0;
   out_bits<=0;out_error<=0;out_flags<=0;
  end else if(cancel&&state!=IDLE&&state!=OUTPUT&&state!=DRAIN)begin
   out_error<=4;out_bits<=0;
   if(state==WAIT&&!alu_out_valid)state<=DRAIN;
   else begin if(state==WAIT)out_flags<=out_flags|alu_flags;state<=OUTPUT;end
  end else case(state)
   IDLE:if(in_valid&&in_ready)begin
    operation<=in_op;a<=in_a;b<=in_b;c<=in_c;d<=in_d;zero<=in_zero;
    out_error<=0;out_flags<=0;state<=CHECK;
   end
   CHECK:if(operation>7||(operation==5&&zero!=0))begin out_error<=1;out_bits<=0;state<=OUTPUT;end
    else begin pc<=operation<=2?0:1;state<=ISSUE;end
   ISSUE:if(alu_valid&&alu_ready)state<=WAIT;
   WAIT:if(alu_out_valid)begin
    out_flags<=out_flags|alu_flags;pc<=pc+1'b1;temporary<=alu_result;
    if(pc==0||pc==2&&operation<6||pc==3&&operation==6||pc==4)begin out_bits<=alu_result;state<=OUTPUT;end
    else state<=ISSUE;
   end
   DRAIN:if(alu_out_valid)begin out_flags<=out_flags|alu_flags;state<=OUTPUT;end
   OUTPUT:if(out_ready)state<=IDLE;
   default:begin out_bits<=0;out_error<=8'hff;state<=OUTPUT;end
  endcase
 end
endmodule
