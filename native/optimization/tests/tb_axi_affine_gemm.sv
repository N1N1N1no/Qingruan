`timescale 1ns/1ps
`ifndef ARRAY_ROWS
`define ARRAY_ROWS 8
`define ARRAY_COLS 32
`define ARRAY_LANES 4
`endif
module tb_axi_affine_gemm;
localparam ROWS=`ARRAY_ROWS,COLS=`ARRAY_COLS,LANES=`ARRAY_LANES,BANKS=ROWS*COLS;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;
wire cmd_ready,busy,done;wire[7:0]error;wire[31:0]cycles,output_bytes;
reg[31:0]cmd_a=0,cmd_b=0,cmd_c=0,cmd_m=0,cmd_n=0,cmd_k=0;
reg cmd_trans_b=0;reg[7:0]cmd_a_zero=0,cmd_b_zero=0;
wire[BANKS*4-1:0]bank_addr;wire[BANKS-1:0]bank_ce,bank_we;wire[BANKS*8-1:0]bank_write,bank_read;
wire tile_valid,dot_valid;wire[ROWS*LANES*8-1:0]a_raw;wire[7:0]a_zero;wire[COLS*8-1:0]b_zero;wire[COLS*LANES*32-1:0]dot_out;
wire[31:0]m_axi_araddr,m_axi_awaddr;wire[2:0]m_axi_arid,m_axi_awid;
wire[7:0]m_axi_arlen,m_axi_awlen;wire[2:0]m_axi_arsize,m_axi_awsize;wire[1:0]m_axi_arburst,m_axi_awburst;
wire m_axi_arvalid,m_axi_arready,m_axi_rready,m_axi_awvalid,m_axi_awready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0,m_axi_bid=0;reg[1:0]m_axi_rresp=0,m_axi_bresp=0;
reg m_axi_rlast=0,m_axi_rvalid=0,m_axi_bvalid=0;
axi_affine_gemm #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES))dut(.*);
affine_array #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES))array_core(.*);
integer tick=0,ar_stall=0,aw_stall=0,w_stall=0;
reg rd_active=0,wr_active=0,response_pending=0;
reg[31:0]read_address=0,write_address=0;integer read_left=0,write_left=0,produced=0,responses=0,write_k_end=0;
integer fault=0,commands=0,read_bursts=0,write_bursts=0,bytes_checked=0,outputs_checked=0,compute_tiles=0;
byte unsigned memory[bit[31:0]];
assign m_axi_arready=!rd_active&&!m_axi_rvalid&&tick%5!=0&&ar_stall==0;
assign m_axi_awready=!wr_active&&!response_pending&&!m_axi_bvalid&&tick%7!=0&&aw_stall==0;
assign m_axi_wready=wr_active&&tick%3!=0&&w_stall==0;
function automatic[7:0]initial_byte(input[31:0]address);begin initial_byte=address*37+(address>>7)+13;end endfunction
function automatic[63:0]read_word(input[31:0]address);
 integer i;begin for(i=0;i<8;i=i+1)read_word[i*8+:8]=memory.exists(address+i)?memory[address+i]:initial_byte(address+i);end
endfunction
function automatic[31:0]golden(input integer row,col,end_k);
 integer z,a,b;reg signed[63:0]sum;begin
  sum=0;
  for(z=0;z<end_k;z=z+1)begin
   a=$signed(initial_byte(cmd_a+row*cmd_k+z));
   b=$signed(initial_byte(cmd_b+(cmd_trans_b?col*cmd_k+z:z*cmd_n+col)));
   sum=sum+(a-$signed(cmd_a_zero))*(b-$signed(cmd_b_zero));
  end
  if(sum>64'sd2147483647||sum< -64'sd2147483648)$fatal(1,"AFFINE_GEMM_FAIL golden overflow sum=%0d",sum);
  golden=sum[31:0];
 end
endfunction
integer j,index,byte_index;reg[31:0]address,expected;
always@(posedge clk)begin
 tick<=tick+1;if(ar_stall>0)ar_stall<=ar_stall-1;if(aw_stall>0)aw_stall<=aw_stall-1;if(w_stall>0)w_stall<=w_stall-1;
 if(tile_valid)compute_tiles=compute_tiles+1;
 if(!rst_n)begin rd_active<=0;wr_active<=0;m_axi_rvalid<=0;m_axi_bvalid<=0;response_pending<=0;end
 else begin
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;
  if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_arvalid&&m_axi_arready)begin
   if(m_axi_araddr[2:0]!=0||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||{1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"AFFINE_GEMM_FAIL AR geometry");
   rd_active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;read_bursts=read_bursts+1;
  end
  if(rd_active&&(!m_axi_rvalid||m_axi_rready)&&tick%4!=0)begin
   m_axi_rvalid<=1;m_axi_rdata<=read_word(read_address);m_axi_rid<=(fault==2&&produced==3)?1:0;
   m_axi_rresp<=((fault==1&&produced==3)||(fault==7&&read_address>=cmd_a&&read_address<cmd_a+cmd_m*cmd_k)||(fault==8&&read_address>=cmd_c&&read_address<cmd_c+cmd_m*cmd_n*4))?2:0;
   m_axi_rlast<=read_left==1;produced=produced+1;read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)rd_active<=0;
  end
  if(m_axi_awvalid&&m_axi_awready)begin
   if(m_axi_awaddr[2:0]!=0||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||{1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"AFFINE_GEMM_FAIL AW geometry");
   wr_active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;write_bursts=write_bursts+1;write_k_end=dut.k_block+dut.active_k;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!wr_active||m_axi_wlast!==(write_left==1))$fatal(1,"AFFINE_GEMM_FAIL WLAST");
   for(j=0;j<8;j=j+1)if(m_axi_wstrb[j])begin
    address=write_address+j;index=(address-cmd_c)>>2;byte_index=(address-cmd_c)&3;
    expected=golden(index/cmd_n,index%cmd_n,write_k_end);
    if(address<cmd_c||address>=cmd_c+cmd_m*cmd_n*4||m_axi_wdata[j*8+:8]!==expected[byte_index*8+:8])$fatal(1,"AFFINE_GEMM_FAIL byte addr=%08x k=%0d actual=%02x expected=%02x",address,write_k_end,m_axi_wdata[j*8+:8],expected[byte_index*8+:8]);
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
task automatic transfer(input integer mm,nn,kk,trans,az,bz,align_a,align_b,align_c,inject,cancel_mode,input[7:0]want_error);
 integer waited,bb;reg[31:0]addr,want,actual;reg sent_cancel;
 begin
  @(negedge clk);if(!cmd_ready)$fatal(1,"AFFINE_GEMM_FAIL not ready");
  memory.delete();fault=inject;produced=0;responses=0;
  cmd_a=32'h80000ff8+align_a;cmd_b=32'h80100ff8+align_b;cmd_c=32'h80300ff8+align_c;
  cmd_m=mm;cmd_n=nn;cmd_k=kk;cmd_trans_b=trans;cmd_a_zero=az;cmd_b_zero=bz;cmd_valid=1;
  if(cancel_mode==1)ar_stall=40;
  if(cancel_mode==2)aw_stall=2000;
  if(cancel_mode==3)w_stall=2000;
  @(negedge clk);cmd_valid=0;waited=0;sent_cancel=0;
  while(!done)begin
   cancel=!sent_cancel&&((cancel_mode==1&&m_axi_arvalid)||(cancel_mode==2&&m_axi_awvalid)||(cancel_mode==3&&m_axi_wvalid)||(cancel_mode==4&&tile_valid));
   if(cancel)sent_cancel=1;
   @(negedge clk);waited=waited+1;if(waited>1000000)$fatal(1,"AFFINE_GEMM_FAIL timeout state=%0d",dut.state);
  end
  cancel=0;
  if(error!==want_error)$fatal(1,"AFFINE_GEMM_FAIL error=%02x expected=%02x",error,want_error);
  if(rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid||dut.read_busy||dut.write_busy)$fatal(1,"AFFINE_GEMM_FAIL premature completion");
  if(!want_error)begin
   if(output_bytes!=mm*nn*4*((kk+ROWS*16-1)/(ROWS*16)))$fatal(1,"AFFINE_GEMM_FAIL byte counter");
   for(bb=0;bb<mm*nn;bb=bb+1)begin
    addr=cmd_c+bb*4;want=golden(bb/nn,bb%nn,kk);
    if(!memory.exists(addr)||!memory.exists(addr+1)||!memory.exists(addr+2)||!memory.exists(addr+3))$fatal(1,"AFFINE_GEMM_FAIL missing destination word");
    actual={memory[addr+3],memory[addr+2],memory[addr+1],memory[addr]};
    if(actual!==want)$fatal(1,"AFFINE_GEMM_FAIL final word=%0d actual=%08x expected=%08x",bb,actual,want);
    outputs_checked=outputs_checked+1;
   end
  end
  commands=commands+1;
 end
endtask
integer a,b,t,kcase,kk;
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 for(a=0;a<8;a=a+1)for(b=0;b<8;b=b+1)
  transfer(1+(a+b)%7,1+(a*7+b)%39,1+(a*19+b*13)%259,(a+b)%2,a%3==0?-128:a%3==1?0:127,b%3==0?127:b%3==1?0:-128,a,b,(a+3*b)%8,0,0,0);
 for(t=0;t<2;t=t+1)for(kcase=0;kcase<10;kcase=kcase+1)begin
  case(kcase)0:kk=1;1:kk=7;2:kk=8;3:kk=9;4:kk=127;5:kk=128;6:kk=129;7:kk=255;8:kk=257;9:kk=3072;endcase
  transfer(kcase==9?1:5,kcase==9?3:33,kk,t,-128,127,3,5,7,0,0,0);
 end
 transfer(0,3,7,0,0,0,0,0,0,0,0,1);
 transfer(3,0,7,0,0,0,0,0,0,0,0,1);
 transfer(3,3,0,0,0,0,0,0,0,0,0,1);
 transfer(3,3,32768,0,0,0,0,0,0,0,0,1);
 transfer(3,33,129,0,0,0,3,5,7,1,0,8'h12);
 transfer(3,33,129,0,0,0,3,5,7,2,0,8'h13);
 transfer(3,33,129,0,0,0,3,5,7,7,0,8'h12);
 transfer(3,33,ROWS*16+1,0,0,0,3,5,7,8,0,8'h12);
 transfer(3,33,129,0,0,0,3,5,7,3,0,8'h22);
 transfer(3,33,129,0,0,0,3,5,7,4,0,8'h23);
 transfer(3,33,129,0,0,0,3,5,7,0,1,8'h04);
 transfer(3,33,129,0,0,0,3,5,7,0,2,8'h04);
 transfer(3,33,129,0,0,0,3,5,7,0,3,8'h04);
 transfer(3,33,129,0,0,0,3,5,7,0,4,8'h04);
 transfer(5,37,257,1,8'h80,8'h7f,3,5,7,0,0,0);
 $display("AFFINE_GEMM_PASS commands=%0d outputs=%0d bytes=%0d read_bursts=%0d write_bursts=%0d compute_tiles=%0d",commands,outputs_checked,bytes_checked,read_bursts,write_bursts,compute_tiles);$finish;
end
endmodule
