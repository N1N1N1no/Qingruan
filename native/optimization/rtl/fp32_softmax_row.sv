// One contiguous row: max, exp(x-max), sequential FP32 sum, then e/sum.
// The existing PE SRAM holds x and is overwritten by e. Only one shared
// HardFloat ALU is used, including the classic-Newlib exp controller.
// The caller grants exclusive bank ownership and keeps MAC tile_valid low.
// A cancelled command invalidates the internal stream, drains arithmetic,
// and reports error=4; it does not publish a successful row.
module fp32_softmax_row #(
 parameter SHARED_ALU=0,BANKS=256, BANK_BITS=$clog2(BANKS), CAPACITY=BANKS*4
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,input wire[31:0]cmd_channels,
 input wire cancel,output wire busy,output reg done,output reg[7:0]error,
 input wire in_valid,output wire in_ready,input wire[63:0]in_data,
 input wire[7:0]in_keep,input wire in_last,
 output wire out_valid,input wire out_ready,output reg[63:0]out_data,
 output reg[7:0]out_keep,output wire out_last,
 output reg[BANKS*4-1:0]bank_addr,output reg[BANKS-1:0]bank_ce,bank_we,
 output reg[BANKS*8-1:0]bank_write,input wire[BANKS*8-1:0]bank_read,
 output wire shared_alu_valid,input wire shared_alu_ready,
 output wire[3:0]shared_alu_op,output wire[31:0]shared_alu_a,shared_alu_b,
 input wire shared_alu_out_valid,output wire shared_alu_out_ready,
 input wire[31:0]shared_alu_result,input wire[4:0]shared_alu_flags
);
 localparam IDLE=0,CHECK=1,LOAD=2,MAX_READ=3,MAX_WAIT=4,
  CMP_ISSUE=5,CMP_WAIT=6,X_READ=7,X_WAIT=8,SUB_ISSUE=9,SUB_WAIT=10,
  EXP_ISSUE=11,EXP_WAIT=12,SUM_ISSUE=13,SUM_WAIT=14,
  NORM_READ=15,NORM_WAIT=16,DIV_ISSUE=17,DIV_WAIT=18,
  OUTPUT=19,DRAIN=20,COMPLETE=21;
 reg[4:0]state;
 reg[31:0]channels,index,loaded_bytes,x,maximum,sum,exponential,difference;
 wire[31:0]row_bytes=channels<<2;
 wire[7:0]load_keep=row_bytes-loaded_bytes>=8?8'hff:8'h0f;
 wire load_last=row_bytes-loaded_bytes<=8;
 wire load_correct=in_keep==load_keep&&in_last==load_last;
 wire load_accept=in_valid&&in_ready;
 assign cmd_ready=state==IDLE&&!cancel;
 assign busy=state!=IDLE;
 assign in_ready=state==LOAD&&!cancel;
 assign out_valid=state==OUTPUT;
 assign out_last=index+1==channels;

 wire exp_in_ready,exp_busy,exp_out_valid,exp_cancelled;
 wire[31:0]exp_result;wire[4:0]exp_flags;
 wire exp_alu_valid,exp_alu_ready,exp_alu_out_ready;
 wire[3:0]exp_alu_op;wire[31:0]exp_alu_a,exp_alu_b;
 wire alu_ready,alu_out_valid,alu_out_ready;
 wire[31:0]alu_result;wire[4:0]alu_flags;
 reg[3:0]own_op;reg[31:0]own_a,own_b;
 wire own_issue=state==CMP_ISSUE||state==SUB_ISSUE||state==SUM_ISSUE||state==DIV_ISSUE;
 wire own_wait=state==CMP_WAIT||state==SUB_WAIT||state==SUM_WAIT||state==DIV_WAIT;
 wire alu_valid=exp_busy?exp_alu_valid:(own_issue&&!cancel);
 assign exp_alu_ready=exp_busy&&alu_ready;
 assign alu_out_ready=exp_busy?exp_alu_out_ready:(own_wait||state==DRAIN);
 reg alu_pending;
 fp32_exp u_exp(
  .clk(clk),.rst_n(rst_n),.in_valid(state==EXP_ISSUE&&!cancel),.in_ready(exp_in_ready),.in_bits(difference),
  .cancel(cancel||state==DRAIN),.busy(exp_busy),.out_valid(exp_out_valid),.out_ready(state==EXP_WAIT||state==DRAIN),
  .out_bits(exp_result),.out_flags(exp_flags),.out_cancelled(exp_cancelled),
  .alu_valid(exp_alu_valid),.alu_ready(exp_alu_ready),.alu_op(exp_alu_op),.alu_a(exp_alu_a),.alu_b(exp_alu_b),
  .alu_out_valid(exp_busy&&alu_out_valid),.alu_out_ready(exp_alu_out_ready),.alu_result(alu_result),.alu_flags(alu_flags));
 wire local_alu_ready,local_alu_out_valid;wire[31:0]local_alu_result;wire[4:0]local_alu_flags;
 assign alu_ready=SHARED_ALU?shared_alu_ready:local_alu_ready;
 assign alu_out_valid=SHARED_ALU?shared_alu_out_valid:local_alu_out_valid;
 assign alu_result=SHARED_ALU?shared_alu_result:local_alu_result;
 assign alu_flags=SHARED_ALU?shared_alu_flags:local_alu_flags;
 assign shared_alu_valid=SHARED_ALU&&alu_valid;
 assign shared_alu_op=SHARED_ALU?(exp_busy?exp_alu_op:own_op):4'b0;
 assign shared_alu_a=SHARED_ALU?(exp_busy?exp_alu_a:own_a):32'b0;
 assign shared_alu_b=SHARED_ALU?(exp_busy?exp_alu_b:own_b):32'b0;
 assign shared_alu_out_ready=SHARED_ALU&&alu_out_ready;
 generate if(!SHARED_ALU)begin:PRIVATE
  fp32_alu u_alu(.clk(clk),.rst_n(rst_n),.in_valid(alu_valid),.in_ready(local_alu_ready),.op((exp_busy?exp_alu_op:own_op)),.a((exp_busy?exp_alu_a:own_a)),.b((exp_busy?exp_alu_b:own_b)),
   .out_valid(local_alu_out_valid),.out_ready(alu_out_ready),.result(local_alu_result),.flags(local_alu_flags));
 end else begin:SHARED
  assign local_alu_ready=0;assign local_alu_out_valid=0;assign local_alu_result=0;assign local_alu_flags=0;
 end endgenerate

 always @*begin
  own_op=0;own_a=0;own_b=0;
  case(state)
   CMP_ISSUE:begin own_op=7;own_a=x;own_b=maximum;end
   SUB_ISSUE:begin own_op=1;own_a=x;own_b=maximum;end
   SUM_ISSUE:begin own_a=sum;own_b=exponential;end
   DIV_ISSUE:begin own_op=3;own_a=x;own_b=sum;end
  endcase
 end
 integer j,bank,offset;
 reg[31:0]cached_word;
 always @*begin
  bank_addr=0;bank_ce=0;bank_we=0;bank_write=0;cached_word=0;
  for(j=0;j<4;j=j+1)begin
   offset=(index<<2)+j;bank=offset&(BANKS-1);
   cached_word[j*8+:8]=bank_read[bank*8+:8];
  end
  if(load_accept&&load_correct)begin
   for(j=0;j<8;j=j+1)begin
    offset=loaded_bytes+j;bank=offset&(BANKS-1);
    bank_addr[bank*4+:4]=offset>>BANK_BITS;
    bank_ce[bank]=in_keep[j];bank_we[bank]=in_keep[j];
    bank_write[bank*8+:8]=in_data[j*8+:8];
   end
  end else if(state==MAX_READ||state==X_READ||state==NORM_READ||state==SUM_ISSUE)begin
   for(j=0;j<4;j=j+1)begin
    offset=(index<<2)+j;bank=offset&(BANKS-1);
    bank_addr[bank*4+:4]=offset>>BANK_BITS;bank_ce[bank]=1;
    bank_we[bank]=state==SUM_ISSUE&&!cancel;
    bank_write[bank*8+:8]=exponential[j*8+:8];
   end
  end
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;done<=0;error<=0;channels<=0;index<=0;loaded_bytes<=0;
   x<=0;maximum<=0;sum<=0;exponential<=0;difference<=0;out_data<=0;out_keep<=0;alu_pending<=0;
  end else begin
   done<=0;
   if(alu_valid&&alu_ready)alu_pending<=1;
   if(alu_out_valid&&alu_out_ready)alu_pending<=0;
   if(cancel&&state!=IDLE&&state!=DRAIN&&state!=COMPLETE)begin error<=4;state<=DRAIN;end
   else case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     channels<=cmd_channels;index<=0;loaded_bytes<=0;maximum<=32'hff800000;
     sum<=0;error<=0;out_data<=0;out_keep<=0;state<=CHECK;
    end
    CHECK:if(channels==0||channels>CAPACITY)begin error<=1;state<=COMPLETE;end else state<=LOAD;
    LOAD:if(load_accept)begin
     if(!load_correct)begin error<=2;state<=DRAIN;end
     else begin loaded_bytes<=loaded_bytes+(in_last?row_bytes-loaded_bytes:32'd8);if(in_last)state<=MAX_READ;end
    end
    MAX_READ:state<=MAX_WAIT;
    MAX_WAIT:begin x<=cached_word;state<=CMP_ISSUE;end
    CMP_ISSUE:if(alu_valid&&alu_ready)state<=CMP_WAIT;
    CMP_WAIT:if(alu_out_valid)begin
     if(alu_result[2])maximum<=x;
     if(index+1==channels)begin index<=0;state<=X_READ;end
     else begin index<=index+1;state<=MAX_READ;end
    end
    X_READ:state<=X_WAIT;
    X_WAIT:begin x<=cached_word;state<=SUB_ISSUE;end
    SUB_ISSUE:if(alu_valid&&alu_ready)state<=SUB_WAIT;
    SUB_WAIT:if(alu_out_valid)begin difference<=alu_result;state<=EXP_ISSUE;end
    EXP_ISSUE:if(exp_in_ready)state<=EXP_WAIT;
    EXP_WAIT:if(exp_out_valid)begin
     if(exp_cancelled)begin error<=4;state<=DRAIN;end
     else begin exponential<=exp_result;state<=SUM_ISSUE;end
    end
    SUM_ISSUE:if(alu_valid&&alu_ready)state<=SUM_WAIT;
    SUM_WAIT:if(alu_out_valid)begin
     sum<=alu_result;
     if(index+1==channels)begin index<=0;state<=NORM_READ;end
     else begin index<=index+1;state<=X_READ;end
    end
    NORM_READ:state<=NORM_WAIT;
    NORM_WAIT:begin x<=cached_word;state<=DIV_ISSUE;end
    DIV_ISSUE:if(alu_valid&&alu_ready)state<=DIV_WAIT;
    DIV_WAIT:if(alu_out_valid)begin
     if(index[0])out_data[63:32]<=alu_result;else out_data<={32'b0,alu_result};
     if(index[0]||index+1==channels)begin out_keep<=index[0]?8'hff:8'h0f;state<=OUTPUT;end
     else begin index<=index+1;state<=NORM_READ;end
    end
    OUTPUT:if(out_ready)begin
     if(index+1==channels)state<=COMPLETE;else begin index<=index+1;state<=NORM_READ;end
    end
    DRAIN:if(!exp_busy&&!alu_pending&&!alu_out_valid)state<=COMPLETE;
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=DRAIN;end
   endcase
  end
 end
endmodule
