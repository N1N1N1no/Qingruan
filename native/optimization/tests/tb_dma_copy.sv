`timescale 1ns/1ps
module tb_dma_copy;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;
wire cmd_ready,busy,done;wire[7:0]error;wire[31:0]cycles,streamed_bytes;
reg[31:0]cmd_source=0,cmd_destination=0,cmd_row_bytes=0,cmd_rows=0,cmd_source_stride=0,cmd_destination_stride=0;
wire[31:0]m_axi_araddr,m_axi_awaddr;wire[2:0]m_axi_arid,m_axi_awid;
wire[7:0]m_axi_arlen,m_axi_awlen;wire[2:0]m_axi_arsize,m_axi_awsize;wire[1:0]m_axi_arburst,m_axi_awburst;
wire m_axi_arvalid,m_axi_arready,m_axi_rready,m_axi_awvalid,m_axi_awready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0,m_axi_bid=0;reg[1:0]m_axi_rresp=0,m_axi_bresp=0;
reg m_axi_rlast=0,m_axi_rvalid=0,m_axi_bvalid=0;
axi_dma_copy dut(.*);
integer tick=0,ar_stall=0,aw_stall=0,w_stall=0;
reg rd_active=0,wr_active=0,response_pending=0;
reg[31:0]read_address=0,write_address=0;integer read_left=0,write_left=0,produced=0,responses=0;
integer fault=0,commands=0,read_bursts=0,write_bursts=0,bytes_checked=0;
byte unsigned memory[bit[31:0]];
assign m_axi_arready=!rd_active&&!m_axi_rvalid&&tick%5!=0&&ar_stall==0;
assign m_axi_awready=!wr_active&&!response_pending&&!m_axi_bvalid&&tick%7!=0&&aw_stall==0;
assign m_axi_wready=wr_active&&tick%3!=0&&w_stall==0;
function automatic[7:0]initial_byte(input[31:0]address);begin initial_byte=address*37+(address>>7)+13;end endfunction
function automatic[63:0]read_word(input[31:0]address);
 integer i;begin for(i=0;i<8;i=i+1)read_word[i*8+:8]=memory.exists(address+i)?memory[address+i]:initial_byte(address+i);end
endfunction
reg[31:0]source_base=0,destination_base=0,row_length=0,source_stride=0,destination_stride=0;integer row_count=0;
integer j,r,found;reg[31:0]address,source_address;
always@(posedge clk)begin
 tick<=tick+1;if(ar_stall>0)ar_stall<=ar_stall-1;if(aw_stall>0)aw_stall<=aw_stall-1;if(w_stall>0)w_stall<=w_stall-1;
 if(!rst_n)begin rd_active<=0;wr_active<=0;m_axi_rvalid<=0;m_axi_bvalid<=0;response_pending<=0;end
 else begin
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;
  if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_arvalid&&m_axi_arready)begin
   if(m_axi_araddr[2:0]!=0||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||{1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"DMA_COPY_FAIL AR geometry");
   rd_active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;read_bursts=read_bursts+1;
  end
  if(rd_active&&(!m_axi_rvalid||m_axi_rready)&&tick%4!=0)begin
   m_axi_rvalid<=1;m_axi_rdata<=read_word(read_address);m_axi_rid<=(fault==2&&produced==3)?1:0;
   m_axi_rresp<=(fault==1&&produced==3)?2:0;m_axi_rlast<=read_left==1;
   produced=produced+1;read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)rd_active<=0;
  end
  if(m_axi_awvalid&&m_axi_awready)begin
   if(m_axi_awaddr[2:0]!=0||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||{1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"DMA_COPY_FAIL AW geometry");
   wr_active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;write_bursts=write_bursts+1;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!wr_active||m_axi_wlast!==(write_left==1))$fatal(1,"DMA_COPY_FAIL WLAST");
   for(j=0;j<8;j=j+1)if(m_axi_wstrb[j])begin
    address=write_address+j;found=0;source_address=0;
    for(r=0;r<row_count;r=r+1)if(address>=destination_base+r*destination_stride&&address<destination_base+r*destination_stride+row_length)begin
     found=1;source_address=source_base+r*source_stride+(address-destination_base-r*destination_stride);
    end
    if(!found||m_axi_wdata[j*8+:8]!==initial_byte(source_address))$fatal(1,"DMA_COPY_FAIL byte addr=%08x actual=%02x expected=%02x",address,m_axi_wdata[j*8+:8],initial_byte(source_address));
    memory[address]=m_axi_wdata[j*8+:8];bytes_checked=bytes_checked+1;
   end
   write_address<=write_address+8;write_left<=write_left-1;
   if(write_left==1)begin wr_active<=0;response_pending<=1;end
  end
  if(response_pending&&!m_axi_bvalid&&tick%4!=0)begin
   response_pending<=0;m_axi_bvalid<=1;m_axi_bresp<=(fault==3&&responses==0)?2:0;m_axi_bid<=(fault==4&&responses==0)?1:0;responses=responses+1;
  end
end
end
task automatic transfer(input[31:0]src,dst,input integer length,rows,input[31:0]srcstride,dststride,input integer inject,cancel_after,stall_mode,input[7:0]want_error);
 integer waited,rr,bb;reg[31:0]addr;
 begin
  @(negedge clk);if(!cmd_ready)$fatal(1,"DMA_COPY_FAIL not ready");
  memory.delete();source_base=src;destination_base=dst;row_length=length;row_count=rows;source_stride=srcstride;destination_stride=dststride;
  fault=inject;produced=0;responses=0;cmd_source=src;cmd_destination=dst;cmd_row_bytes=length;cmd_rows=rows;cmd_source_stride=srcstride;cmd_destination_stride=dststride;cmd_valid=1;
  if(stall_mode==1)begin ar_stall=15;aw_stall=20;end
  if(stall_mode==2)w_stall=30;
  @(negedge clk);cmd_valid=0;waited=0;
  while(!done)begin
   cancel=cancel_after>=0&&waited==cancel_after;@(negedge clk);waited=waited+1;if(waited>100000)$fatal(1,"DMA_COPY_FAIL timeout");
  end
  cancel=0;
  if(error!==want_error)$fatal(1,"DMA_COPY_FAIL error=%02x expected=%02x",error,want_error);
  if(rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid)$fatal(1,"DMA_COPY_FAIL premature completion");
  if(!want_error)begin
   if(streamed_bytes!=length*rows)$fatal(1,"DMA_COPY_FAIL byte counter");
   for(rr=0;rr<rows;rr=rr+1)for(bb=0;bb<length;bb=bb+1)begin
    addr=dst+rr*dststride+bb;if(!memory.exists(addr)||memory[addr]!==initial_byte(src+rr*srcstride+bb))$fatal(1,"DMA_COPY_FAIL missing destination byte");
   end
  end
  commands=commands+1;
 end
endtask
integer align_a,align_b,k,length;
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 for(align_a=0;align_a<8;align_a=align_a+1)for(align_b=0;align_b<8;align_b=align_b+1)for(k=0;k<5;k=k+1)begin
  case(k)0:length=1;1:length=9;2:length=17;3:length=129;4:length=1031;endcase
  transfer(32'h80000ff8+align_a,32'h80100ff8+align_b,length,3,length+5+align_a,length+13+align_b,0,-1,0,0);
 end
 transfer(32'h80004003,32'h80104007,33,5,0,47,0,-1,0,0);
 transfer(32'h7fffffff,32'h80100000,8,1,8,8,0,-1,0,8'h11);
 transfer(32'h80000000,32'h7fffffff,8,1,8,8,0,-1,0,8'h21);
 transfer(32'h80000000,32'h80100000,0,1,8,8,0,-1,0,8'h11);
 transfer(32'h80000000,32'h80100000,8,0,8,8,0,-1,0,8'h11);
 transfer(32'h80004003,32'h80104007,1027,2,1050,1050,1,-1,0,8'h12);
 transfer(32'h80004003,32'h80104007,1027,2,1050,1050,2,-1,0,8'h13);
 transfer(32'h80004003,32'h80104007,1027,2,1050,1050,3,-1,0,8'h22);
 transfer(32'h80004003,32'h80104007,1027,2,1050,1050,4,-1,0,8'h23);
 transfer(32'h80004003,32'h80104007,1027,2,1050,1050,0,3,1,8'h04);
 transfer(32'h80004003,32'h80104007,1027,2,1050,1050,0,5,2,8'h04);
 transfer(32'h80004003,32'h80104007,1027,2,1050,1050,0,-1,0,0);
 $display("DMA_COPY_PASS commands=%0d bytes=%0d read_bursts=%0d write_bursts=%0d",commands,bytes_checked,read_bursts,write_bursts);$finish;
end
endmodule
