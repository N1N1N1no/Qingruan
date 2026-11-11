// Exact scalar-scale FP32->INT8 quantizer, using compiled transition thresholds.
// Eight binary-search stages, one accepted element/cycle after filling.
// This prototype uses FF tables; the final candidate may share PE scratch banks.
module quant_lookup8(
 input clk,rst_n,
 input cfg_clear,cfg_valid,output cfg_ready,
 input[7:0]cfg_index,input[31:0]cfg_value,
 input in_valid,output in_ready,input[31:0]in_bits,
 output out_valid,input out_ready,output[7:0]out_bits,output out_invalid
);
 reg[31:0]threshold[0:254];
 reg[254:0]loaded;reg[7:0]load_count;
 reg[7:0]valids,bad;
 reg[31:0]keys[0:7];reg[7:0]codes[0:7];
 wire empty=!(|valids);
 assign cfg_ready=empty&&!in_valid;
 wire step=!valids[7]||out_ready;
 assign in_ready=step&&!cfg_valid&&!cfg_clear&&(load_count==255);
 assign out_valid=valids[7];assign out_invalid=bad[7];assign out_bits=codes[7]^8'h80;
 wire[31:0]key=in_bits[31]?~in_bits:in_bits^32'h80000000;
 integer stage;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin loaded<=0;load_count<=0;valids<=0;bad<=0;for(stage=0;stage<8;stage=stage+1)begin keys[stage]<=0;codes[stage]<=0;end end
  else begin
   if(cfg_clear&&cfg_ready)begin loaded<=0;load_count<=0;end
   else if(cfg_valid&&cfg_ready&&cfg_index<255)begin
    threshold[cfg_index]<=cfg_value;loaded[cfg_index]<=1;
    if(!loaded[cfg_index])load_count<=load_count+1'b1;
   end
   if(step)begin
    valids[0]<=in_valid&&in_ready;bad[0]<=(&in_bits[30:23])&&(|in_bits[22:0]);
    keys[0]<=key;codes[0]<=(key>=threshold[0]);
    for(stage=1;stage<8;stage=stage+1)begin
     valids[stage]<=valids[stage-1];bad[stage]<=bad[stage-1];keys[stage]<=keys[stage-1];
     codes[stage]<=(codes[stage-1]<<1)|(keys[stage-1]>=threshold[(1<<stage)-1+codes[stage-1]]);
    end
   end
  end
 end
endmodule
