`timescale 1ns/1ps
module tb_affine_cell;
parameter LANES=8;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,ce=1,we=0;
reg [3:0]addr=0;reg[7:0]weight_in=0,weight_zero=0;
reg[LANES*9-1:0]a_centered=0;reg[LANES*32-1:0]psum_in=0;
wire[LANES*9-1:0]data_out;wire[LANES*32-1:0]psum_out;
affine_pe_cell #(.LANES(LANES))dut(.*);
integer a,b,q,z,lane,av,expected,checked=0;
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 for(b=-255;b<=255;b=b+1)begin
  q=b>127?127:b<-128?-128:b;z=q-b;
  @(negedge clk);addr=(b+255)%16;weight_in=q;weight_zero=z;we=1;
  @(negedge clk);we=0;repeat(5)@(negedge clk);
  for(a=-255;a<=255;a=a+1)begin
   for(lane=0;lane<LANES;lane=lane+1)begin
    av=((a+255+17*lane)%511)-255;
    a_centered[lane*9+:9]=av;psum_in[lane*32+:32]=12345-71*lane;
   end
   repeat(3)@(negedge clk);
   for(lane=0;lane<LANES;lane=lane+1)begin
    av=((a+255+17*lane)%511)-255;expected=av*b+12345-71*lane;
    if($signed(psum_out[lane*32+:32])!==expected)$fatal(1,"AFFINE_CELL_FAIL a=%0d b=%0d lane=%0d actual=%0d expected=%0d",av,b,lane,$signed(psum_out[lane*32+:32]),expected);
    checked=checked+1;
   end
  end
 end
 $display("AFFINE_CELL_PASS checked=%0d lanes=%0d",checked,LANES);$finish;
end
endmodule
