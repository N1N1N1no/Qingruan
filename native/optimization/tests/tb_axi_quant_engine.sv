`timescale 1ns/1ps
module tb_axi_quant_engine;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;
wire cmd_ready,busy,done;wire[7:0]error;wire[31:0]cycles,streamed_bytes;
reg[31:0]cmd_source=0,cmd_destination=0,cmd_table=0,cmd_elements=0;
wire[31:0]m_axi_araddr,m_axi_awaddr;wire[2:0]m_axi_arid,m_axi_awid;
wire[7:0]m_axi_arlen,m_axi_awlen;wire[2:0]m_axi_arsize,m_axi_awsize;wire[1:0]m_axi_arburst,m_axi_awburst;
wire m_axi_arvalid,m_axi_arready,m_axi_rready,m_axi_awvalid,m_axi_awready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0,m_axi_bid=0;reg[1:0]m_axi_rresp=0,m_axi_bresp=0;
reg m_axi_rlast=0,m_axi_rvalid=0,m_axi_bvalid=0;
axi_quant_engine dut(.*);
localparam TABLE_BASE=32'h80200003;
reg[31:0]tables[0:45389];reg[31:0]input_bits[0:2047];reg[7:0]expected_codes[0:2047];
string table_path,vector_path;integer input_count=0,fd,rc,bad;
integer tick=0,ar_stall=0,aw_stall=0,w_stall=0;
reg rd_active=0,wr_active=0,response_pending=0;
reg[31:0]read_address=0,write_address=0;integer read_left=0,write_left=0,produced=0,responses=0;
integer fault=0,commands=0,read_bursts=0,write_bursts=0,bytes_checked=0;
byte unsigned memory[bit[31:0]];
assign m_axi_arready=!rd_active&&!m_axi_rvalid&&tick%5!=0&&ar_stall==0;
assign m_axi_awready=!wr_active&&!response_pending&&!m_axi_bvalid&&tick%7!=0&&aw_stall==0;
assign m_axi_wready=wr_active&&tick%3!=0&&w_stall==0;
function automatic[63:0]read_word(input[31:0]address);
 integer i,index;reg[31:0]a;begin for(i=0;i<8;i=i+1)begin
  a=address+i;
  if(a>=TABLE_BASE&&a<TABLE_BASE+181560)begin index=a-TABLE_BASE;read_word[i*8+:8]=tables[index>>2][(index&3)*8+:8];end
  else if(a>=cmd_source&&a<cmd_source+cmd_elements*4)begin index=a-cmd_source;read_word[i*8+:8]=input_bits[index>>2][(index&3)*8+:8];end
  else read_word[i*8+:8]=memory.exists(a)?memory[a]:8'h5a;
 end end
endfunction
integer j,index;reg[31:0]address;
always@(posedge clk)begin
 tick<=tick+1;if(ar_stall>0)ar_stall<=ar_stall-1;if(aw_stall>0)aw_stall<=aw_stall-1;if(w_stall>0)w_stall<=w_stall-1;
 if(!rst_n)begin rd_active<=0;wr_active<=0;m_axi_rvalid<=0;m_axi_bvalid<=0;response_pending<=0;end
 else begin
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_arvalid&&m_axi_arready)begin
   if(m_axi_araddr[2:0]||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||{1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"AXI_QUANT_FAIL AR geometry");
   rd_active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;read_bursts=read_bursts+1;
  end
  if(rd_active&&(!m_axi_rvalid||m_axi_rready)&&tick%4!=0)begin
   m_axi_rvalid<=1;m_axi_rdata<=read_word(read_address);m_axi_rid<=(fault==2&&produced==3)?1:0;
   m_axi_rresp<=((fault==1&&produced==3)||(fault==7&&read_address>=cmd_source&&read_address<cmd_source+cmd_elements*4))?2:0;
   m_axi_rlast<=read_left==1;produced=produced+1;read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)rd_active<=0;
  end
  if(m_axi_awvalid&&m_axi_awready)begin
   if(m_axi_awaddr[2:0]||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||{1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"AXI_QUANT_FAIL AW geometry");
   wr_active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;write_bursts=write_bursts+1;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!wr_active||m_axi_wlast!==(write_left==1))$fatal(1,"AXI_QUANT_FAIL WLAST");
   for(j=0;j<8;j=j+1)if(m_axi_wstrb[j])begin
    address=write_address+j;index=address-cmd_destination;
    if(index<0||index>=cmd_elements||m_axi_wdata[j*8+:8]!==expected_codes[index])$fatal(1,"AXI_QUANT_FAIL byte index=%0d actual=%02x expected=%02x",index,m_axi_wdata[j*8+:8],expected_codes[index]);
    memory[address]=m_axi_wdata[j*8+:8];bytes_checked=bytes_checked+1;
   end
   write_address<=write_address+8;write_left<=write_left-1;if(write_left==1)begin wr_active<=0;response_pending<=1;end
  end
  if(response_pending&&!m_axi_bvalid&&tick%4!=0)begin
   response_pending<=0;m_axi_bvalid<=1;m_axi_bresp<=(fault==3&&responses==0)?2:0;m_axi_bid<=(fault==4&&responses==0)?1:0;responses=responses+1;
  end
 end
end
task automatic load_vectors;
 integer i;begin
  rc=$fscanf(fd,"%d\n",input_count);if(rc!=1||input_count>2048)$fatal(1,"AXI_QUANT_FAIL vector count");
  for(i=0;i<input_count;i=i+1)begin
   rc=$fscanf(fd,"%h %h %h\n",input_bits[i],expected_codes[i],bad);
   if(rc!=3||bad!=(i==input_count-1))$fatal(1,"AXI_QUANT_FAIL vector format");
  end
 end
endtask
task automatic transfer(input integer table_index,n,align_a,align_b,inject,cancel_mode,invalid_mode,input[7:0]want_error);
 integer waited,bb;reg sent_cancel;begin
  @(negedge clk);if(!cmd_ready)$fatal(1,"AXI_QUANT_FAIL not ready");
  memory.delete();fault=inject;produced=0;responses=0;cmd_source=32'h80000ff8+align_a;cmd_destination=32'h80100ff8+align_b;
  cmd_table=TABLE_BASE+table_index*1020;cmd_elements=n;cmd_valid=1;
  case(invalid_mode)1:cmd_source=32'h7fffffff;2:cmd_destination=cmd_source;3:cmd_table=32'h83ffff00;4:cmd_source=32'h83fffffc;endcase
  if(cancel_mode==1)ar_stall=20;if(cancel_mode==2)aw_stall=2000;if(cancel_mode==3)w_stall=2000;
  @(negedge clk);cmd_valid=0;waited=0;sent_cancel=0;
  while(!done)begin
   cancel=!sent_cancel&&((cancel_mode==1&&m_axi_arvalid)||(cancel_mode==2&&m_axi_awvalid)||(cancel_mode==3&&m_axi_wvalid)||(cancel_mode==4&&dut.quant_out_valid));
   if(cancel)sent_cancel=1;
   @(negedge clk);waited=waited+1;if(waited>100000)$fatal(1,"AXI_QUANT_FAIL timeout state=%0d words=%0d cfg=%0d",dut.state,dut.word_count,dut.table_index);
  end
  cancel=0;
  if(error!==want_error)$fatal(1,"AXI_QUANT_FAIL error=%02x expected=%02x",error,want_error);
  if(rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid||dut.read_busy||dut.write_busy)$fatal(1,"AXI_QUANT_FAIL early completion");
  if(!want_error)begin
   if(streamed_bytes!=n)$fatal(1,"AXI_QUANT_FAIL byte counter");
   for(bb=0;bb<n;bb=bb+1)if(!memory.exists(cmd_destination+bb)||memory[cmd_destination+bb]!==expected_codes[bb])$fatal(1,"AXI_QUANT_FAIL missing output");
  end
  commands=commands+1;
 end
endtask
integer q,a,b,t,n;
initial begin
 if(!$value$plusargs("TABLES=%s",table_path)||!$value$plusargs("VECTORS=%s",vector_path))$fatal(1,"AXI_QUANT_FAIL arguments");
 $readmemh(table_path,tables);fd=$fopen(vector_path,"r");if(!fd)$fatal(1,"AXI_QUANT_FAIL vectors file");
 repeat(4)@(negedge clk);rst_n=1;
 for(q=0;q<178;q=q+1)begin load_vectors();transfer(q,input_count-1,q%8,(q*3)%8,0,0,0,0);end
 $fclose(fd);fd=$fopen(vector_path,"r");load_vectors();$fclose(fd);
 for(a=0;a<8;a=a+1)for(b=0;b<8;b=b+1)for(t=0;t<11;t=t+1)begin
  case(t)0:n=1;1:n=2;2:n=3;3:n=7;4:n=8;5:n=9;6:n=31;7:n=127;8:n=128;9:n=129;10:n=1025;endcase
  transfer(0,n,a,b,0,0,0,0);
 end
 transfer(0,0,0,0,0,0,0,1);
 for(t=1;t<=4;t=t+1)transfer(0,33,0,0,0,0,t,1);
 transfer(0,33,3,7,1,0,0,8'h12);transfer(0,33,3,7,2,0,0,8'h13);transfer(0,33,3,7,7,0,0,8'h12);
 transfer(0,33,3,7,3,0,0,8'h22);transfer(0,33,3,7,4,0,0,8'h23);
 transfer(0,input_count,3,7,0,0,0,8'h03);
 for(t=1;t<=4;t=t+1)transfer(0,33,3,7,0,t,0,8'h04);
 transfer(0,input_count-1,3,7,0,0,0,0);
 $display("AXI_QUANT_PASS tables=178 commands=%0d bytes=%0d read_bursts=%0d write_bursts=%0d",commands,bytes_checked,read_bursts,write_bursts);$finish;
end
endmodule
