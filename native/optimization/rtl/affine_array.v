// OpenACM-derived native affine INT8 array candidate.
// One original 16x8 SRAM per PE; independent bank addresses also permit LUT use.
// Horizontal skew/long static waits are replaced by a pipelined reduction tree.
module affine_array #(
 parameter ROWS=8,COLS=32,LANES=4,
 parameter LEVELS=$clog2(ROWS),BANKS=ROWS*COLS
)(
 input clk,rst_n,
 input[BANKS*4-1:0]bank_addr,input[BANKS-1:0]bank_ce,bank_we,
 input[BANKS*8-1:0]bank_write,output[BANKS*8-1:0]bank_read,
 input tile_valid,input[ROWS*LANES*8-1:0]a_raw,input[7:0]a_zero,
 input[COLS*8-1:0]b_zero,
 output dot_valid,output[COLS*LANES*32-1:0]dot_out
);
 reg[LEVELS+1:0]valids;
 reg[ROWS*LANES*9-1:0]a_centered;
 reg[COLS*8-1:0]b_zero_delayed;
 integer i;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin valids<=0;a_centered<=0;b_zero_delayed<=0;end
  else begin
   valids<={valids[LEVELS:0],tile_valid};
   if(tile_valid)begin
    b_zero_delayed<=b_zero;
    for(i=0;i<ROWS*LANES;i=i+1)a_centered[i*9+:9]<=$signed({a_raw[i*8+7],a_raw[i*8+:8]})-$signed({a_zero[7],a_zero});
   end
  end
 end
 assign dot_valid=valids[LEVELS+1];
 wire[COLS*LANES*ROWS*32-1:0]tree[0:LEVELS];
 genvar r,c,l,level,p;
 generate for(r=0;r<ROWS;r=r+1)begin:R
  for(c=0;c<COLS;c=c+1)begin:C
   localparam BANK=r*COLS+c;
   wire[7:0]raw=bank_read[BANK*8+:8];
   rfsp16x8 u_weight(.Q(bank_read[BANK*8+:8]),.CLK(clk),.CEN(~bank_ce[BANK]),.GWEN(~bank_we[BANK]),
    .A(bank_addr[BANK*4+:4]),.D(bank_write[BANK*8+:8]),.STOV(1'b0),.EMA(3'b111),.EMAW(2'b11),.EMAS(1'b1),.RET(1'b0),.QNAP(1'b0));
   wire signed[8:0]weight=$signed({raw[7],raw})-$signed({b_zero_delayed[c*8+7],b_zero_delayed[c*8+:8]});
   for(l=0;l<LANES;l=l+1)begin:L
    wire signed[8:0]value=a_centered[(r*LANES+l)*9+:9];reg signed[17:0]product;
    always@(posedge clk or negedge rst_n)begin if(!rst_n)product<=0;else if(valids[0])product<=value*weight;end
    assign tree[0][((c*LANES+l)*ROWS+r)*32+:32]={{14{product[17]}},product};
   end
  end
 end
 for(level=1;level<=LEVELS;level=level+1)begin:T
  localparam WIDTH=18+level;
  for(c=0;c<COLS;c=c+1)begin:C
   for(l=0;l<LANES;l=l+1)begin:L
    for(p=0;p<(ROWS>>level);p=p+1)begin:P
     localparam BASE=(c*LANES+l)*ROWS;
     wire signed[WIDTH-2:0]left=tree[level-1][(BASE+2*p)*32+:WIDTH-1];
     wire signed[WIDTH-2:0]right=tree[level-1][(BASE+2*p+1)*32+:WIDTH-1];
     reg signed[WIDTH-1:0]sum;
     always@(posedge clk or negedge rst_n)begin if(!rst_n)sum<=0;else if(valids[level])sum<={left[WIDTH-2],left}+{right[WIDTH-2],right};end
     assign tree[level][(BASE+p)*32+:32]={{(32-WIDTH){sum[WIDTH-1]}},sum};
    end
   end
  end
 end
 for(c=0;c<COLS;c=c+1)begin:OUT
  for(l=0;l<LANES;l=l+1)begin:L
   assign dot_out[(c*LANES+l)*32+:32]=tree[LEVELS][(c*LANES+l)*ROWS*32+:32];
  end
 end endgenerate
endmodule
