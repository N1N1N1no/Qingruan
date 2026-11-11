`timescale 1ns/1ps
module tb_affine_array;
`ifndef ARRAY_ROWS
`define ARRAY_ROWS 8
`endif
`ifndef ARRAY_COLS
`define ARRAY_COLS 32
`endif
`ifndef ARRAY_LANES
`define ARRAY_LANES 4
`endif
parameter ROWS=`ARRAY_ROWS,COLS=`ARRAY_COLS,LANES=`ARRAY_LANES,BANKS=ROWS*COLS;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,tile_valid=0;
reg[BANKS*4-1:0]bank_addr=0;reg[BANKS-1:0]bank_ce={BANKS{1'b1}},bank_we=0;
reg[BANKS*8-1:0]bank_write=0;wire[BANKS*8-1:0]bank_read;
reg[ROWS*LANES*8-1:0]a_raw=0;reg[7:0]a_zero=0;reg[COLS*8-1:0]b_zero=0;
wire dot_valid;wire[COLS*LANES*32-1:0]dot_out;
affine_array #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES))dut(.*);
reg[7:0]weights[0:15][0:BANKS-1];
reg[31:0]expected[0:999][0:COLS*LANES-1];
integer slot,bank,r,c,l,t,sum,av,bv,za,zb,address,sent=0,received=0,checked=0;
always@(posedge clk)if(rst_n)begin
 if(tile_valid)begin
  za=$signed(a_zero);
  for(c=0;c<COLS;c=c+1)for(l=0;l<LANES;l=l+1)begin
   sum=0;zb=$signed(b_zero[c*8+:8]);
   for(r=0;r<ROWS;r=r+1)begin
    av=$signed(a_raw[(r*LANES+l)*8+:8]);address=bank_addr[(r*COLS+c)*4+:4];bv=$signed(weights[address][r*COLS+c]);
    sum=sum+(av-za)*(bv-zb);
   end
   expected[sent][c*LANES+l]=sum;
  end
  sent=sent+1;
 end
 if(dot_valid)begin
  if(received>=sent)$fatal(1,"ARRAY_FAIL unexpected result");
  for(c=0;c<COLS*LANES;c=c+1)begin
   if(dot_out[c*32+:32]!==expected[received][c])$fatal(1,"ARRAY_FAIL tile=%0d result=%0d actual=%0d expected=%0d",received,c,$signed(dot_out[c*32+:32]),$signed(expected[received][c]));
   checked=checked+1;
  end
  received=received+1;
 end
end
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 for(slot=0;slot<16;slot=slot+1)begin
  for(bank=0;bank<BANKS;bank=bank+1)begin
   weights[slot][bank]=(slot*71+bank*37+128)%256;
   bank_addr[bank*4+:4]=slot;bank_write[bank*8+:8]=weights[slot][bank];
  end
  bank_we={BANKS{1'b1}};@(negedge clk);
 end
 bank_we=0;
 for(t=0;t<1100;t=t+1)begin
  tile_valid=(t%11!=3)&&(sent<1000);
  a_zero=t*47+128;
  for(bank=0;bank<BANKS;bank=bank+1)bank_addr[bank*4+:4]=(t+bank*3)%16;
  for(r=0;r<ROWS*LANES;r=r+1)a_raw[r*8+:8]=t*97+r*67+128;
  for(c=0;c<COLS;c=c+1)b_zero[c*8+:8]=t*53+c*31+128;
  @(negedge clk);
 end
 tile_valid=0;repeat(12)@(negedge clk);
 if(sent!=1000||received!=1000)$fatal(1,"ARRAY_FAIL incomplete sent=%0d received=%0d",sent,received);
 $display("AFFINE_ARRAY_PASS R=%0d C=%0d L=%0d tiles=%0d outputs=%0d",ROWS,COLS,LANES,received,checked);$finish;
end
endmodule
