// FP32 exp controller matching Newlib 4.4.0's classic ef_exp.c operation order.
// Arithmetic uses the shared HardFloat unit; no FMA, reciprocal approximation,
// or host computation. Opcode 8 is float->int truncation toward zero.
//
// Algorithm derived from ef_exp.c (float conversion by Ian Lance Taylor):
// Copyright (C) 1993 by Sun Microsystems, Inc. All rights reserved.
// Developed at SunPro, a Sun Microsystems, Inc. business.
// Permission to use, copy, modify, and distribute this software is freely
// granted, provided that this notice is preserved.
// Original source/license and archive hash: vendor/newlib-exp-provenance.json.
module fp32_exp(
 input wire clk,rst_n,
 input wire in_valid,output wire in_ready,input wire[31:0]in_bits,
 input wire cancel,output wire busy,
 output wire out_valid,input wire out_ready,output reg[31:0]out_bits,
 output reg[4:0]out_flags,output reg out_cancelled,
 output wire alu_valid,input wire alu_ready,output reg[3:0]alu_op,
 output reg[31:0]alu_a,alu_b,
 input wire alu_out_valid,output wire alu_out_ready,
 input wire[31:0]alu_result,input wire[4:0]alu_flags
);
 localparam IDLE=0,CHECK=1,ISSUE=2,WAIT=3,ADJUST=4,OUTPUT=5,ABORT_DRAIN=6;
 reg[2:0]state;
 reg[4:0]pc;
 reg[31:0]original,x,hi,lo,t,p,c,y;
 reg signed[31:0]k;
 wire[31:0]magnitude={1'b0,original[30:0]};
 wire nan=magnitude>32'h7f800000;
 wire inf=magnitude==32'h7f800000;
 assign in_ready=state==IDLE&&!cancel;
 assign busy=state!=IDLE;
 assign out_valid=state==OUTPUT;
 assign alu_valid=state==ISSUE&&!cancel;
 assign alu_out_ready=state==WAIT||state==ABORT_DRAIN;
 always @*begin
  alu_op=0;alu_a=0;alu_b=0;
  case(pc)
   0:begin alu_op=2;alu_a=32'h3fb8aa3b;alu_b=original;end
   1:begin alu_a=p;alu_b=original[31]?32'hbf000000:32'h3f000000;end
   2:begin alu_op=8;alu_a=p;end
   3:begin alu_op=5;alu_a=k;end
   4:begin alu_op=2;alu_a=t;alu_b=32'h3f317180;end
   5:begin alu_op=1;alu_a=original;alu_b=p;end
   6:begin alu_op=2;alu_a=t;alu_b=32'h3717f7d1;end
   7:begin alu_op=1;alu_a=hi;alu_b=lo;end
   8:begin alu_op=1;alu_a=original;alu_b=original[31]?32'hbf317180:32'h3f317180;end
   9:begin alu_a=32'h3f800000;alu_b=original;end
   10:begin alu_op=2;alu_a=x;alu_b=x;end
   11:begin alu_op=2;alu_a=t;alu_b=32'h3331bb4c;end
   12:begin alu_a=32'hb5ddea0e;alu_b=p;end
   13:begin alu_op=2;alu_a=t;alu_b=p;end
   14:begin alu_a=32'h388ab355;alu_b=p;end
   15:begin alu_op=2;alu_a=t;alu_b=p;end
   16:begin alu_a=32'hbb360b61;alu_b=p;end
   17:begin alu_op=2;alu_a=t;alu_b=p;end
   18:begin alu_a=32'h3e2aaaab;alu_b=p;end
   19:begin alu_op=2;alu_a=t;alu_b=p;end
   20:begin alu_op=1;alu_a=x;alu_b=p;end
   21:begin alu_op=2;alu_a=x;alu_b=c;end
   22:begin alu_op=1;alu_a=k==0?c:32'h40000000;alu_b=k==0?32'h40000000:c;end
   23:begin alu_op=3;alu_a=p;alu_b=t;end
   24:begin alu_op=1;alu_a=k==0?p:lo;alu_b=k==0?x:p;end
   25:begin alu_op=1;alu_a=k==0?32'h3f800000:p;alu_b=k==0?p:hi;end
   26:begin alu_op=1;alu_a=32'h3f800000;alu_b=p;end
   27:begin alu_op=2;alu_a=y;alu_b=32'h0d800000;end
   28:begin alu_a=original;alu_b=original;end
  endcase
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;pc<=0;original<=0;x<=0;hi<=0;lo<=0;t<=0;p<=0;c<=0;y<=0;k<=0;
   out_bits<=0;out_flags<=0;out_cancelled<=0;
  end else if(cancel&&state!=IDLE&&state!=OUTPUT&&state!=ABORT_DRAIN)begin
   out_cancelled<=1;out_bits<=0;
   if(state==WAIT&&!alu_out_valid)state<=ABORT_DRAIN;
   else begin if(state==WAIT)out_flags<=out_flags|alu_flags;state<=OUTPUT;end
  end else case(state)
   IDLE:if(in_valid&&in_ready)begin
    original<=in_bits;x<=in_bits;hi<=0;lo<=0;k<=0;
    out_flags<=0;out_cancelled<=0;state<=CHECK;
   end
   CHECK:begin
    if(nan)begin pc<=28;state<=ISSUE;end
    else if(inf)begin out_bits<=original[31]?32'b0:original;state<=OUTPUT;end
    else if(!original[31]&&magnitude>32'h42b17217)begin out_bits<=32'h7f800000;out_flags<=5'b00101;state<=OUTPUT;end
    else if(original[31]&&magnitude>32'h42cff1b5)begin out_bits<=0;out_flags<=5'b00011;state<=OUTPUT;end
    else if(magnitude>32'h3eb17218)begin
     if(magnitude<32'h3f851592)begin
      pc<=8;k<=original[31]?-32'sd1:32'sd1;lo<=original[31]?32'hb717f7d1:32'h3717f7d1;
     end else pc<=0;
     state<=ISSUE;
    end else begin pc<=magnitude<32'h34000000 ? 9 : 10;state<=ISSUE;end
   end
   ISSUE:if(alu_valid&&alu_ready)state<=WAIT;
   WAIT:if(alu_out_valid)begin
    out_flags<=out_flags|alu_flags;state<=ISSUE;pc<=pc+1'b1;
    case(pc)
     0,1,4: p<=alu_result;
     2:k<=alu_result;
     3,10,22:t<=alu_result;
     5,8:begin hi<=alu_result;if(pc==8)pc<=7;end
     6:lo<=alu_result;
     7:begin x<=alu_result;pc<=10;end
     9,27,28:begin out_bits<=alu_result;state<=OUTPUT;end
     11,12,13,14,15,16,17,18,19,21,23,24:p<=alu_result;
     20:c<=alu_result;
     25:begin
      if(k==0)begin y<=alu_result;state<=ADJUST;end else p<=alu_result;
     end
     26:begin y<=alu_result;state<=ADJUST;end
     default:begin out_bits<=32'h7fc00000;out_flags<=5'b10000;state<=OUTPUT;end
    endcase
   end
   ADJUST:begin
    if(k==0)begin out_bits<=y;state<=OUTPUT;end
    else if(k>=-32'sd125)begin out_bits<=y+(k<<23);state<=OUTPUT;end
    else begin y<=y+((k+32'sd100)<<23);pc<=27;state<=ISSUE;end
   end
   ABORT_DRAIN:if(alu_out_valid)begin out_flags<=out_flags|alu_flags;state<=OUTPUT;end
   OUTPUT:if(out_ready)state<=IDLE;
   default:begin out_bits<=32'h7fc00000;out_flags<=5'b10000;state<=OUTPUT;end
  endcase
 end
endmodule
