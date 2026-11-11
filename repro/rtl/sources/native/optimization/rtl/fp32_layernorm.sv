// Ordered FP32 LayerNorm, with a shared external HardFloat ALU.
// First pass: sum+=x; ss+=x*x. Then mean=sum/N,
// inv=1/sqrt(max(ss/N-mean*mean,0)+epsilon).
// Second pass: ((x-mean)*inv)*scale+bias, with four separate roundings.
// The caller streams/replays data and parameters; the core stores no row RAM.
// This supports model rows wider than the shared PE SRAM (e.g. 1536 floats).
module fp32_layernorm(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,input wire[31:0]cmd_elements,cmd_epsilon,
 input wire cancel,output wire busy,output reg done,output reg[7:0]error,
 input wire reduce_valid,output wire reduce_ready,input wire[31:0]reduce_value,
 output wire stats_valid,input wire stats_ready,output reg[31:0]mean,inverse,
 input wire apply_valid,output wire apply_ready,input wire[31:0]apply_x,apply_scale,apply_bias,
 output wire out_valid,input wire out_ready,output reg[31:0]out_bits,
 output wire alu_valid,input wire alu_ready,output reg[3:0]alu_op,
 output reg[31:0]alu_a,alu_b,
 input wire alu_out_valid,output wire alu_out_ready,input wire[31:0]alu_result
);
 localparam IDLE=0,CHECK=1,REDUCE_GET=2,ISSUE=3,WAIT=4,STATS=5,APPLY_GET=6,OUTPUT=7,DRAIN=8,COMPLETE=9;
 reg[3:0]state,pc;
 reg[31:0]elements,epsilon,index,x,sum,ss,square,denominator,temporary,variance,scale,bias;
 assign cmd_ready=state==IDLE&&!cancel;
 assign busy=state!=IDLE;
 assign reduce_ready=state==REDUCE_GET&&!cancel;
 assign apply_ready=state==APPLY_GET&&!cancel;
 assign stats_valid=state==STATS;
 assign out_valid=state==OUTPUT;
 assign alu_valid=state==ISSUE&&!cancel;
 assign alu_out_ready=state==WAIT||state==DRAIN;
 always @*begin
  alu_op=0;alu_a=0;alu_b=0;
  case(pc)
   0:begin alu_a=sum;alu_b=x;end
   1:begin alu_op=2;alu_a=x;alu_b=x;end
   2:begin alu_a=ss;alu_b=square;end
   3:begin alu_op=5;alu_a=elements;end
   4:begin alu_op=3;alu_a=sum;alu_b=denominator;end
   5:begin alu_op=3;alu_a=ss;alu_b=denominator;end
   6:begin alu_op=2;alu_a=mean;alu_b=mean;end
   7:begin alu_op=1;alu_a=temporary;alu_b=square;end
   8:begin alu_op=7;alu_a=variance;alu_b=0;end
   9:begin alu_a=temporary;alu_b=epsilon;end
   10:begin alu_op=4;alu_a=temporary;end
   11:begin alu_op=3;alu_a=32'h3f800000;alu_b=temporary;end
   12:begin alu_op=1;alu_a=x;alu_b=mean;end
   13:begin alu_op=2;alu_a=temporary;alu_b=inverse;end
   14:begin alu_op=2;alu_a=temporary;alu_b=scale;end
   15:begin alu_a=temporary;alu_b=bias;end
  endcase
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;pc<=0;done<=0;error<=0;elements<=0;epsilon<=0;index<=0;
   x<=0;sum<=0;ss<=0;square<=0;denominator<=0;temporary<=0;variance<=0;
   scale<=0;bias<=0;mean<=0;inverse<=0;out_bits<=0;
  end else begin
   done<=0;
   if(cancel&&state!=IDLE&&state!=DRAIN&&state!=COMPLETE)begin
    error<=4;
    // An accepted division/square-root cannot be withdrawn. Consume its
    // response before relinquishing the shared arithmetic owner.
    if(state==WAIT&&!alu_out_valid)state<=DRAIN;else state<=COMPLETE;
   end else case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     elements<=cmd_elements;epsilon<=cmd_epsilon;index<=0;sum<=0;ss<=0;
     mean<=0;inverse<=0;error<=0;state<=CHECK;
    end
    CHECK:if(elements==0||elements>32'h00ffffff)begin error<=1;state<=COMPLETE;end else state<=REDUCE_GET;
    REDUCE_GET:if(reduce_valid&&reduce_ready)begin x<=reduce_value;pc<=0;state<=ISSUE;end
    ISSUE:if(alu_valid&&alu_ready)state<=WAIT;
    WAIT:if(alu_out_valid)begin
     state<=ISSUE;pc<=pc+1'b1;
     case(pc)
      0:sum<=alu_result;
      1:square<=alu_result;
      2:begin
       ss<=alu_result;
       if(index+1==elements)begin index<=0;pc<=3;end
       else begin index<=index+1;state<=REDUCE_GET;end
      end
      3:denominator<=alu_result;
      4:mean<=alu_result;
      5,9,10,12,13,14:temporary<=alu_result;
      6:square<=alu_result;
      7:variance<=alu_result;
      // fmaxf(NaN,0) is 0. The finite negative-variance cancellation case
      // must clamp before epsilon and square-root, as in the frozen C.
      8:temporary<=(alu_result[3]||alu_result[0])?32'b0:variance;
      11:begin inverse<=alu_result;state<=STATS;end
      15:begin out_bits<=alu_result;state<=OUTPUT;end
     endcase
    end
    STATS:if(stats_ready)begin index<=0;state<=APPLY_GET;end
    APPLY_GET:if(apply_valid&&apply_ready)begin x<=apply_x;scale<=apply_scale;bias<=apply_bias;pc<=12;state<=ISSUE;end
    OUTPUT:if(out_ready)begin
     if(index+1==elements)state<=COMPLETE;else begin index<=index+1;state<=APPLY_GET;end
    end
    DRAIN:if(alu_out_valid)state<=COMPLETE;
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=COMPLETE;end
   endcase
  end
 end
endmodule
