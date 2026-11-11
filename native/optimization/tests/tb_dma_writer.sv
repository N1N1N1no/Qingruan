`timescale 1ns/1ps
module tb_dma_writer;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;
wire cmd_ready,busy,done;wire[3:0]error;
reg[31:0]cmd_address=0,cmd_row_bytes=0,cmd_rows=0,cmd_stride=0;
reg in_valid=0;wire in_ready;reg[63:0]in_data=0;reg[7:0]in_keep=0;reg in_row_last=0,in_command_last=0;
wire[31:0]m_axi_awaddr;wire[2:0]m_axi_awid;wire[7:0]m_axi_awlen;wire[2:0]m_axi_awsize;wire[1:0]m_axi_awburst;wire m_axi_awvalid,m_axi_awready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;wire m_axi_wlast,m_axi_wvalid,m_axi_wready;
reg[2:0]m_axi_bid=0;reg[1:0]m_axi_bresp=0;reg m_axi_bvalid=0;wire m_axi_bready;
axi_dma_writer dut(.*);
integer cycles=0,aw_stall=0,w_stall=0;
reg active=0,response_pending=0;reg[31:0]write_address=0;integer write_left=0,responses=0;
integer inject=0,commands=0,requests=0,bytes_checked=0,zero_strobes=0,source_stalls=0,bus_stalls=0;
byte unsigned memory[bit[31:0]];
reg[31:0]expected_base=0,expected_length=0,expected_rows=0,expected_stride=0;
integer src_row=0,src_byte=0;reg source_enable=0,source_accepted=0;
assign m_axi_awready=!active&&!response_pending&&!m_axi_bvalid&&cycles%7!=0&&aw_stall==0;
assign m_axi_wready=active&&cycles%5!=0&&w_stall==0;
function automatic[7:0]mem_byte(input[31:0]address);
 begin mem_byte=(address*37)+(address>>7)+13;end
endfunction
integer j,row,allowed;reg[31:0]address;
always@(posedge clk)begin
 cycles<=cycles+1;if(aw_stall>0)aw_stall<=aw_stall-1;if(w_stall>0)w_stall<=w_stall-1;
 source_accepted=in_valid&&in_ready;
 if(source_accepted)begin
  if(expected_length-src_byte>8)src_byte=src_byte+8;
  else begin src_byte=0;src_row=src_row+1;end
 end
 if(!rst_n)begin active<=0;response_pending<=0;m_axi_bvalid<=0;end
 else begin
  if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_awvalid&&m_axi_awready)begin
   if(m_axi_awaddr[2:0]!=0||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||
      {1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"DMA_WRITER_FAIL invalid AW");
   active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;requests=requests+1;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!active||m_axi_wlast!==(write_left==1))$fatal(1,"DMA_WRITER_FAIL WLAST/count");
   if(m_axi_wstrb==0)zero_strobes=zero_strobes+1;
   for(j=0;j<8;j=j+1)if(m_axi_wstrb[j])begin
    address=write_address+j;allowed=0;
    for(row=0;row<expected_rows;row=row+1)if(address>=expected_base+row*expected_stride&&address<expected_base+row*expected_stride+expected_length)allowed=1;
    if(!allowed)$fatal(1,"DMA_WRITER_FAIL write outside destination row addr=%08x",address);
    if(m_axi_wdata[j*8+:8]!==mem_byte(address))$fatal(1,"DMA_WRITER_FAIL data addr=%08x actual=%02x expected=%02x",address,m_axi_wdata[j*8+:8],mem_byte(address));
    memory[address]=m_axi_wdata[j*8+:8];bytes_checked=bytes_checked+1;
   end
   write_address<=write_address+8;write_left<=write_left-1;
   if(write_left==1)begin active<=0;response_pending<=1;end
  end
  if(response_pending&&!m_axi_bvalid&&cycles%4!=0)begin
   response_pending<=0;m_axi_bvalid<=1;m_axi_bresp<=(inject==1&&responses==0)?2:0;m_axi_bid<=(inject==2&&responses==0)?1:0;responses=responses+1;
  end
  if(in_valid&&!in_ready)source_stalls=source_stalls+1;
  if(m_axi_wvalid&&!m_axi_wready)bus_stalls=bus_stalls+1;
 end
end
integer send_bytes,k;
always@(negedge clk)begin
 if(!source_enable)in_valid=0;
 else if(source_accepted||!in_valid)begin
  in_valid=src_row<expected_rows&&cycles%9!=2;
  if(src_row<expected_rows)begin
   send_bytes=expected_length-src_byte;if(send_bytes>8)send_bytes=8;
   in_keep=8'hff>>(8-send_bytes);in_row_last=src_byte+send_bytes==expected_length;in_command_last=in_row_last&&src_row+1==expected_rows;
   for(k=0;k<8;k=k+1)in_data[k*8+:8]=k<send_bytes?mem_byte(expected_base+src_row*expected_stride+src_byte+k):0;
   if(inject==3&&src_row==0&&src_byte==0)in_keep=in_keep^1;
  end
 end
end
reg aw_was_stalled=0,w_was_stalled=0;reg[31:0]saved_aw;reg[7:0]saved_awlen,saved_strobe;reg[63:0]saved_w;reg saved_last;
always@(posedge clk)if(rst_n)begin
 if(aw_was_stalled&&(!m_axi_awvalid||m_axi_awaddr!==saved_aw||m_axi_awlen!==saved_awlen))$fatal(1,"DMA_WRITER_FAIL AW changed while stalled");
 if(w_was_stalled&&(!m_axi_wvalid||m_axi_wdata!==saved_w||m_axi_wstrb!==saved_strobe||m_axi_wlast!==saved_last))$fatal(1,"DMA_WRITER_FAIL W changed while stalled/cancelled");
 aw_was_stalled=m_axi_awvalid&&!m_axi_awready;saved_aw=m_axi_awaddr;saved_awlen=m_axi_awlen;
 w_was_stalled=m_axi_wvalid&&!m_axi_wready;saved_w=m_axi_wdata;saved_strobe=m_axi_wstrb;saved_last=m_axi_wlast;
 if(dut.fifo_count>16)$fatal(1,"DMA_WRITER_FAIL FIFO overflow");
end
task automatic transfer(input[31:0]dest,input integer length,rows,input[31:0]stride_value,input integer fault,cancel_after,stall_mode,want_error);
 integer waited,r,b;
 reg[31:0]addr;
 begin
  @(negedge clk);#1;if(!cmd_ready)$fatal(1,"DMA_WRITER_FAIL not ready");
  expected_base=dest;expected_length=length;expected_rows=rows;expected_stride=stride_value;
  src_row=0;src_byte=0;responses=0;inject=fault;memory.delete();
  cmd_address=dest;cmd_row_bytes=length;cmd_rows=rows;cmd_stride=stride_value;cmd_valid=1;source_enable=1;
  if(stall_mode==1)aw_stall=15;if(stall_mode==2)w_stall=25;
  @(negedge clk);#1;cmd_valid=0;waited=0;
  while(!done)begin
   cancel=cancel_after>=0&&waited==cancel_after;
   @(negedge clk);#1;waited=waited+1;if(waited>100000)$fatal(1,"DMA_WRITER_FAIL timeout");
  end
  cancel=0;source_enable=0;in_valid=0;
  if(error!==want_error)$fatal(1,"DMA_WRITER_FAIL error=%0d expected=%0d",error,want_error);
  if(active||response_pending||m_axi_bvalid)$fatal(1,"DMA_WRITER_FAIL response not drained");
  if(!want_error)begin
   if(src_row!=rows||src_byte!=0)$fatal(1,"DMA_WRITER_FAIL incomplete input");
   for(r=0;r<rows;r=r+1)for(b=0;b<length;b=b+1)begin
    addr=dest+r*stride_value+b;
    if(!memory.exists(addr)||memory[addr]!==mem_byte(addr))$fatal(1,"DMA_WRITER_FAIL missing destination byte %08x",addr);
   end
  end
  commands=commands+1;
 end
endtask
integer alignment,n,length;
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 for(alignment=0;alignment<8;alignment=alignment+1)for(n=0;n<9;n=n+1)begin
  case(n)0:length=1;1:length=7;2:length=8;3:length=9;4:length=15;5:length=16;6:length=17;7:length=129;8:length=5119;endcase
  transfer(32'h80000ff8+alignment,length,3,length+11+alignment,0,-1,0,0);
 end
 transfer(32'h80002003,33,5,0,0,-1,0,0);
 transfer(32'h83fffff9,7,1,0,0,-1,0,0);
 transfer(32'h7fffffff,8,1,8,0,-1,0,1);
 transfer(32'h83fffffc,8,1,8,0,-1,0,1);
 transfer(32'h80000000,0,1,8,0,-1,0,1);
 transfer(32'h80000000,8,0,8,0,-1,0,1);
 transfer(32'h83fff000,8,2,32'h80000000,0,-1,0,1);
 transfer(32'h80000103,300,2,320,1,-1,0,2);
 transfer(32'h80000103,300,2,320,2,-1,0,3);
 transfer(32'h80000103,300,2,320,3,-1,0,3);
 transfer(32'h80000103,300,2,320,0,3,1,4);
 transfer(32'h80000103,300,2,320,0,5,2,4);
 transfer(32'h80000103,300,2,320,0,-1,0,0);
 if(zero_strobes==0||source_stalls==0||bus_stalls==0)$fatal(1,"DMA_WRITER_FAIL missing backpressure/cancel coverage");
 $display("DMA_WRITER_PASS commands=%0d bytes=%0d bursts=%0d zero_strobes=%0d source_stalls=%0d bus_stalls=%0d",commands,bytes_checked,requests,zero_strobes,source_stalls,bus_stalls);$finish;
end
endmodule
