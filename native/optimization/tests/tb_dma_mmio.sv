`timescale 1ns/1ps
module tb_dma_mmio;
reg clk=0;always #5 clk=~clk;reg rst_n=0;
reg[31:0]s_axi_awaddr=0,s_axi_araddr=0;reg[2:0]s_axi_awid=0,s_axi_arid=0;
reg[7:0]s_axi_awlen=0,s_axi_arlen=0;reg[2:0]s_axi_awsize=2,s_axi_arsize=2;
reg[1:0]s_axi_awburst=1,s_axi_arburst=1;reg s_axi_awvalid=0,s_axi_arvalid=0;
wire s_axi_awready,s_axi_arready,s_axi_wready,s_axi_bvalid,s_axi_rlast,s_axi_rvalid;
reg[63:0]s_axi_wdata=0;reg[7:0]s_axi_wstrb=8'h0f;reg s_axi_wlast=1,s_axi_wvalid=0,s_axi_bready=0,s_axi_rready=0;
wire[2:0]s_axi_bid,s_axi_rid;wire[1:0]s_axi_bresp,s_axi_rresp;wire[63:0]s_axi_rdata;
wire request_valid;reg request_ready=0;wire[7:0]request_opcode;wire[511:0]request_arguments;wire cancel;
reg engine_busy=0,engine_done=0;reg[7:0]engine_error=0;reg[31:0]engine_cycles=0,engine_bytes=0;
axi_accel_mmio dut(.*);
integer ticks=0,started=0,left=0;reg allow_engine=0;
reg[511:0]captured;
always@(posedge clk)begin
 ticks<=ticks+1;request_ready<=allow_engine&&!engine_busy&&ticks%5!=0;engine_done<=0;
 if(request_valid&&request_ready)begin
  if(request_opcode!=1)$fatal(1,"MMIO_FAIL unsupported backend command");
  captured<=request_arguments;engine_busy<=1;engine_error<=0;engine_cycles<=0;engine_bytes<=0;left<=80;started=started+1;
 end
 if(engine_busy)begin
  if(request_arguments!==captured)$fatal(1,"MMIO_FAIL changed inflight arguments");
  engine_cycles<=engine_cycles+1;engine_bytes<=engine_bytes+8;
  if(cancel)begin engine_error<=4;left<=2;end else left<=left-1;
  if(left==1)begin engine_busy<=0;engine_done<=1;end
 end
 if(ticks>10000)$fatal(1,"MMIO_FAIL timeout");
end
reg b_stall=0,r_stall=0;reg[2:0]saved_bid,saved_rid;reg[1:0]saved_bresp,saved_rresp;reg[63:0]saved_rdata;reg saved_last;
always@(posedge clk)if(rst_n)begin
 if(b_stall&&(!s_axi_bvalid||s_axi_bid!==saved_bid||s_axi_bresp!==saved_bresp))$fatal(1,"MMIO_FAIL B stability");
 if(r_stall&&(!s_axi_rvalid||s_axi_rdata!==saved_rdata||s_axi_rid!==saved_rid||s_axi_rresp!==saved_rresp||s_axi_rlast!==saved_last))$fatal(1,"MMIO_FAIL R stability");
 b_stall=s_axi_bvalid&&!s_axi_bready;saved_bid=s_axi_bid;saved_bresp=s_axi_bresp;
 r_stall=s_axi_rvalid&&!s_axi_rready;saved_rdata=s_axi_rdata;saved_rid=s_axi_rid;saved_rresp=s_axi_rresp;saved_last=s_axi_rlast;
end
task automatic send_aw(input[31:0]address,input integer len,size,id,delay_cycles);
 begin
  repeat(delay_cycles)@(negedge clk);s_axi_awaddr=address;s_axi_awlen=len;s_axi_awsize=size;s_axi_awid=id;s_axi_awvalid=1;
  @(posedge clk);while(!s_axi_awready)@(posedge clk);@(negedge clk);s_axi_awvalid=0;
 end
endtask
task automatic send_w(input[63:0]data,input[7:0]strb,input integer len,delay_cycles);
 integer i;begin
  repeat(delay_cycles)@(negedge clk);
  for(i=0;i<=len;i=i+1)begin
   s_axi_wdata=data+i;s_axi_wstrb=strb;s_axi_wlast=i==len;s_axi_wvalid=1;
   @(posedge clk);while(!s_axi_wready)@(posedge clk);@(negedge clk);s_axi_wvalid=0;
  end
 end
endtask
integer transactions=0;
task automatic write_reg(input[31:0]address,input[63:0]data,input[7:0]strb,input integer len,size,id,early_w,expected_response);
 begin
  @(negedge clk);
  fork send_aw(address,len,size,id,early_w?3:0);send_w(data,strb,len,early_w?0:3);join
  repeat(3)@(negedge clk);s_axi_bready=1;
  @(posedge clk);while(!s_axi_bvalid)@(posedge clk);
  if(s_axi_bid!==id||s_axi_bresp!==expected_response)$fatal(1,"MMIO_FAIL write response addr=%08x actual=%0d expected=%0d",address,s_axi_bresp,expected_response);
  @(negedge clk);s_axi_bready=0;transactions=transactions+1;
 end
endtask
task automatic read_reg(input[31:0]address,input integer len,size,id,expected_response,check_data,input[31:0]expected_data,output[31:0]value);
 integer i;begin
  @(negedge clk);s_axi_araddr=address;s_axi_arlen=len;s_axi_arsize=size;s_axi_arid=id;s_axi_arvalid=1;
  @(posedge clk);while(!s_axi_arready)@(posedge clk);@(negedge clk);s_axi_arvalid=0;
  for(i=0;i<=len;i=i+1)begin
   repeat(3)@(negedge clk);s_axi_rready=1;
   @(posedge clk);while(!s_axi_rvalid)@(posedge clk);
   if(s_axi_rid!==id||s_axi_rresp!==expected_response||s_axi_rlast!==(i==len)||s_axi_rdata[63:32]!==0)$fatal(1,"MMIO_FAIL read metadata");
   value=s_axi_rdata[31:0];if(check_data&&value!==expected_data)$fatal(1,"MMIO_FAIL read addr=%08x actual=%08x expected=%08x",address,value,expected_data);
   @(negedge clk);s_axi_rready=0;
  end
  transactions=transactions+1;
 end
endtask
reg[31:0]value;integer i,count;
initial begin
 repeat(4)@(negedge clk);rst_n=1;
 for(i=0;i<16;i=i+1)begin
  write_reg(32'h90001100+8*i,32'ha5a50000+i,8'h0f,0,2,i%8,i%2,0);
  read_reg(32'h90001100+8*i,0,2,(i+3)%8,0,1,32'ha5a50000+i,value);
 end
 write_reg(32'h90001100,64'h0000000012345678,8'hff,0,3,2,1,0);
 read_reg(32'h90001100,0,3,1,0,1,32'h12345678,value);
 write_reg(32'h90001100,0,8'h01,0,2,1,1,2);
 write_reg(32'h90001104,0,8'h0f,0,2,3,0,2);
 write_reg(32'h90002000,1,8'h0f,0,2,2,1,2);
 write_reg(32'h90001000,2,8'h0f,0,2,4,0,2);
 write_reg(32'h90001000,1,8'hff,3,3,6,1,2);
 read_reg(32'h90001008,3,3,7,2,1,0,value);
 read_reg(32'h90001080,0,2,4,2,1,0,value);
 if(started!=0)$fatal(1,"MMIO_FAIL rejected burst had side effect");
 // Queue against backend backpressure, protect arguments, then cancel pending.
 write_reg(32'h90001000,1,8'h0f,0,2,1,1,0);
 read_reg(32'h90001008,0,2,2,0,1,1,value);
 write_reg(32'h90001100,0,8'h0f,0,2,3,0,2);
 write_reg(32'h90001000,1,8'h0f,0,2,5,1,2);
 write_reg(32'h90001028,1,8'h0f,0,2,6,0,0);
 read_reg(32'h90001020,0,2,1,0,1,4,value);
 if(started!=0||request_valid)$fatal(1,"MMIO_FAIL pending cancel");
 write_reg(32'h90001008,1,8'h0f,0,2,4,1,0);
 allow_engine=1;
 write_reg(32'h90001000,1,8'h0f,0,2,1,0,0);
 write_reg(32'h90001108,0,8'h0f,0,2,3,1,2);
 count=0;value=0;
 while(!value[1])begin read_reg(32'h90001008,0,2,2,0,0,0,value);count=count+1;if(count>30)$fatal(1,"MMIO_FAIL completion timeout");end
 if(value[2]||value[0]||started!=1)$fatal(1,"MMIO_FAIL normal completion");
 read_reg(32'h90001010,0,2,1,0,1,80,value);
 read_reg(32'h90001018,0,2,2,0,1,640,value);
 write_reg(32'h90001000,1,8'h0f,0,2,3,1,0);
 write_reg(32'h90001028,1,8'h0f,0,2,4,0,0);
 value=0;while(!value[1])read_reg(32'h90001008,0,2,5,0,0,0,value);
 read_reg(32'h90001020,0,2,6,0,1,4,value);
 if(started!=2)$fatal(1,"MMIO_FAIL in-flight cancel");
 read_reg(32'h90001100,0,2,7,0,1,32'h12345678,value);
 $display("DMA_MMIO_PASS transactions=%0d backend_starts=%0d",transactions,started);$finish;
end
endmodule
