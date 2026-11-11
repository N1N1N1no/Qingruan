`timescale 1ns/1ps
module tb_quant_lookup;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cfg_clear=0,cfg_valid=0,in_valid=0,out_ready=0;
reg[7:0]cfg_index=0;reg[31:0]cfg_value=0,in_bits=0;
wire cfg_ready,in_ready,out_valid,out_invalid;wire[7:0]out_bits;
quant_lookup8 dut(.*);
reg[31:0]inputs[0:4095];reg[7:0]answers[0:4095];reg errors[0:4095];
integer tf,vf,rc,q,i,count,sent=0,got=0,cycles=0,checked=0;
reg prior_stall=0;reg[7:0]saved;reg saved_error;
string tables,vectors;
always@(posedge clk)if(rst_n)begin
 if(in_valid&&in_ready)sent=sent+1;
 if(prior_stall&&(!out_valid||out_bits!==saved||out_invalid!==saved_error))$fatal(1,"QUANT_LUT_FAIL backpressure");
 prior_stall=out_valid&&!out_ready;saved=out_bits;saved_error=out_invalid;
 if(out_valid&&out_ready)begin
  if(got>=count||out_invalid!==errors[got]||(!errors[got]&&out_bits!==answers[got]))$fatal(1,"QUANT_LUT_FAIL table=%0d index=%0d value=%08x actual=%02x expected=%02x",q,got,inputs[got],out_bits,answers[got]);
  got=got+1;checked=checked+1;
 end
end
initial begin
 if(!$value$plusargs("TABLES=%s",tables)||!$value$plusargs("VECTORS=%s",vectors))$fatal(1,"QUANT_LUT_FAIL arguments");
 tf=$fopen(tables,"r");vf=$fopen(vectors,"r");if(!tf||!vf)$fatal(1,"QUANT_LUT_FAIL files");
 repeat(4)@(negedge clk);rst_n=1;
 for(q=0;q<178;q=q+1)begin
  @(negedge clk);cfg_clear=1;@(negedge clk);cfg_clear=0;
  for(i=0;i<255;i=i+1)begin
   if(!cfg_ready)$fatal(1,"QUANT_LUT_FAIL config not ready");
   rc=$fscanf(tf,"%h",cfg_value);if(rc!=1)$fatal(1,"QUANT_LUT_FAIL table file");cfg_valid=1;cfg_index=i;@(negedge clk);
  end
  cfg_valid=0;
  rc=$fscanf(vf,"%d",count);if(rc!=1||count>4096)$fatal(1,"QUANT_LUT_FAIL vector count");
  for(i=0;i<count;i=i+1)begin rc=$fscanf(vf,"%h %h %h",inputs[i],answers[i],errors[i]);if(rc!=3)$fatal(1,"QUANT_LUT_FAIL vector file");end
  sent=0;got=0;cycles=0;
  while(got<count)begin
   @(negedge clk);in_valid=sent<count&&(cycles%11!=3);if(sent<count)in_bits=inputs[sent];out_ready=cycles%7!=0;cycles=cycles+1;
   if(cycles>4*count+20)$fatal(1,"QUANT_LUT_FAIL timeout");
  end
  @(negedge clk);in_valid=0;out_ready=0;
 end
 $display("QUANT_LOOKUP_PASS tables=178 checked=%0d",checked);$finish;
end
endmodule
