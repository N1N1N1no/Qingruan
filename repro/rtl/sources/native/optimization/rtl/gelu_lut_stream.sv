// Reuse the PE's 16x8 SRAM banks for exact INT8 -> INT8 GELU tables.
// Sixteen banks per channel: low input nibble selects bank, high selects word.
// Eight different channels are read/written together, without bank conflicts.
// The owner must hold tile_valid=0 and grant exclusive bank access throughout.
module gelu_lut_stream #(
 parameter CHANNELS=16,BANKS=256,
 parameter INDEX_WIDTH=$clog2(CHANNELS*32),
 parameter CHANNEL_WIDTH=$clog2(CHANNELS)
)(
 input wire clk,rst_n,stream_reset,
 input wire load_valid,output wire load_ready,
 input wire[INDEX_WIDTH-1:0]load_index,input wire[63:0]load_data,
 input wire in_valid,output wire in_ready,input wire[63:0]in_data,
 input wire[7:0]in_keep,input wire in_row_last,in_command_last,
 output wire out_valid,input wire out_ready,output reg[63:0]out_data,
 output wire[7:0]out_keep,output wire out_row_last,out_command_last,
 output reg[BANKS*4-1:0]bank_addr,output reg[BANKS-1:0]bank_ce,bank_we,
 output reg[BANKS*8-1:0]bank_write,input wire[BANKS*8-1:0]bank_read
);
 reg pending;
 reg[CHANNEL_WIDTH-1:0]channel;
 reg[$clog2(BANKS)-1:0]selected[0:7];
 reg[7:0]keep;
 reg row_last,command_last;
 wire accept=in_valid&&in_ready;
 assign in_ready=!stream_reset&&!load_valid&&(!pending||out_ready);
 assign load_ready=!stream_reset&&!pending&&!in_valid;
 assign out_valid=pending&&!stream_reset;
 assign out_keep=keep;
 assign out_row_last=row_last;
 assign out_command_last=command_last;
 integer j,index;
 always @*begin
  bank_addr=0;bank_ce=0;bank_we=0;bank_write=0;
  if(load_valid&&load_ready)begin
   for(j=0;j<8;j=j+1)begin
    index=(load_index>>5)*16+(load_index[0]?8:0)+j;
    bank_ce[index]=1;bank_we[index]=1;
    bank_addr[index*4+:4]=(load_index>>1)&15;
    bank_write[index*8+:8]=load_data[j*8+:8];
   end
  end else if(accept)begin
   for(j=0;j<8;j=j+1)begin
    index=(channel+j)*16+in_data[j*8+:4];
    bank_ce[index]=in_keep[j];
    bank_addr[index*4+:4]=in_data[j*8+4+:4];
   end
  end
  out_data=0;
  for(j=0;j<8;j=j+1)if(keep[j])out_data[j*8+:8]=bank_read[selected[j]*8+:8];
 end
 integer i;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   pending<=0;channel<=0;keep<=0;row_last<=0;command_last<=0;
   for(i=0;i<8;i=i+1)selected[i]<=0;
  end else if(stream_reset)begin pending<=0;channel<=0;keep<=0;end
  else begin
   if(pending&&out_ready)pending<=0;
   if(accept)begin
    pending<=1;keep<=in_keep;row_last<=in_row_last;command_last<=in_command_last;
    for(i=0;i<8;i=i+1)selected[i]<=(channel+i)*16+in_data[i*8+:4];
    if(in_row_last)channel<=0;else channel<=channel+8;
   end
  end
 end
endmodule
