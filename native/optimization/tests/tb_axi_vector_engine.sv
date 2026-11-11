`timescale 1ns/1ps
`ifndef VECTOR_BANKS
`define VECTOR_BANKS 256
`endif
module tb_axi_vector_engine;
localparam BANKS=`VECTOR_BANKS;
wire shared_alu_valid,shared_alu_out_ready;wire[3:0]shared_alu_op;wire[31:0]shared_alu_a,shared_alu_b;
wire shared_alu_ready=0,shared_alu_out_valid=0;wire[31:0]shared_alu_result=0;wire[4:0]shared_alu_flags=0;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,cmd_valid=0,cancel=0;wire cmd_ready,busy,done;wire[7:0]error;wire[31:0]cycles,streamed_bytes;
reg[31:0]cmd_a=0,cmd_b=0,cmd_c=0,cmd_destination=0,cmd_elements=0,cmd_rows=0;
reg[31:0]cmd_a_stride=0,cmd_b_stride=0,cmd_c_stride=0,cmd_destination_stride=0;
reg[31:0]cmd_operation=0,cmd_flags=0,cmd_zero=0,cmd_scalar_b=0,cmd_scalar_c=0,cmd_scalar_d=0;
integer op_value=0,flags_value=0,n_value=0,rows_value=0,broadcast_mask=0,case_id=0;
reg[31:0]zero_value,scalar_b_value,scalar_c_value,scalar_d_value;
wire[BANKS*4-1:0]bank_addr;wire[BANKS-1:0]bank_ce,bank_we;wire[BANKS*8-1:0]bank_write,bank_read;
wire unused_valid;wire[4095:0]unused_data;
wire[31:0]m_axi_araddr,m_axi_awaddr;wire[2:0]m_axi_arid,m_axi_awid;
wire[7:0]m_axi_arlen,m_axi_awlen;wire[2:0]m_axi_arsize,m_axi_awsize;wire[1:0]m_axi_arburst,m_axi_awburst;
wire m_axi_arvalid,m_axi_arready,m_axi_rready,m_axi_awvalid,m_axi_awready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0,m_axi_bid=0;reg[1:0]m_axi_rresp=0,m_axi_bresp=0;
reg m_axi_rlast=0,m_axi_rvalid=0,m_axi_bvalid=0;
axi_vector_engine #(.BANKS(BANKS))dut(.*);
affine_array #(.ROWS(BANKS/32),.COLS(32),.LANES(4))u_array(
 .clk(clk),.rst_n(rst_n),.bank_addr(bank_addr),.bank_ce(bank_ce),.bank_we(bank_we),.bank_write(bank_write),.bank_read(bank_read),
 .tile_valid(1'b0),.a_raw({(BANKS/32*4*8){1'b0}}),.a_zero(8'b0),.b_zero(256'b0),.dot_valid(unused_valid),.dot_out(unused_data));
reg[31:0]input_bits[0:6143],scale_bits[0:6143],bias_bits[0:6143],golden[0:6143];
byte unsigned memory[bit[31:0]];
integer tick=0,ar_stall=0,aw_stall=0,w_stall=0;
reg rd_active=0,wr_active=0,response_pending=0;
reg[31:0]read_address=0,write_address=0;integer read_left=0,write_left=0,produced=0,responses=0;
integer fault=0,commands=0,read_bursts=0,write_bursts=0,values_checked=0;
assign m_axi_arready=!rd_active&&!m_axi_rvalid&&tick%5!=0&&ar_stall==0;
assign m_axi_awready=!wr_active&&!response_pending&&!m_axi_bvalid&&tick%7!=0&&aw_stall==0;
assign m_axi_wready=wr_active&&tick%3!=0&&w_stall==0;
function automatic[63:0]read_word(input[31:0]a);
 integer i;begin for(i=0;i<8;i=i+1)read_word[i*8+:8]=memory.exists(a+i)?memory[a+i]:8'h5a;end
endfunction
reg ar_held=0,aw_held=0,w_held=0;reg[47:0]ar_payload,aw_payload;reg[72:0]w_payload;
integer j,index,write_row,write_column;reg[31:0]address;
always@(posedge clk)begin
 tick<=tick+1;if(ar_stall>0)ar_stall<=ar_stall-1;if(aw_stall>0)aw_stall<=aw_stall-1;if(w_stall>0)w_stall<=w_stall-1;
 if(!rst_n)begin rd_active<=0;wr_active<=0;m_axi_rvalid<=0;m_axi_bvalid<=0;response_pending<=0;ar_held<=0;aw_held<=0;w_held<=0;end
 else begin
  if(ar_held&&(!m_axi_arvalid||{m_axi_araddr,m_axi_arid,m_axi_arlen,m_axi_arsize,m_axi_arburst}!==ar_payload))$fatal(1,"AXI_VECTOR_FAIL stalled AR");
  if(aw_held&&(!m_axi_awvalid||{m_axi_awaddr,m_axi_awid,m_axi_awlen,m_axi_awsize,m_axi_awburst}!==aw_payload))$fatal(1,"AXI_VECTOR_FAIL stalled AW");
  if(w_held&&(!m_axi_wvalid||{m_axi_wdata,m_axi_wstrb,m_axi_wlast}!==w_payload))$fatal(1,"AXI_VECTOR_FAIL stalled W");
  ar_held<=m_axi_arvalid&&!m_axi_arready;aw_held<=m_axi_awvalid&&!m_axi_awready;w_held<=m_axi_wvalid&&!m_axi_wready;
  ar_payload<={m_axi_araddr,m_axi_arid,m_axi_arlen,m_axi_arsize,m_axi_arburst};aw_payload<={m_axi_awaddr,m_axi_awid,m_axi_awlen,m_axi_awsize,m_axi_awburst};w_payload<={m_axi_wdata,m_axi_wstrb,m_axi_wlast};
  if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
  if(m_axi_arvalid&&m_axi_arready)begin
   if(m_axi_araddr[2:0]||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||{1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"AXI_VECTOR_FAIL AR geometry");
   rd_active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;read_bursts=read_bursts+1;
  end
  if(rd_active&&(!m_axi_rvalid||m_axi_rready)&&tick%4!=0)begin
   m_axi_rvalid<=1;m_axi_rdata<=read_word(read_address);m_axi_rid<=(fault==2&&produced==3)?1:0;
   m_axi_rresp<=((fault==1&&produced==3)||(fault==5&&read_address>=cmd_b&&read_address<cmd_b+(cmd_rows-1)*cmd_b_stride+cmd_elements*4)||(fault==6&&read_address>=cmd_c&&read_address<cmd_c+(cmd_rows-1)*cmd_c_stride+cmd_elements*4))?2:0;m_axi_rlast<=read_left==1;
   produced=produced+1;read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)rd_active<=0;
  end
  if(m_axi_awvalid&&m_axi_awready)begin
   if(m_axi_awaddr[2:0]||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||{1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"AXI_VECTOR_FAIL AW geometry");
   wr_active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;write_bursts=write_bursts+1;
  end
  if(m_axi_wvalid&&m_axi_wready)begin
   if(!wr_active||m_axi_wlast!==(write_left==1))$fatal(1,"AXI_VECTOR_FAIL WLAST");
   for(j=0;j<8;j=j+1)if(m_axi_wstrb[j])begin
    address=write_address+j;index=address-cmd_destination;write_row=index/cmd_destination_stride;write_column=index%cmd_destination_stride;
    if(index<0||write_row>=cmd_rows||write_column>=cmd_elements*4)$fatal(1,"AXI_VECTOR_FAIL write guard %08x",address);
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
 integer waited,r,i,k,jj,a_width;reg sent_cancel;reg[31:0]got,want;begin
 @(negedge clk);if(!cmd_ready)$fatal(1,"AXI_VECTOR_FAIL not ready");
 memory.delete();fault=inject;produced=0;responses=0;cmd_a=32'h80000ff8+align_a;cmd_b=32'h80100ffb;cmd_c=32'h80200ffd;cmd_destination=32'h80300ff8+align_b;
 cmd_elements=n_value;cmd_rows=rows_value;cmd_operation=op_value;cmd_flags=flags_value;cmd_zero=zero_value;
 cmd_scalar_b=scalar_b_value;cmd_scalar_c=scalar_c_value;cmd_scalar_d=scalar_d_value;a_width=(op_value==3||op_value==4)?1:4;
 cmd_a_stride=(broadcast_mask&1)?0:n_value*a_width+13;cmd_b_stride=(broadcast_mask&2)?0:n_value*4+11;cmd_c_stride=(broadcast_mask&4)?0:n_value*4+9;cmd_destination_stride=n_value*4+13;
 if(flags_value&1)cmd_b=0;if(op_value!=7||flags_value&2)cmd_c=0;
 for(r=0;r<rows_value;r=r+1)for(i=0;i<n_value;i=i+1)begin
  if(r==0||cmd_a_stride!=0)for(k=0;k<a_width;k=k+1)memory[cmd_a+r*cmd_a_stride+i*a_width+k]=input_bits[r*n_value+i][8*k+:8];
  if(cmd_b!=0&&(r==0||cmd_b_stride!=0))for(k=0;k<4;k=k+1)memory[cmd_b+r*cmd_b_stride+i*4+k]=scale_bits[r*n_value+i][8*k+:8];
  if(cmd_c!=0&&(r==0||cmd_c_stride!=0))for(k=0;k<4;k=k+1)memory[cmd_c+r*cmd_c_stride+i*4+k]=bias_bits[r*n_value+i][8*k+:8];
 end
 case(invalid_mode)
  1:cmd_operation=8;2:cmd_flags=4;3:cmd_elements=0;4:cmd_elements=65536;5:cmd_rows=0;
  6:cmd_a=32'h7fffffff;7:cmd_destination=cmd_a;8:cmd_destination=cmd_b;9:cmd_destination_stride=n_value*4-1;
  10:cmd_b=32'h83fffffe;11:begin cmd_operation=7;cmd_c=32'h83fffffe;end
  12:begin cmd_operation=5;cmd_zero=1;end
 endcase
 if(cancel_mode==1)ar_stall=20;if(cancel_mode==2)aw_stall=200000;if(cancel_mode==3)w_stall=200000;
 cmd_valid=1;@(negedge clk);cmd_valid=0;waited=0;sent_cancel=0;
 while(!done)begin
  cancel=!sent_cancel&&((cancel_mode==1&&m_axi_arvalid)||(cancel_mode==2&&m_axi_awvalid)||(cancel_mode==3&&m_axi_wvalid)||
    (cancel_mode==4&&dut.core.state==3&&dut.PRIVATE.u_alu.busy)||(cancel_mode==5&&dut.core_out_valid)||(cancel_mode==6&&dut.plane==1&&dut.read_valid));
  if(cancel)sent_cancel=1;
  @(negedge clk);waited=waited+1;if(waited>2000000)$fatal(1,"AXI_VECTOR_FAIL timeout state=%0d core=%0d reader=%0d writer=%0d err=%02x",dut.state,dut.core.state,dut.reader.state,dut.writer.state,error);
 end
 cancel=0;ar_stall=0;aw_stall=0;w_stall=0;
 if(error!==want_error)$fatal(1,"AXI_VECTOR_FAIL error=%02x expected=%02x command=%0d",error,want_error,commands);
 if(cancel_mode&&!sent_cancel)$fatal(1,"AXI_VECTOR_FAIL cancel not exercised");
 if(rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid||dut.read_busy||dut.write_busy||dut.core_busy)$fatal(1,"AXI_VECTOR_FAIL early completion");
 if(!want_error)begin
  if(streamed_bytes!=n_value*rows_value*4)$fatal(1,"AXI_VECTOR_FAIL byte counter");
  for(r=0;r<rows_value;r=r+1)for(i=0;i<n_value;i=i+1)begin
   got=read_word(cmd_destination+r*cmd_destination_stride+i*4);want=golden[r*n_value+i];
   if((want&32'h7fffffff)>32'h7f800000)begin if((got&32'h7fffffff)<=32'h7f800000||^got===1'bx)$fatal(1,"AXI_VECTOR_FAIL NaN");end
   else if(got!==want)$fatal(1,"AXI_VECTOR_FAIL case=%0d op=%0d row=%0d col=%0d got=%08x expected=%08x",case_id,op_value,r,i,got,want);
   values_checked=values_checked+1;
  end
 end
 commands=commands+1;
end endtask
string vector_path;integer fd,rc,i,a,b,t,reference_cases=0;
task automatic load_case;
 integer i;begin
 rc=$fscanf(fd,"%d %d %d %d %d %d %h %h %h %h\n",case_id,op_value,flags_value,n_value,rows_value,broadcast_mask,zero_value,scalar_b_value,scalar_c_value,scalar_d_value);
 if(rc!=10||n_value<1||n_value*rows_value>6144)$fatal(1,"AXI_VECTOR_FAIL format");
 for(i=0;i<n_value*rows_value;i=i+1)begin rc=$fscanf(fd,"%h %h %h %h\n",input_bits[i],scale_bits[i],bias_bits[i],golden[i]);if(rc!=4)$fatal(1,"AXI_VECTOR_FAIL data format");end
 end
endtask
initial begin
 if(!$value$plusargs("VECTORS=%s",vector_path))$fatal(1,"AXI_VECTOR_FAIL argument");
 fd=$fopen(vector_path,"r");if(!fd)$fatal(1,"AXI_VECTOR_FAIL file");
 repeat(4)@(negedge clk);rst_n=1;
 while(!$feof(fd))begin load_case();transfer(reference_cases%8,(reference_cases*3)%8,0,0,0,0);reference_cases=reference_cases+1;end
 $fclose(fd);if(op_value!=2||n_value!=17||flags_value!=0)$fatal(1,"AXI_VECTOR_FAIL missing fault base");
 for(a=0;a<8;a=a+1)for(b=0;b<8;b=b+1)transfer(a,b,0,0,0,0);
 for(t=1;t<=12;t=t+1)transfer(0,0,0,0,t,1);
 transfer(3,7,1,0,0,8'h12);transfer(3,7,2,0,0,8'h13);transfer(3,7,3,0,0,8'h22);transfer(3,7,4,0,0,8'h23);transfer(3,7,5,0,0,8'h12);
 // Bias-load faults require a postscale command. Reopen a flags=0 POST case.
 fd=$fopen(vector_path,"r");load_case();while(op_value!=7||flags_value!=0)load_case();$fclose(fd);transfer(3,7,6,0,0,8'h12);
 // Restore the finite divide case for divide-in-flight cancellation.
 fd=$fopen(vector_path,"r");while(!$feof(fd))load_case();$fclose(fd);
 for(t=1;t<=6;t=t+1)begin transfer(3,7,0,t,0,4);transfer(0,0,0,0,0,0);end
 $display("AXI_VECTOR_PASS banks=%0d reference_cases=%0d commands=%0d values=%0d read_bursts=%0d write_bursts=%0d cancellation_cases=6",BANKS,reference_cases,commands,values_checked,read_bursts,write_bursts);$finish;
end
endmodule
