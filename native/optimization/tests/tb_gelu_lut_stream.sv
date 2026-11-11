`timescale 1ns/1ps
module tb_gelu_lut_stream;
parameter CHANNELS=16,BANKS=256;
localparam INDEX_WIDTH=$clog2(CHANNELS*32);
reg clk=0;always #5 clk=~clk;
reg rst_n=0,stream_reset=0,load_valid=0,in_valid=0,out_ready=0;
reg[INDEX_WIDTH-1:0]load_index=0;
reg[63:0]load_data=0,in_data=0;
reg[7:0]in_keep=0;
reg in_row_last=0,in_command_last=0;
wire load_ready,in_ready,out_valid,out_row_last,out_command_last;
wire[63:0]out_data;wire[7:0]out_keep;
wire[BANKS*4-1:0]bank_addr;wire[BANKS-1:0]bank_ce,bank_we;
wire[BANKS*8-1:0]bank_write,bank_read;
gelu_lut_stream #(.CHANNELS(CHANNELS),.BANKS(BANKS))dut(.*);
wire unused_valid;wire[32*4*32-1:0]unused_dot;
affine_array #(.ROWS(8),.COLS(32),.LANES(4))shared_pe(
 .clk(clk),.rst_n(rst_n),.bank_addr(bank_addr),.bank_ce(bank_ce),.bank_we(bank_we),.bank_write(bank_write),.bank_read(bank_read),
 .tile_valid(1'b0),.a_raw(256'b0),.a_zero(8'b0),.b_zero(256'b0),.dot_valid(unused_valid),.dot_out(unused_dot));
reg[7:0]tables[0:4521983];
string table_path;integer tick=0,block=0,blocks=0,accepted=0,checked=0,total=0,j,word_index,timeout;
integer raw,channel;reg[63:0]expected,last_data;reg[7:0]last_keep;reg last_row,last_command,stalled=0;
always@(posedge clk)begin
 tick=tick+1;
 if(rst_n&&!stream_reset)begin
  if(stalled&&(!out_valid||out_data!==last_data||out_keep!==last_keep||out_row_last!==last_row||out_command_last!==last_command))$fatal(1,"GELU_LUT_STREAM_FAIL output changed under backpressure");
  if(out_valid&&out_ready)begin
   expected=0;
   for(j=0;j<8;j=j+1)begin
    channel=(checked*8+j)%CHANNELS;raw=((checked*8+j)/CHANNELS+17*channel)&255;
    expected[j*8+:8]=tables[block*CHANNELS*256+channel*256+raw];
   end
   if(out_data!==expected||out_keep!==8'hff||out_row_last!==((checked+1)%(CHANNELS/8)==0)||out_command_last!==(checked==CHANNELS*32-1))$fatal(1,"GELU_LUT_STREAM_FAIL block=%0d beat=%0d actual=%016x expected=%016x",block,checked,out_data,expected);
   checked=checked+1;total=total+8;
  end
  if(in_valid&&in_ready)accepted=accepted+1;
 end
 stalled=out_valid&&!out_ready&&!stream_reset;last_data=out_data;last_keep=out_keep;last_row=out_row_last;last_command=out_command_last;
end
initial begin
 if(!$value$plusargs("TABLES=%s",table_path))$fatal(1,"GELU_LUT_STREAM_FAIL missing tables");
 $readmemh(table_path,tables);repeat(4)@(negedge clk);rst_n=1;
 for(block=0;block<4521984/(CHANNELS*256);block=block+1)begin
  stream_reset=1;@(negedge clk);stream_reset=0;accepted=0;checked=0;
  for(word_index=0;word_index<CHANNELS*32;word_index=word_index+1)begin
   load_valid=1;load_index=word_index;
   for(j=0;j<8;j=j+1)load_data[j*8+:8]=tables[block*CHANNELS*256+word_index*8+j];
   #1;if(!load_ready)$fatal(1,"GELU_LUT_STREAM_FAIL table loading blocked");
   @(negedge clk);
  end
  load_valid=0;timeout=0;
  while(checked<CHANNELS*32)begin
   in_valid=accepted<CHANNELS*32&&tick%7!=0;out_ready=tick%5!=0&&tick%13!=0;
   in_keep=8'hff;in_row_last=(accepted+1)%(CHANNELS/8)==0;in_command_last=accepted==CHANNELS*32-1;
   for(j=0;j<8;j=j+1)begin
    channel=(accepted*8+j)%CHANNELS;raw=((accepted*8+j)/CHANNELS+17*channel)&255;in_data[j*8+:8]=raw;
   end
   @(negedge clk);timeout=timeout+1;if(timeout>CHANNELS*100)$fatal(1,"GELU_LUT_STREAM_FAIL timeout");
  end
  in_valid=0;out_ready=0;blocks=blocks+1;
 end
 if(total!=4521984)$fatal(1,"GELU_LUT_STREAM_FAIL coverage");
 $display("GELU_LUT_STREAM_PASS blocks=%0d values=%0d existing_PE_SRAMs=%0d",blocks,total,BANKS);$finish;
end
endmodule
