`timescale 1ns/1ps
module tb_dma_arbiter;
reg clk=0;always #5 clk=~clk;
reg rst_n=0;
reg[63:0]s_axi_awaddr=0,s_axi_araddr=0;reg[5:0]s_axi_awid=0,s_axi_arid=0;
reg[15:0]s_axi_awlen=0,s_axi_arlen=0;reg[5:0]s_axi_awsize={2{3'd3}},s_axi_arsize={2{3'd3}};
reg[3:0]s_axi_awburst={2{2'd1}},s_axi_arburst={2{2'd1}};
reg[1:0]s_axi_awvalid=0,s_axi_arvalid=0,s_axi_wlast=0,s_axi_wvalid=0,s_axi_bready=0,s_axi_rready=0;
wire[1:0]s_axi_awready,s_axi_arready,s_axi_wready,s_axi_bvalid,s_axi_rvalid,s_axi_rlast;
reg[127:0]s_axi_wdata=0;reg[15:0]s_axi_wstrb=16'hffff;
wire[5:0]s_axi_bid,s_axi_rid;wire[3:0]s_axi_bresp,s_axi_rresp;wire[127:0]s_axi_rdata;
wire[31:0]m_axi_awaddr,m_axi_araddr;wire[2:0]m_axi_awid,m_axi_arid;wire[7:0]m_axi_awlen,m_axi_arlen;
wire[2:0]m_axi_awsize,m_axi_arsize;wire[1:0]m_axi_awburst,m_axi_arburst;
wire m_axi_awvalid,m_axi_awready,m_axi_arvalid,m_axi_arready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready,m_axi_rready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[2:0]m_axi_bid=0,m_axi_rid=0;reg[1:0]m_axi_bresp=0,m_axi_rresp=0;reg m_axi_bvalid=0,m_axi_rvalid=0,m_axi_rlast=0;reg[63:0]m_axi_rdata=0;
axi_dma_arbiter dut(.*);
integer tick=0,write_master=0,write_index=0,write_beat=0,write_length=0;
integer writes=0,reads=0,write_contentions=0,read_contentions=0;
reg write_active=0,response_pending=0,read_active=0;reg[31:0]read_address=0;reg[2:0]read_id=0;integer read_left=0;
assign m_axi_awready=!write_active&&!response_pending&&!m_axi_bvalid&&tick>20&&tick%5!=0;
assign m_axi_arready=!read_active&&!m_axi_rvalid&&tick>25&&tick%7!=0;
// Deliberately ready without an active AW: the arbiter must gate the next W
// after WLAST until its matching response and next selected AW are complete.
assign m_axi_wready=tick%4!=0;
function automatic[63:0]payload(input integer master,index,beat);
 begin payload=64'hdacef00d00000000^(64'h1000000000000000*master)^(index<<16)^beat;end
endfunction
function automatic[63:0]read_payload(input[31:0]address,input[2:0]id);
 begin read_payload=64'ha1b2c3d400000000^{32'b0,address}^id;end
endfunction
always@(posedge clk)begin
 tick<=tick+1;
 if(!rst_n)begin write_active<=0;response_pending<=0;read_active<=0;m_axi_bvalid<=0;m_axi_rvalid<=0;end
 else begin
  if(&s_axi_awvalid)write_contentions=write_contentions+1;
  if(&s_axi_arvalid)read_contentions=read_contentions+1;
  if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;
  if(m_axi_awvalid&&m_axi_awready)begin
   write_master=(m_axi_awaddr>>20)&1;write_index=(m_axi_awaddr&32'hfffff)>>8;write_beat=0;write_length=m_axi_awlen+1;
   if(m_axi_awid!==((write_master*3+write_index)&7)||m_axi_awsize!=3||m_axi_awburst!=1)$fatal(1,"ARBITER_FAIL AW routing");
   m_axi_bid<=m_axi_awid;write_active<=1;writes=writes+1;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!write_active||m_axi_wdata!==payload(write_master,write_index,write_beat)||m_axi_wstrb!==8'hff||m_axi_wlast!==(write_beat+1==write_length))$fatal(1,"ARBITER_FAIL W owner/order");
   write_beat=write_beat+1;
   if(write_beat==write_length)begin write_active<=0;response_pending<=1;end
  end
  if(response_pending&&!m_axi_bvalid&&tick%9==0)begin response_pending<=0;m_axi_bvalid<=1;m_axi_bresp<=0;end
  if(m_axi_arvalid&&m_axi_arready)begin
   read_active<=1;read_address<=m_axi_araddr;read_id<=m_axi_arid;read_left<=m_axi_arlen+1;reads=reads+1;
   if(m_axi_arsize!=3||m_axi_arburst!=1)$fatal(1,"ARBITER_FAIL AR attributes");
  end
  if(read_active&&(!m_axi_rvalid||m_axi_rready)&&tick%3!=0)begin
   m_axi_rvalid<=1;m_axi_rid<=read_id;m_axi_rdata<=read_payload(read_address,read_id);m_axi_rresp<=0;m_axi_rlast<=read_left==1;
   read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)read_active<=0;
  end
 end
end
reg aw_stalled=0,ar_stalled=0,w_stalled=0;reg[31:0]saved_aw,saved_ar;reg[7:0]saved_awlen,saved_arlen;reg[2:0]saved_awid,saved_arid;
reg[63:0]saved_w;reg saved_last;
always@(posedge clk)if(rst_n)begin
 if(aw_stalled&&(!m_axi_awvalid||m_axi_awaddr!==saved_aw||m_axi_awlen!==saved_awlen||m_axi_awid!==saved_awid))$fatal(1,"ARBITER_FAIL AW changed while stalled");
 if(ar_stalled&&(!m_axi_arvalid||m_axi_araddr!==saved_ar||m_axi_arlen!==saved_arlen||m_axi_arid!==saved_arid))$fatal(1,"ARBITER_FAIL AR changed while stalled");
 if(w_stalled&&(!m_axi_wvalid||m_axi_wdata!==saved_w||m_axi_wlast!==saved_last))$fatal(1,"ARBITER_FAIL W changed while stalled");
 aw_stalled=m_axi_awvalid&&!m_axi_awready;saved_aw=m_axi_awaddr;saved_awlen=m_axi_awlen;saved_awid=m_axi_awid;
 ar_stalled=m_axi_arvalid&&!m_axi_arready;saved_ar=m_axi_araddr;saved_arlen=m_axi_arlen;saved_arid=m_axi_arid;
 w_stalled=m_axi_wvalid&&!m_axi_wready;saved_w=m_axi_wdata;saved_last=m_axi_wlast;
 if(tick>50000)$fatal(1,"ARBITER_FAIL starvation/timeout");
end
task automatic send_aw(input integer master);
 integer i;
 begin
  repeat(master*2)@(negedge clk);
  for(i=0;i<64;i=i+1)begin
   @(negedge clk);s_axi_awaddr[master*32+:32]=32'h80000000+master*32'h100000+i*256;s_axi_awid[master*3+:3]=(master*3+i)&7;s_axi_awlen[master*8+:8]=i%16;s_axi_awvalid[master]=1;
   @(posedge clk);while(!s_axi_awready[master])@(posedge clk);
   @(negedge clk);s_axi_awvalid[master]=0;
  end
 end
endtask
task automatic send_w(input integer master);
 integer i,j;
 begin
  for(i=0;i<64;i=i+1)for(j=0;j<=i%16;j=j+1)begin
   @(negedge clk);s_axi_wdata[master*64+:64]=payload(master,i,j);s_axi_wlast[master]=j==i%16;s_axi_wvalid[master]=1;
   @(posedge clk);while(!s_axi_wready[master])@(posedge clk);
   @(negedge clk);s_axi_wvalid[master]=0;
  end
 end
endtask
task automatic receive_b(input integer master);
 integer i;
 begin
  for(i=0;i<64;i=i+1)begin
   repeat((i+master)%7+1)@(negedge clk);s_axi_bready[master]=1;
   @(posedge clk);while(!s_axi_bvalid[master])@(posedge clk);
   if(s_axi_bid[master*3+:3]!==((master*3+i)&7)||s_axi_bresp[master*2+:2]!==0)$fatal(1,"ARBITER_FAIL B routing");
   @(negedge clk);s_axi_bready[master]=0;
  end
 end
endtask
task automatic send_ar(input integer master);
 integer i;
 begin
  repeat(master*3)@(negedge clk);
  for(i=0;i<64;i=i+1)begin
   @(negedge clk);s_axi_araddr[master*32+:32]=32'h80200000+master*32'h100000+i*256;s_axi_arid[master*3+:3]=(master*5+i)&7;s_axi_arlen[master*8+:8]=i%16;s_axi_arvalid[master]=1;
   @(posedge clk);while(!s_axi_arready[master])@(posedge clk);
   @(negedge clk);s_axi_arvalid[master]=0;
  end
 end
endtask
task automatic receive_r(input integer master);
 integer i,j;
 reg[31:0]address;reg[2:0]id;
 begin
  for(i=0;i<64;i=i+1)for(j=0;j<=i%16;j=j+1)begin
   repeat((i+j+master)%3+1)@(negedge clk);s_axi_rready[master]=1;
   @(posedge clk);while(!s_axi_rvalid[master])@(posedge clk);
   address=32'h80200000+master*32'h100000+i*256+j*8;id=(master*5+i)&7;
   if(s_axi_rid[master*3+:3]!==id||s_axi_rdata[master*64+:64]!==read_payload(address,id)||s_axi_rlast[master]!==(j==i%16)||s_axi_rresp[master*2+:2]!==0)$fatal(1,"ARBITER_FAIL R routing");
   @(negedge clk);s_axi_rready[master]=0;
  end
 end
endtask
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 fork send_aw(0);send_w(0);receive_b(0);send_ar(0);receive_r(0);send_aw(1);send_w(1);receive_b(1);send_ar(1);receive_r(1);join
 if(writes!=128||reads!=128||write_contentions==0||read_contentions==0)$fatal(1,"ARBITER_FAIL coverage");
 $display("DMA_ARBITER_PASS write_bursts=%0d read_bursts=%0d write_contentions=%0d read_contentions=%0d cycles=%0d",writes,reads,write_contentions,read_contentions,tick);$finish;
end
endmodule
