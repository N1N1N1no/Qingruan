`timescale 1ns/1ps
module tb_dma_reader;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;
wire cmd_ready,busy,done;wire[3:0]error;
reg[31:0]cmd_address=0,cmd_row_bytes=0,cmd_rows=0,cmd_stride=0;
wire out_valid;wire out_ready;wire[63:0]out_data;wire[7:0]out_keep;wire out_row_last,out_command_last;
wire[31:0]m_axi_araddr;wire[2:0]m_axi_arid;wire[7:0]m_axi_arlen;wire[2:0]m_axi_arsize;wire[1:0]m_axi_arburst;wire m_axi_arvalid,m_axi_arready;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0;reg[1:0]m_axi_rresp=0;reg m_axi_rlast=0,m_axi_rvalid=0;wire m_axi_rready;
axi_dma_reader dut(.*);
integer cycles=0,ar_stall=0;
assign out_ready=(cycles%11>=6);
reg active=0;reg[31:0]read_address=0;integer read_left=0,produced=0;
integer inject=0,requests=0,read_stalls=0,output_stalls=0;
assign m_axi_arready=!active&&!m_axi_rvalid&&(cycles%5!=0)&&ar_stall==0;
function automatic[7:0]mem_byte(input[31:0]address);
 begin mem_byte=(address*37)+(address>>7)+13;end
endfunction
function automatic[63:0]mem_word(input[31:0]address);
 integer j;begin for(j=0;j<8;j=j+1)mem_word[j*8+:8]=mem_byte(address+j);end
endfunction
always@(posedge clk)begin
 cycles<=cycles+1;if(ar_stall>0)ar_stall<=ar_stall-1;
 if(!rst_n)begin active<=0;m_axi_rvalid<=0;end
 else begin
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;
  if(m_axi_arvalid&&m_axi_arready)begin
   if(m_axi_araddr[2:0]!=0||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||
      {1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"DMA_READER_FAIL invalid AXI burst");
   active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;requests=requests+1;
  end
  if(active&&(!m_axi_rvalid||m_axi_rready)&&cycles%3!=0)begin
   m_axi_rvalid<=1;m_axi_rdata<=mem_word(read_address);
   m_axi_rid<=(inject==2&&produced==1)?1:0;
   m_axi_rresp<=(inject==1&&produced==1)?2:0;
   m_axi_rlast<=(read_left==1)&&(inject!=3);
   produced=produced+1;read_address<=read_address+8;read_left<=read_left-1;
   if(read_left==1)active<=0;
  end
  if(m_axi_rvalid&&!m_axi_rready)read_stalls=read_stalls+1;
 end
end
reg[31:0]expected_base=0,expected_length=0,expected_rows=0,expected_stride=0;
integer row_index=0,byte_index=0,bytes_checked=0,commands=0;
reg was_stalled=0,ar_was_stalled=0;
reg[63:0]saved_data;reg[7:0]saved_keep;reg saved_row,saved_command;
reg[31:0]saved_ar;reg[7:0]saved_len;
integer take,j;
always@(posedge clk)if(rst_n)begin
 if(ar_was_stalled&&(!m_axi_arvalid||m_axi_araddr!==saved_ar||m_axi_arlen!==saved_len))$fatal(1,"DMA_READER_FAIL AR changed while stalled");
 ar_was_stalled=m_axi_arvalid&&!m_axi_arready;saved_ar=m_axi_araddr;saved_len=m_axi_arlen;
 if(was_stalled&&!cancel&&error==0&&(!out_valid||out_data!==saved_data||out_keep!==saved_keep||out_row_last!==saved_row||out_command_last!==saved_command))$fatal(1,"DMA_READER_FAIL output changed while stalled");
 was_stalled=out_valid&&!out_ready;saved_data=out_data;saved_keep=out_keep;saved_row=out_row_last;saved_command=out_command_last;
 if(was_stalled)output_stalls=output_stalls+1;
 if(dut.fifo_count>16)$fatal(1,"DMA_READER_FAIL FIFO overflow");
 if(out_valid&&out_ready)begin
  take=expected_length-byte_index;if(take>8)take=8;
  if(out_keep!==(8'hff>>(8-take))||out_row_last!==(byte_index+take==expected_length)||out_command_last!==((byte_index+take==expected_length)&&(row_index+1==expected_rows)))$fatal(1,"DMA_READER_FAIL packed metadata");
  for(j=0;j<8;j=j+1)begin
   if(j<take)begin
    if(out_data[j*8+:8]!==mem_byte(expected_base+row_index*expected_stride+byte_index+j))$fatal(1,"DMA_READER_FAIL data row=%0d byte=%0d",row_index,byte_index+j);
    bytes_checked=bytes_checked+1;
   end else if(out_data[j*8+:8]!==0)$fatal(1,"DMA_READER_FAIL unmasked tail");
  end
  byte_index=byte_index+take;
  if(byte_index==expected_length)begin byte_index=0;row_index=row_index+1;end
 end
end
task automatic transfer(input[31:0]address,input integer length,rows,input[31:0]stride_value,input integer fault,cancel_after,want_error);
 integer wait_cycles;
 begin
  @(negedge clk);if(!cmd_ready)$fatal(1,"DMA_READER_FAIL command not ready");
  expected_base=address;expected_length=length;expected_rows=rows;expected_stride=stride_value;row_index=0;byte_index=0;
  produced=0;inject=fault;cmd_address=address;cmd_row_bytes=length;cmd_rows=rows;cmd_stride=stride_value;cmd_valid=1;
  if(cancel_after>=0)ar_stall=12;
  @(negedge clk);cmd_valid=0;wait_cycles=0;
  while(!done)begin
   cancel=(cancel_after>=0&&wait_cycles==cancel_after);
   @(negedge clk);wait_cycles=wait_cycles+1;
   if(wait_cycles>100000)$fatal(1,"DMA_READER_FAIL timeout");
  end
  cancel=0;
  if(error!==want_error)$fatal(1,"DMA_READER_FAIL error=%0d expected=%0d",error,want_error);
  if(!want_error&&(row_index!=rows||byte_index!=0))$fatal(1,"DMA_READER_FAIL incomplete row stream");
  if(active||m_axi_rvalid)$fatal(1,"DMA_READER_FAIL command ended before AXI drain");
  commands=commands+1;
 end
endtask
integer alignment,k,length;
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 for(alignment=0;alignment<8;alignment=alignment+1)begin
  for(k=0;k<9;k=k+1)begin
   case(k)0:length=1;1:length=7;2:length=8;3:length=9;4:length=15;5:length=16;6:length=17;7:length=129;8:length=5119;endcase
   transfer(32'h80000ff8+alignment,length,3,length+11+alignment,0,-1,0);
  end
 end
 transfer(32'h80002003,33,5,0,0,-1,0);
 transfer(32'h83fffff9,7,1,0,0,-1,0);
 transfer(32'h7fffffff,8,1,8,0,-1,1);
 transfer(32'h83fffffc,8,1,8,0,-1,1);
 transfer(32'h80000000,0,1,8,0,-1,1);
 transfer(32'h80000000,8,0,8,0,-1,1);
 transfer(32'h83fff000,8,2,32'h80000000,0,-1,1);
 transfer(32'h80000103,300,2,320,1,-1,2);
 transfer(32'h80000103,300,2,320,2,-1,3);
 transfer(32'h80000103,300,2,320,3,-1,3);
 transfer(32'h80000103,300,2,320,0,3,4);
 transfer(32'h80000103,300,2,320,0,-1,0);
 if(read_stalls==0||output_stalls==0)$fatal(1,"DMA_READER_FAIL missing backpressure coverage");
 $display("DMA_READER_PASS commands=%0d bytes=%0d bursts=%0d r_stalls=%0d output_stalls=%0d",commands,bytes_checked,requests,read_stalls,output_stalls);$finish;
end
endmodule
