`timescale 1ns/1ps
`ifndef MODEL_ROWS
`define MODEL_ROWS 8
`define MODEL_COLS 32
`define MODEL_LANES 4
`endif
module tb_rtl_model;
import "DPI-C" function longint unsigned c_read64(input int unsigned address);
import "DPI-C" function void c_write64(input int unsigned address,input longint unsigned data,input byte unsigned strobes);
import "DPI-C" context task c_run_model(input string plan_dir,input string input_path,input string expected_path,input string output_path,input string report_path,
 input string checkpoint_path,input string identity,input longint unsigned checkpoint_interval,input int stop_after_checkpoint,output int status);
export "DPI-C" task rtl_command;
export "DPI-C" function rtl_last_bytes;
export "DPI-C" function rtl_read_tick;
export "DPI-C" function rtl_restore_tick;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,request_valid=0,cancel=0;
wire request_ready,engine_busy,engine_done;wire[7:0]engine_error;wire[31:0]engine_cycles,engine_bytes;
reg[7:0]request_opcode=0;reg[511:0]request_arguments=0;
wire[31:0]m_axi_araddr,m_axi_awaddr;wire[2:0]m_axi_arid,m_axi_awid;
wire[7:0]m_axi_arlen,m_axi_awlen;wire[2:0]m_axi_arsize,m_axi_awsize;wire[1:0]m_axi_arburst,m_axi_awburst;
wire m_axi_arvalid,m_axi_arready,m_axi_rready,m_axi_awvalid,m_axi_awready,m_axi_wlast,m_axi_wvalid,m_axi_wready,m_axi_bready;
wire[63:0]m_axi_wdata;wire[7:0]m_axi_wstrb;
reg[63:0]m_axi_rdata=0;reg[2:0]m_axi_rid=0,m_axi_bid=0;reg[1:0]m_axi_rresp=0,m_axi_bresp=0;
reg m_axi_rlast=0,m_axi_rvalid=0,m_axi_bvalid=0;
axi_accel_backend #(.ROWS(`MODEL_ROWS),.COLS(`MODEL_COLS),.LANES(`MODEL_LANES))dut(.*);
longint unsigned tick=0;
reg rd_active=0,wr_active=0,response_pending=0;
reg[31:0]read_address=0,write_address=0;integer read_left=0,write_left=0;
assign m_axi_arready=!rd_active&&!m_axi_rvalid&&tick%5!=0;
assign m_axi_awready=!wr_active&&!response_pending&&!m_axi_bvalid&&tick%7!=0;
assign m_axi_wready=wr_active&&tick%3!=0;
integer read_bursts=0,write_bursts=0;
always@(posedge clk)begin
 tick<=tick+1;
 if(m_axi_rvalid&&m_axi_rready)m_axi_rvalid<=0;
 if(m_axi_bvalid&&m_axi_bready)m_axi_bvalid<=0;
 if(m_axi_arvalid&&m_axi_arready)begin
  if(m_axi_araddr[2:0]||m_axi_arlen>15||m_axi_arsize!=3||m_axi_arburst!=1||{1'b0,m_axi_araddr[11:0]}+(m_axi_arlen+1)*8>4096)$fatal(1,"RTL_MODEL_FAIL AR geometry");
  rd_active<=1;read_address<=m_axi_araddr;read_left<=m_axi_arlen+1;m_axi_rid<=m_axi_arid;read_bursts=read_bursts+1;
 end
 if(rd_active&&(!m_axi_rvalid||m_axi_rready)&&tick%4!=0)begin
  m_axi_rvalid<=1;m_axi_rdata<=c_read64(read_address);m_axi_rlast<=read_left==1;
  read_address<=read_address+8;read_left<=read_left-1;if(read_left==1)rd_active<=0;
 end
 if(m_axi_awvalid&&m_axi_awready)begin
  if(m_axi_awaddr[2:0]||m_axi_awlen>15||m_axi_awsize!=3||m_axi_awburst!=1||{1'b0,m_axi_awaddr[11:0]}+(m_axi_awlen+1)*8>4096)$fatal(1,"RTL_MODEL_FAIL AW geometry");
  wr_active<=1;write_address<=m_axi_awaddr;write_left<=m_axi_awlen+1;m_axi_bid<=m_axi_awid;write_bursts=write_bursts+1;
 end
 if(m_axi_wvalid&&m_axi_wready)begin
  if(!wr_active||m_axi_wlast!==(write_left==1))$fatal(1,"RTL_MODEL_FAIL WLAST");
  c_write64(write_address,m_axi_wdata,m_axi_wstrb);write_address<=write_address+8;write_left<=write_left-1;
  if(write_left==1)begin wr_active<=0;response_pending<=1;end
 end
 if(response_pending&&!m_axi_bvalid&&tick%4!=0)begin response_pending<=0;m_axi_bvalid<=1;end
end
int unsigned saved_bytes=0;
task rtl_command(input int unsigned opcode,input bit[511:0]args,output int unsigned count,status);
 longint unsigned begin_tick;begin
  @(negedge clk);request_opcode=opcode[7:0];request_arguments=args;request_valid=1;begin_tick=tick;
  do @(posedge clk);while(!request_ready);
  @(negedge clk);request_valid=0;
  while(!engine_done)begin @(negedge clk);if(tick-begin_tick>50000000)$fatal(1,"RTL_MODEL_FAIL command timeout op=%0d",opcode);end
  count=engine_cycles;status=engine_error;saved_bytes=engine_bytes;
  if(rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid)$fatal(1,"RTL_MODEL_FAIL early completion");
  @(negedge clk);
 end
endtask
function void rtl_last_bytes(output int unsigned count);count=saved_bytes;endfunction
function void rtl_read_tick(output longint unsigned count);
 if(clk||engine_busy||request_valid||rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid)$fatal(1,"RTL_MODEL_FAIL unsafe checkpoint save");
 count=tick;
endfunction
function void rtl_restore_tick(input longint unsigned count);
 if(clk||engine_busy||request_valid||rd_active||wr_active||response_pending||m_axi_rvalid||m_axi_bvalid)$fatal(1,"RTL_MODEL_FAIL unsafe checkpoint boundary");
 tick=count;
endfunction
string plan_dir,input_path,expected_path,output_path,report_path,checkpoint_path,identity;int status;
longint unsigned checkpoint_interval=5000000;
initial begin
 if(!$value$plusargs("PLAN=%s",plan_dir)||!$value$plusargs("INPUT=%s",input_path)||!$value$plusargs("EXPECTED=%s",expected_path)||!$value$plusargs("OUTPUT=%s",output_path)||!$value$plusargs("REPORT=%s",report_path))$fatal(1,"RTL_MODEL_FAIL arguments");
 if(!$value$plusargs("CHECKPOINT=%s",checkpoint_path)||!$value$plusargs("IDENTITY=%s",identity))$fatal(1,"RTL_MODEL_FAIL checkpoint arguments");
 if($value$plusargs("CHECKPOINT_INTERVAL=%d",checkpoint_interval))begin end
 repeat(4)@(negedge clk);rst_n=1;
 c_run_model(plan_dir,input_path,expected_path,output_path,report_path,checkpoint_path,identity,checkpoint_interval,$test$plusargs("STOP_AFTER_CHECKPOINT"),status);
 if(status==2)begin $display("RTL_MODEL_CHECKPOINT_STOP");$finish;end
 if(status)$fatal(1,"RTL_MODEL_FAIL C graph or output");
 $display("RTL_MODEL_AXI_PASS logical_cycles=%0d read_bursts_this_segment=%0d write_bursts_this_segment=%0d",tick,read_bursts,write_bursts);$finish;
end
endmodule
