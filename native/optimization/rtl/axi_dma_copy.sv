// Common copy/strided-reorder command: reader and writer run concurrently.
// The graph compiler submits disjoint physical input/output allocations.
// Completion waits for both read draining and the final write response.
module axi_dma_copy #(
 parameter ID_WIDTH=3,
 parameter [ID_WIDTH-1:0]DMA_ID=0,
 parameter [31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_source,cmd_destination,cmd_row_bytes,cmd_rows,cmd_source_stride,cmd_destination_stride,
 input wire cancel,output wire busy,output reg done,output reg[7:0]error,
 output reg[31:0]cycles,streamed_bytes,
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
 input wire[1:0]m_axi_bresp,input wire m_axi_bvalid,output wire m_axi_bready
);
 localparam IDLE=0,RUN=1,COMPLETE=2;
 reg[1:0]state;reg read_finished,write_finished;
 wire read_ready,write_ready,read_busy,write_busy,read_done,write_done;
 wire[3:0]read_error,write_error;
 wire stream_valid,stream_ready,stream_row_last,stream_command_last;
 wire[63:0]stream_data;wire[7:0]stream_keep;
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE&&read_ready&&write_ready;
 wire start=cmd_valid&&cmd_ready;
 wire stop=state==RUN&&(cancel||read_error!=0||write_error!=0);
 axi_dma_reader #(.ID_WIDTH(ID_WIDTH),.READ_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))reader(
  .clk(clk),.rst_n(rst_n),.cmd_valid(start),.cmd_ready(read_ready),.cmd_address(cmd_source),.cmd_row_bytes(cmd_row_bytes),.cmd_rows(cmd_rows),.cmd_stride(cmd_source_stride),
  .cancel(stop),.busy(read_busy),.done(read_done),.error(read_error),.out_valid(stream_valid),.out_ready(stream_ready),.out_data(stream_data),.out_keep(stream_keep),.out_row_last(stream_row_last),.out_command_last(stream_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(start),.cmd_ready(write_ready),.cmd_address(cmd_destination),.cmd_row_bytes(cmd_row_bytes),.cmd_rows(cmd_rows),.cmd_stride(cmd_destination_stride),
  .cancel(stop),.busy(write_busy),.done(write_done),.error(write_error),.in_valid(stream_valid),.in_ready(stream_ready),.in_data(stream_data),.in_keep(stream_keep),.in_row_last(stream_row_last),.in_command_last(stream_command_last),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 integer j;reg[3:0]accepted_bytes;
 always @*begin accepted_bytes=0;for(j=0;j<8;j=j+1)accepted_bytes=accepted_bytes+stream_keep[j];end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin state<=IDLE;done<=0;error<=0;read_finished<=0;write_finished<=0;cycles<=0;streamed_bytes<=0;end
  else begin
   done<=0;
   case(state)
    IDLE:if(start)begin state<=RUN;error<=0;read_finished<=0;write_finished<=0;cycles<=0;streamed_bytes<=0;end
    RUN:begin
     cycles<=cycles+1'b1;
     if(stream_valid&&stream_ready)streamed_bytes<=streamed_bytes+accepted_bytes;
     if(read_done)read_finished<=1;
     if(write_done)write_finished<=1;
     if(error==0)begin
      if(read_error!=0)error<={4'h1,read_error};
      else if(write_error!=0)error<={4'h2,write_error};
      else if(cancel)error<=8'h04;
     end
     if((read_finished||read_done)&&(write_finished||write_done))state<=COMPLETE;
    end
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=COMPLETE;end
   endcase
  end
 end
endmodule
