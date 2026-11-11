// Shared IEEE FP32 candidate. Berkeley HardFloat arithmetic, RISC-V specialization.
// No implicit FMA contraction: ADD and MUL are distinct rounded operations.
module fp32_alu(
 input clk,rst_n,
 input in_valid,output in_ready,
 input[3:0]op,input[31:0]a,b,
 output reg out_valid,input out_ready,
 output reg[31:0]result,output reg[4:0]flags
);
 wire[32:0]ra,rb,add,mul,divroot,itof;
 wire[31:0]addfn,mulfn,divfn,itoffn,ftoi;
 wire[4:0]addflags,mulflags,divflags,itofflags,cmpflags;
 wire[2:0]ftoiflags;
 wire lt,eq,gt,unordered,div_ready,div_valid,div_sqrt;
 reg busy;
 assign in_ready=!busy&&(!out_valid||out_ready)&&((op!=3&&op!=4)||div_ready);
 wire start=in_valid&&in_ready;
 fNToRecFN #(8,24) conv_a(a,ra);
 fNToRecFN #(8,24) conv_b(b,rb);
 addRecFN #(8,24) add_unit(.control(1'b1),.subOp(op==1),.a(ra),.b(rb),.roundingMode(3'b000),.out(add),.exceptionFlags(addflags));
 mulRecFN #(8,24) mul_unit(.control(1'b1),.a(ra),.b(rb),.roundingMode(3'b000),.out(mul),.exceptionFlags(mulflags));
 divSqrtRecFN_small #(8,24,0) div_unit(.nReset(rst_n),.clock(clk),.control(1'b1),.inReady(div_ready),
  .inValid(start&&(op==3||op==4)),.sqrtOp(op==4),.a(ra),.b(rb),.roundingMode(3'b000),
  .outValid(div_valid),.sqrtOpOut(div_sqrt),.out(divroot),.exceptionFlags(divflags));
 iNToRecFN #(32,8,24) int_to_float(.control(1'b1),.signedIn(1'b1),.in(a),.roundingMode(3'b000),.out(itof),.exceptionFlags(itofflags));
 // Opcode 8 implements the truncation required by the installed newlib expf
 // range-reduction cast; opcode 6 retains RNE conversion.
 recFNToIN #(8,24,32) float_to_int(.control(1'b1),.in(ra),.roundingMode(op==8?3'b001:3'b000),.signedOut(1'b1),.out(ftoi),.intExceptionFlags(ftoiflags));
 compareRecFN #(8,24) compare(.a(ra),.b(rb),.signaling(1'b0),.lt(lt),.eq(eq),.gt(gt),.unordered(unordered),.exceptionFlags(cmpflags));
 recFNToFN #(8,24) cv_add(add,addfn);
 recFNToFN #(8,24) cv_mul(mul,mulfn);
 recFNToFN #(8,24) cv_div(divroot,divfn);
 // Iterative HardFloat leaves unused exponent bits uninitialized for initial
 // special-case operations. Canonicalize by the recoded class before exporting
 // IEEE bits; this also preserves signed zero in strict four-state simulation.
 wire[31:0]div_clean=divroot[31:29]==3'b000 ? {divroot[32],31'b0} :
                         divroot[31:29]==3'b110 ? {divroot[32],8'hff,23'b0} :
                         divroot[31:29]==3'b111 ? 32'h7fc00000 : divfn;
 recFNToFN #(8,24) cv_int(itof,itoffn);
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin busy<=0;out_valid<=0;result<=0;flags<=0;end
  else begin
   if(out_valid&&out_ready)out_valid<=0;
   if(start)begin
    case(op)
     0,1:begin result<=addfn;flags<=addflags;out_valid<=1;end
     2:begin result<=mulfn;flags<=mulflags;out_valid<=1;end
     3,4:busy<=1;
     5:begin result<=itoffn;flags<=itofflags;out_valid<=1;end
     6,8:begin result<=ftoi;flags<={ftoiflags[2]|ftoiflags[1],3'b000,ftoiflags[0]};out_valid<=1;end
     7:begin result<={28'b0,unordered,gt,eq,lt};flags<=cmpflags;out_valid<=1;end
     default:begin result<=32'h7fc00000;flags<=5'b10000;out_valid<=1;end
    endcase
   end
   if(busy&&div_valid)begin result<=div_clean;flags<=divflags;out_valid<=1;busy<=0;end
  end
 end
endmodule
