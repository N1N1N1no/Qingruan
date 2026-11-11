`timescale 1ns/1ps
`ifndef ARRAY_ROWS
`define ARRAY_ROWS 8
`define ARRAY_COLS 32
`define ARRAY_LANES 4
`endif
module tb_axi_gelu_engine;
localparam ROWS=`ARRAY_ROWS,COLS=`ARRAY_COLS,LANES=`ARRAY_LANES,BANKS=ROWS*COLS;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;
wire cmd_ready,busy,done;wire[7:0]error;wire[31:0]cycles,streamed_bytes;
reg[31:0]cmd_source=0,cmd_destination=0,cmd_table=0,cmd_channels=0,cmd_rows=0;
wire[BANKS*4-1:0]bank_addr;wire[BANKS-1:0]bank_ce,bank_we;wire[BANKS*8-1:0]bank_write,bank_read;
wire[31:0]m_axi_araddr,m_axi_awaddr;wire[2:0]m_axi_arid,m_axi_awid;
wire[7:0]m_axi_arlen,m_axi_awlen;wire[2:0]m_axi_arsize,m_axi_awsize;wire[1:0]m_axi_arburst,m_axi_awburst;
wire m_axi_arvalid,m_axi_arready,m_axi_rready,m_axi_awvalid,m_axi_awready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0,m_axi_bid=0;reg[1:0]m_axi_rresp=0,m_axi_bresp=0;
reg m_axi_rlast=0,m_axi_rvalid=0,m_axi_bvalid=0;
axi_gelu_engine #(.BANKS(BANKS))dut(.*);
wire unused_valid;wire[COLS*LANES*32-1:0]unused_dot;
affine_array #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES))shared_pe(
 .clk(clk),.rst_n(rst_n),.bank_addr(bank_addr),.bank_ce(bank_ce),.bank_we(bank_we),.bank_write(bank_write),.bank_read(bank_read),
 .tile_valid(1'b0),.a_raw({ROWS*LANES*8{1'b0}}),.a_zero(8'b0),.b_zero({COLS*8{1'b0}}),.dot_valid(unused_valid),.dot_out(unused_dot));
localparam TABLE_BASE=32'h80200003;
reg[7:0]tables[0:4521983];string table_path;
integer tick=0,ar_stall=0,aw_stall=0,w_stall=0;
reg rd_active=0,wr_active=0,response_pending=0;
reg[31:0]read_address=0,write_address=0;integer read_left=0,write_left=0,produced=0,responses=0;
integer fault=0,commands=0,read_bursts=0,write_bursts=0,bytes_checked=0,table_offset=0;
byte unsigned memory[bit[31:0]];
assign m_axi_arready=!rd_active&&!m_axi_rvalid&&tick%5!=0&&ar_stall==0;
assign m_axi_awready=!wr_active&&!response_pending&&!m_axi_bvalid&&tick%7!=0&&aw_stall==0;
assign m_axi_wready=wr_active&&tick%3!=0&&w_stall==0;
function automatic[7:0]initial_byte(input[31:0]address);begin initial_byte=address*37+(address>>7)+13;end endfunction
function automatic[63:0]read_word(input[31:0]address);
 integer i;begin for(i=0;i<8;i=i+1)begin
  if(address+i>=TABLE_BASE&&address+i<TABLE_BASE+4521984)read_word[i*8+:8]=tables[address+i-TABLE_BASE];
  else read_word[i*8+:8]=memory.exists(address+i)?memory[address+i]:initial_byte(address+i);
 end end
endfunction
integer j,index,raw;reg[31:0]address,source_address;
always@(posedge clk)begin
 tick<=tick+1;if(ar_stall>0)ar_stall<=ar_stall-1;if(aw_stall>0)aw_stall<=aw_stall-1;if(w_stall>0)w_stall<=w_stall-1;
 if(!rst_n)begin rd_active<=0;wr_active<=0;m_axi_rvalid<=0;m_axi_bvalid<=0;response_pending<=0;end
 else begin
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;
  if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_arvalid&&m_axi_arready)begin
   if(m_axi_araddr[2:0]!=0||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||{1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"AXI_GELU_FAIL AR geometry");
   rd_active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;read_bursts=read_bursts+1;
  end
  if(rd_active&&(!m_axi_rvalid||m_axi_rready)&&tick%4!=0)begin
   m_axi_rvalid<=1;m_axi_rdata<=read_word(read_address);m_axi_rid<=(fault==2&&produced==3)?1:0;
   m_axi_rresp<=((fault==1&&produced==3)||(fault==7&&read_address>=cmd_source&&read_address<cmd_source+cmd_rows*cmd_channels))?2:0;m_axi_rlast<=read_left==1;
   produced=produced+1;read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)rd_active<=0;
  end
  if(m_axi_awvalid&&m_axi_awready)begin
   if(m_axi_awaddr[2:0]!=0||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||{1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"AXI_GELU_FAIL AW geometry");
   wr_active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;write_bursts=write_bursts+1;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!wr_active||m_axi_wlast!==(write_left==1))$fatal(1,"AXI_GELU_FAIL WLAST");
   for(j=0;j<8;j=j+1)if(m_axi_wstrb[j])begin
    address=write_address+j;index=address-cmd_destination;source_address=cmd_source+index;raw=initial_byte(source_address);
    if(index<0||index>=cmd_channels*cmd_rows||m_axi_wdata[j*8+:8]!==tables[table_offset+(index%cmd_channels)*256+raw])$fatal(1,"AXI_GELU_FAIL byte addr=%08x actual=%02x expected=%02x",address,m_axi_wdata[j*8+:8],tables[table_offset+(index%cmd_channels)*256+raw]);
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
task automatic transfer(input[31:0]src,dst,input integer channels,rows,offset,inject,cancel_mode,input[7:0]want_error);
 integer waited,bb;reg[31:0]addr;reg sent_cancel;
 begin
  @(negedge clk);if(!cmd_ready)$fatal(1,"AXI_GELU_FAIL not ready");
  memory.delete();table_offset=offset;fault=inject;produced=0;responses=0;
  cmd_source=src;cmd_destination=dst;cmd_channels=channels;cmd_rows=rows;cmd_table=TABLE_BASE+offset;cmd_valid=1;
  if(cancel_mode==1)ar_stall=20;
  if(cancel_mode==2)aw_stall=2000;
  if(cancel_mode==3)w_stall=2000;
  @(negedge clk);cmd_valid=0;waited=0;sent_cancel=0;
  while(!done)begin
   cancel=!sent_cancel&&((cancel_mode==1&&m_axi_arvalid)||(cancel_mode==2&&m_axi_awvalid)||(cancel_mode==3&&m_axi_wvalid));
   if(cancel)sent_cancel=1;
   @(negedge clk);waited=waited+1;if(waited>1000000)$fatal(1,"AXI_GELU_FAIL timeout state=%0d",dut.state);
  end
  cancel=0;
  if(error!==want_error)$fatal(1,"AXI_GELU_FAIL error=%02x expected=%02x",error,want_error);
  if(rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid||outstanding())$fatal(1,"AXI_GELU_FAIL premature completion");
  if(!want_error)begin
   if(streamed_bytes!=channels*rows)$fatal(1,"AXI_GELU_FAIL byte counter");
   for(bb=0;bb<channels*rows;bb=bb+1)begin
    addr=dst+bb;raw=initial_byte(src+bb);if(!memory.exists(addr)||memory[addr]!==tables[offset+(bb%channels)*256+raw])$fatal(1,"AXI_GELU_FAIL missing destination byte");
   end
  end
  commands=commands+1;
 end
endtask
function automatic outstanding;begin outstanding=dut.read_busy||dut.write_busy;end endfunction
integer align_a,align_b,f,channels,offset;
initial begin
 if(!$value$plusargs("TABLES=%s",table_path))$fatal(1,"AXI_GELU_FAIL missing tables");
 $readmemh(table_path,tables);repeat(4)@(negedge clk);rst_n=1;
 for(align_a=0;align_a<8;align_a=align_a+1)for(align_b=0;align_b<8;align_b=align_b+1)
  transfer(32'h80000ff8+align_a,32'h80100ff8+align_b,16*(1+(align_a+align_b)%4),1+(align_a*3+align_b)%17,0,0,0,0);
 offset=0;
 for(f=0;f<12;f=f+1)begin
  case(f)0,1:channels=384;2,3:channels=768;10,11:channels=3072;default:channels=1536;endcase
  transfer(32'h80004003,32'h80104007,channels,3,offset,0,0,0);offset=offset+channels*256;
 end
 if(offset!=4521984)$fatal(1,"AXI_GELU_FAIL table coverage");
 transfer(32'h7fffffff,32'h80100000,16,1,0,0,0,1);
 transfer(32'h80000000,32'h7fffffff,16,1,0,0,0,1);
 transfer(32'h80000000,32'h80100000,0,1,0,0,0,1);
 transfer(32'h80000000,32'h80100000,17,1,0,0,0,1);
 transfer(32'h80000000,32'h80100000,16,0,0,0,0,1);
 transfer(32'h80000000,32'h80000000,16,1,0,0,0,1);
 transfer(32'h80000000,TABLE_BASE,16,1,0,0,0,1);
 transfer(32'h83fffff1,32'h80100000,16,1,0,0,0,1);
 transfer(32'h80004003,32'h80104007,32,3,0,1,0,8'h12);
 transfer(32'h80004003,32'h80104007,32,3,0,2,0,8'h13);
 transfer(32'h80004003,32'h80104007,32,3,0,7,0,8'h12);
 transfer(32'h80004003,32'h80104007,32,3,0,3,0,8'h22);
 transfer(32'h80004003,32'h80104007,32,3,0,4,0,8'h23);
 transfer(32'h80004003,32'h80104007,32,3,0,0,1,8'h04);
 transfer(32'h80004003,32'h80104007,32,3,0,0,2,8'h04);
 transfer(32'h80004003,32'h80104007,32,3,0,0,3,8'h04);
 transfer(32'h80004003,32'h80104007,32,3,0,0,0,0);
 $display("AXI_GELU_PASS commands=%0d bytes=%0d read_bursts=%0d write_bursts=%0d cached_table_bytes=%0d",commands,bytes_checked,read_bursts,write_bursts,offset);$finish;
end
endmodule
