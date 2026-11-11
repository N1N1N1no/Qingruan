// Last-axis float32 Softmax. Process complete rows with one shared FP32 ALU
// and the existing PE SRAM. Source/destination spans must be disjoint.
// Each DMA row is independently drained before advancing; no private SRAM.
module axi_softmax_engine #(
 parameter SHARED_ALU=0,ID_WIDTH=3,BANKS=256,
 parameter [ID_WIDTH-1:0]DMA_ID=0,
 parameter [31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_source,cmd_destination,cmd_channels,cmd_rows,
 input wire[31:0]cmd_source_stride,cmd_destination_stride,
 input wire cancel,output wire busy,output reg done,output reg[7:0]error,
 output reg[31:0]cycles,streamed_bytes,
 output wire[BANKS*4-1:0]bank_addr,output wire[BANKS-1:0]bank_ce,bank_we,
 output wire[BANKS*8-1:0]bank_write,input wire[BANKS*8-1:0]bank_read,
 output wire[31:0]m_axi_araddr,output wire[ID_WIDTH-1:0]m_axi_arid,
 output wire[7:0]m_axi_arlen,output wire[2:0]m_axi_arsize,output wire[1:0]m_axi_arburst,
 output wire m_axi_arvalid,input wire m_axi_arready,
 input wire[63:0]m_axi_rdata,input wire[ID_WIDTH-1:0]m_axi_rid,
 input wire[1:0]m_axi_rresp,input wire m_axi_rlast,m_axi_rvalid,output wire m_axi_rready,
 output wire[31:0]m_axi_awaddr,output wire[ID_WIDTH-1:0]m_axi_awid,
 output wire[7:0]m_axi_awlen,output wire[2:0]m_axi_awsize,output wire[1:0]m_axi_awburst,
 output wire m_axi_awvalid,input wire m_axi_awready,
 output wire[63:0]m_axi_wdata,output wire[7:0]m_axi_wstrb,output wire m_axi_wlast,m_axi_wvalid,
 input wire m_axi_wready,input wire[ID_WIDTH-1:0]m_axi_bid,
 input wire[1:0]m_axi_bresp,input wire m_axi_bvalid,output wire m_axi_bready,
 output wire shared_alu_valid,input wire shared_alu_ready,
 output wire[3:0]shared_alu_op,output wire[31:0]shared_alu_a,shared_alu_b,
 input wire shared_alu_out_valid,output wire shared_alu_out_ready,
 input wire[31:0]shared_alu_result,input wire[4:0]shared_alu_flags
);
 localparam IDLE=0,CHECK=1,START=2,RUN=3,COMPLETE=4;
 reg[2:0]state;
 reg[31:0]source,destination,channels,rows,source_stride,destination_stride,row_index;
 reg read_finished,write_finished,core_finished;
 wire read_ready,write_ready,core_ready,read_busy,write_busy,core_busy;
 wire read_done,write_done,core_done;wire[3:0]read_error,write_error;wire[7:0]core_error;
 wire read_valid,read_accept,read_row_last,read_command_last;
 wire[63:0]read_data;wire[7:0]read_keep;
 wire core_valid,core_accept,core_last;wire[63:0]core_data;wire[7:0]core_keep;
 wire stopping=error!=0||cancel;
 wire start=state==START&&!stopping&&read_ready&&write_ready&&core_ready;
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE&&!cancel;
 wire[31:0]row_bytes=channels<<2;
 wire[63:0]source_end={32'b0,source}+({32'b0,rows}-1)*{32'b0,source_stride}+{32'b0,row_bytes};
 wire[63:0]destination_end={32'b0,destination}+({32'b0,rows}-1)*{32'b0,destination_stride}+{32'b0,row_bytes};
 wire[63:0]ram_end={32'b0,RAM_BASE}+{32'b0,RAM_BYTES};
 wire bad_command=channels==0||channels>BANKS*4||rows==0||destination_stride<row_bytes||
  source<RAM_BASE||destination<RAM_BASE||source_end>ram_end||destination_end>ram_end||
  ({32'b0,source}<destination_end&&{32'b0,destination}<source_end);
 wire row_cancel=state==RUN&&stopping;
 wire core_in_ready;
 assign read_accept=core_in_ready&&!stopping;
 axi_dma_reader #(.ID_WIDTH(ID_WIDTH),.READ_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))reader(
  .clk(clk),.rst_n(rst_n),.cmd_valid(start),.cmd_ready(read_ready),
  .cmd_address(source),.cmd_row_bytes(row_bytes),.cmd_rows(32'd1),.cmd_stride(row_bytes),
  .cancel(row_cancel),.busy(read_busy),.done(read_done),.error(read_error),
  .out_valid(read_valid),.out_ready(read_accept),.out_data(read_data),.out_keep(read_keep),.out_row_last(read_row_last),.out_command_last(read_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 fp32_softmax_row #(.BANKS(BANKS),.SHARED_ALU(SHARED_ALU))core(
  .clk(clk),.rst_n(rst_n),.cmd_valid(start),.cmd_ready(core_ready),.cmd_channels(channels),
  .cancel(row_cancel),.busy(core_busy),.done(core_done),.error(core_error),
  .in_valid(read_valid&&!stopping),.in_ready(core_in_ready),.in_data(read_data),.in_keep(read_keep),.in_last(read_command_last),
  .out_valid(core_valid),.out_ready(core_accept),.out_data(core_data),.out_keep(core_keep),.out_last(core_last),
  .bank_addr(bank_addr),.bank_ce(bank_ce),.bank_we(bank_we),.bank_write(bank_write),.bank_read(bank_read),
  .shared_alu_valid(shared_alu_valid),.shared_alu_ready(shared_alu_ready),.shared_alu_op(shared_alu_op),.shared_alu_a(shared_alu_a),.shared_alu_b(shared_alu_b),.shared_alu_out_valid(shared_alu_out_valid),.shared_alu_out_ready(shared_alu_out_ready),.shared_alu_result(shared_alu_result),.shared_alu_flags(shared_alu_flags));
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(start),.cmd_ready(write_ready),.cmd_address(destination),.cmd_row_bytes(row_bytes),.cmd_rows(32'd1),.cmd_stride(row_bytes),
  .cancel(row_cancel),.busy(write_busy),.done(write_done),.error(write_error),
  .in_valid(core_valid&&!stopping),.in_ready(core_accept),.in_data(core_data),.in_keep(core_keep),.in_row_last(core_last),.in_command_last(core_last),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 // Initial full-span validation uses the original base addresses. During RUN
 // source/destination advance monotonically only within those checked spans.
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;done<=0;error<=0;cycles<=0;streamed_bytes<=0;
   source<=0;destination<=0;channels<=0;rows<=0;source_stride<=0;destination_stride<=0;row_index<=0;
   read_finished<=0;write_finished<=0;core_finished<=0;
  end else begin
   done<=0;if(busy)cycles<=cycles+1'b1;
   if(busy&&cancel&&error==0)error<=4;
   case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     state<=CHECK;source<=cmd_source;destination<=cmd_destination;channels<=cmd_channels;rows<=cmd_rows;
     source_stride<=cmd_source_stride;destination_stride<=cmd_destination_stride;row_index<=0;
     error<=0;cycles<=0;streamed_bytes<=0;
    end
    CHECK:if(stopping)state<=COMPLETE;else if(bad_command)begin error<=1;state<=COMPLETE;end else state<=START;
    START:if(stopping)state<=COMPLETE;else if(start)begin
     state<=RUN;read_finished<=0;write_finished<=0;core_finished<=0;
    end
    RUN:begin
     if(core_valid&&core_accept&&!stopping)streamed_bytes<=streamed_bytes+(core_keep==8'hff ? 32'd8 : 32'd4);
     if(read_done)read_finished<=1;if(write_done)write_finished<=1;if(core_done)core_finished<=1;
     if(error==0&&!cancel)begin
      if(read_error!=0)error<={4'h1,read_error};
      else if(write_error!=0)error<={4'h2,write_error};
      else if(core_error!=0)error<=8'h30|core_error;
     end
     if((read_finished||read_done)&&(write_finished||write_done)&&(core_finished||core_done))begin
      if(stopping||read_error!=0||write_error!=0||core_error!=0||row_index+1==rows)state<=COMPLETE;
      else begin source<=source+source_stride;destination<=destination+destination_stride;row_index<=row_index+1;state<=START;end
     end
    end
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=COMPLETE;end
   endcase
  end
 end
endmodule
