`timescale 1ns/1ps
module tb_axi_reorder_engine;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;wire cmd_ready,busy,done;wire[7:0]error;wire[31:0]cycles,streamed_bytes;
reg[31:0]cmd_source=0,cmd_destination=0,cmd_row_bytes=0,cmd_rank=0;
reg[127:0]cmd_dimensions=0,cmd_source_strides=0;
integer dims[0:3],steps[0:3],rank_value=0,row_bytes_value=0,node_value=0;
integer expected_total=0;
wire[31:0]m_axi_araddr,m_axi_awaddr;wire[2:0]m_axi_arid,m_axi_awid;
wire[7:0]m_axi_arlen,m_axi_awlen;wire[2:0]m_axi_arsize,m_axi_awsize;wire[1:0]m_axi_arburst,m_axi_awburst;
wire m_axi_arvalid,m_axi_arready,m_axi_rready,m_axi_awvalid,m_axi_awready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0,m_axi_bid=0;reg[1:0]m_axi_rresp=0,m_axi_bresp=0;
reg m_axi_rlast=0,m_axi_rvalid=0,m_axi_bvalid=0;
axi_reorder_engine dut(.*);
byte unsigned memory[bit[31:0]];
integer tick=0,ar_stall=0,aw_stall=0,w_stall=0;
reg rd_active=0,wr_active=0,response_pending=0;
reg[31:0]read_address=0,write_address=0;integer read_left=0,write_left=0,produced=0,responses=0;
integer fault=0,commands=0,read_bursts=0,write_bursts=0,bytes_checked=0;
assign m_axi_arready=!rd_active&&!m_axi_rvalid&&tick%5!=0&&ar_stall==0;
assign m_axi_awready=!wr_active&&!response_pending&&!m_axi_bvalid&&tick%7!=0&&aw_stall==0;
assign m_axi_wready=wr_active&&tick%3!=0&&w_stall==0;
function automatic[7:0]initial_byte(input[31:0]a);begin initial_byte=a*37+(a>>7)+(a>>13)*251+13;end endfunction
function automatic[63:0]read_word(input[31:0]a);
 integer i;begin for(i=0;i<8;i=i+1)read_word[i*8+:8]=initial_byte(a+i);end
endfunction
function automatic[7:0]golden(input integer index);
 integer q,j,offset;begin
  q=index/cmd_row_bytes;offset=index%cmd_row_bytes;
  for(j=cmd_rank-1;j>=0;j=j-1)begin offset=offset+(q%cmd_dimensions[j*32+:32])*cmd_source_strides[j*32+:32];q=q/cmd_dimensions[j*32+:32];end
  golden=initial_byte(cmd_source+offset);
 end
endfunction
reg ar_held=0,aw_held=0,w_held=0;reg[47:0]ar_payload,aw_payload;reg[72:0]w_payload;
integer j,index,write_row,write_column;reg[31:0]address;
always@(posedge clk)begin
 tick<=tick+1;if(ar_stall>0)ar_stall<=ar_stall-1;if(aw_stall>0)aw_stall<=aw_stall-1;if(w_stall>0)w_stall<=w_stall-1;
 if(!rst_n)begin rd_active<=0;wr_active<=0;m_axi_rvalid<=0;m_axi_bvalid<=0;response_pending<=0;ar_held<=0;aw_held<=0;w_held<=0;end
 else begin
  if(ar_held&&(!m_axi_arvalid||{m_axi_araddr,m_axi_arid,m_axi_arlen,m_axi_arsize,m_axi_arburst}!==ar_payload))$fatal(1,"AXI_REORDER_FAIL stalled AR");
  if(aw_held&&(!m_axi_awvalid||{m_axi_awaddr,m_axi_awid,m_axi_awlen,m_axi_awsize,m_axi_awburst}!==aw_payload))$fatal(1,"AXI_REORDER_FAIL stalled AW");
  if(w_held&&(!m_axi_wvalid||{m_axi_wdata,m_axi_wstrb,m_axi_wlast}!==w_payload))$fatal(1,"AXI_REORDER_FAIL stalled W");
  ar_held<=m_axi_arvalid&&!m_axi_arready;aw_held<=m_axi_awvalid&&!m_axi_awready;w_held<=m_axi_wvalid&&!m_axi_wready;
  ar_payload<={m_axi_araddr,m_axi_arid,m_axi_arlen,m_axi_arsize,m_axi_arburst};aw_payload<={m_axi_awaddr,m_axi_awid,m_axi_awlen,m_axi_awsize,m_axi_awburst};w_payload<={m_axi_wdata,m_axi_wstrb,m_axi_wlast};
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_arvalid&&m_axi_arready)begin
   if(m_axi_araddr[2:0]||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||{1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"AXI_REORDER_FAIL AR geometry");
   rd_active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;read_bursts=read_bursts+1;
  end
  if(rd_active&&(!m_axi_rvalid||m_axi_rready)&&tick%4!=0)begin
   m_axi_rvalid<=1;m_axi_rdata<=read_word(read_address);m_axi_rid<=(fault==2&&produced==3)?1:0;
   m_axi_rresp<=(fault==1&&produced==3)?2:0;m_axi_rlast<=read_left==1;
   produced=produced+1;read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)rd_active<=0;
  end
  if(m_axi_awvalid&&m_axi_awready)begin
   if(m_axi_awaddr[2:0]||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||{1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"AXI_REORDER_FAIL AW geometry");
   wr_active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;write_bursts=write_bursts+1;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!wr_active||m_axi_wlast!==(write_left==1))$fatal(1,"AXI_REORDER_FAIL WLAST");
   for(j=0;j<8;j=j+1)if(m_axi_wstrb[j])begin
    address=write_address+j;index=address-cmd_destination;
    if(index<0||index>=expected_total||m_axi_wdata[j*8+:8]!==golden(index))$fatal(1,"AXI_REORDER_FAIL write guard %08x",address);
    memory[address]=m_axi_wdata[j*8+:8];
   end
   write_address<=write_address+8;write_left<=write_left-1;if(write_left==1)begin wr_active<=0;response_pending<=1;end
  end
  if(response_pending&&!m_axi_bvalid&&tick%4!=0)begin
   response_pending<=0;m_axi_bvalid<=1;m_axi_bresp<=(fault==3&&responses==0)?2:0;m_axi_bid<=(fault==4&&responses==0)?1:0;responses=responses+1;
  end
 end
end
task automatic transfer(input integer align_a,align_b,inject,cancel_mode,invalid_mode,input[7:0]want_error);
 integer waited,i,reads_before,writes_before;reg sent_cancel;reg[7:0]got,want;begin
 @(negedge clk);if(!cmd_ready)$fatal(1,"AXI_REORDER_FAIL not ready");
 memory.delete();fault=inject;produced=0;responses=0;reads_before=read_bursts;writes_before=write_bursts;
 cmd_source=32'h80000ff8+align_a;cmd_destination=32'h82000ff8+align_b;cmd_rank=rank_value;cmd_row_bytes=row_bytes_value;
 expected_total=row_bytes_value;
 for(i=0;i<4;i=i+1)begin cmd_dimensions[i*32+:32]=dims[i];cmd_source_strides[i*32+:32]=steps[i];if(i<rank_value)expected_total=expected_total*dims[i];end
 case(invalid_mode)
  1:cmd_rank=0;2:cmd_rank=5;3:cmd_row_bytes=0;4:cmd_dimensions[31:0]=0;
  5:cmd_dimensions[31:0]=32'hffffffff;6:cmd_source_strides[31:0]=32'h04000001;
  7:cmd_destination=cmd_source;8:cmd_source=32'h83fffffc;9:cmd_source=32'h7fffffff;
  10:cmd_destination=32'h83fffffe;11:begin cmd_dimensions[31:0]=65536;cmd_dimensions[63:32]=65536;end
 endcase
 if(cancel_mode==1)ar_stall=20;if(cancel_mode==2)aw_stall=2000;if(cancel_mode==3)w_stall=2000;
 cmd_valid=1;@(negedge clk);cmd_valid=0;waited=0;sent_cancel=0;
 while(!done)begin
  cancel=!sent_cancel&&((cancel_mode==1&&m_axi_arvalid)||(cancel_mode==2&&m_axi_awvalid)||(cancel_mode==3&&m_axi_wvalid)||(cancel_mode==4&&dut.state==10)||(cancel_mode==5&&dut.state==4));
  if(cancel)sent_cancel=1;
  @(negedge clk);waited=waited+1;if(waited>30000000)$fatal(1,"AXI_REORDER_FAIL timeout state=%0d reader=%0d writer=%0d err=%02x",dut.state,dut.reader.state,dut.writer.state,error);
 end
 cancel=0;ar_stall=0;aw_stall=0;w_stall=0;
 if(error!==want_error)$fatal(1,"AXI_REORDER_FAIL error=%02x expected=%02x command=%0d",error,want_error,commands);
 if(cancel_mode&&!sent_cancel)$fatal(1,"AXI_REORDER_FAIL cancel not exercised");
 if(rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid||dut.read_busy||dut.write_busy)$fatal(1,"AXI_REORDER_FAIL early completion");
 if(want_error==1&&(read_bursts!=reads_before||write_bursts!=writes_before))$fatal(1,"AXI_REORDER_FAIL invalid command issued AXI");
 if(!want_error)begin
  if(streamed_bytes!=expected_total)$fatal(1,"AXI_REORDER_FAIL byte counter got=%0d expected=%0d",streamed_bytes,expected_total);
  for(i=0;i<expected_total;i=i+1)begin
   want=golden(i);if(!memory.exists(cmd_destination+i)||memory[cmd_destination+i]!==want)$fatal(1,"AXI_REORDER_FAIL missing/wrong byte index=%0d",i);
   bytes_checked=bytes_checked+1;
  end
 end
 $display("REORDER_CASE node=%0d rank=%0d row_bytes=%0d bytes=%0d cycles=%0d error=%0d",node_value,cmd_rank,cmd_row_bytes,streamed_bytes,cycles,error);
 commands=commands+1;
end endtask
string vector_path;integer fd,rc,n,i,a,b,t,model_nodes=0,want_total;
initial begin
 if(!$value$plusargs("VECTORS=%s",vector_path))$fatal(1,"AXI_REORDER_FAIL argument");
 fd=$fopen(vector_path,"r");if(!fd)$fatal(1,"AXI_REORDER_FAIL file");
 repeat(4)@(negedge clk);rst_n=1;
 while(!$feof(fd))begin
  rc=$fscanf(fd,"%d %d %d %d %d %d %d %d %d %d %d %d\n",node_value,rank_value,row_bytes_value,dims[0],dims[1],dims[2],dims[3],steps[0],steps[1],steps[2],steps[3],want_total);
  if(rc!=12)$fatal(1,"AXI_REORDER_FAIL descriptor format");
  transfer(model_nodes%8,(model_nodes*3)%8,0,0,0,0);
  if(streamed_bytes!=want_total)$fatal(1,"AXI_REORDER_FAIL model byte count");model_nodes=model_nodes+1;
 end
 $fclose(fd);node_value=-1;rank_value=4;row_bytes_value=3;
 dims[0]=2;dims[1]=1;dims[2]=3;dims[3]=2;steps[0]=256;steps[1]=0;steps[2]=32;steps[3]=4;
 for(a=0;a<8;a=a+1)for(b=0;b<8;b=b+1)transfer(a,b,0,0,0,0);
 for(t=0;t<7;t=t+1)begin
  case(t)0:row_bytes_value=1;1:row_bytes_value=7;2:row_bytes_value=8;3:row_bytes_value=127;4:row_bytes_value=128;5:row_bytes_value=129;6:row_bytes_value=4097;endcase
  rank_value=2;dims[0]=3;dims[1]=5;steps[0]=row_bytes_value*7;steps[1]=0;transfer(3,7,0,0,0,0);
 end
 rank_value=4;row_bytes_value=17;dims[0]=2;dims[1]=1;dims[2]=3;dims[3]=2;steps[0]=1024;steps[1]=0;steps[2]=128;steps[3]=32;
 for(t=1;t<=11;t=t+1)transfer(0,0,0,0,t,1);
 transfer(3,7,1,0,0,8'h12);transfer(3,7,2,0,0,8'h13);transfer(3,7,3,0,0,8'h22);transfer(3,7,4,0,0,8'h23);
 for(t=1;t<=5;t=t+1)begin transfer(3,7,0,t,0,4);transfer(0,0,0,0,0,0);end
 if(model_nodes!=62)$fatal(1,"AXI_REORDER_FAIL missing model nodes");
 $display("AXI_REORDER_PASS model_nodes=%0d commands=%0d bytes=%0d read_bursts=%0d write_bursts=%0d cancellation_cases=5",model_nodes,commands,bytes_checked,read_bursts,write_bursts);$finish;
end
endmodule
