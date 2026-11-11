// Four-dimensional strided source rows -> contiguous destination.
// The model's 62 physical transposes need at most four outer dimensions after
// folding the contiguous suffix. Odometer carries take one cycle per axis;
// all multiplication/range validation happens before any AXI is advertised.
// One shared configuration multiplier is time-multiplexed across row count,
// axis rewind and final byte count. No private SRAM or per-element division.
module axi_reorder_engine #(
 parameter ID_WIDTH=3,
 parameter [ID_WIDTH-1:0]DMA_ID=0,
 parameter [31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_source,cmd_destination,cmd_row_bytes,cmd_rank,
 input wire[127:0]cmd_dimensions,cmd_source_strides,
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
 localparam IDLE=0,CHECK=1,V_CHECK=2,V_ROWS=3,V_REWIND=4,V_ACC=5,FINAL_MUL=6,FINAL_CHECK=7,
  START_ALL=8,READ_ROW=9,ADVANCE=10,START_ROW=11,WAIT_WRITE=12,ABORT=13,COMPLETE=14;
 reg[3:0]state;reg[1:0]axis_index;
 reg[31:0]source,destination,row_bytes,rank,total_rows,source_extent,row_address,row_index;
 reg[31:0]dimensions[0:3],strides[0:3],positions[0:3],rewinds[0:3];
 reg[53:0]row_product,rewind_product,output_bytes;
 wire[26:0]multiply_a=state==V_REWIND ? dimensions[axis_index][26:0]-27'd1 : total_rows[26:0];
 wire[26:0]multiply_b=state==V_ROWS ? dimensions[axis_index][26:0] : state==V_REWIND ? strides[axis_index][26:0] : row_bytes[26:0];
 wire[53:0]configuration_product=multiply_a*multiply_b;
 wire[54:0]next_extent={1'b0,rewind_product}+{23'b0,source_extent};
 wire[32:0]ram_end={1'b0,RAM_BASE}+{1'b0,RAM_BYTES};
 wire[32:0]source_end={1'b0,source}+{1'b0,source_extent};
 wire[32:0]destination_end={1'b0,destination}+{1'b0,output_bytes[31:0]};
 wire stopping=error!=0||cancel;
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE&&!cancel;
 wire read_ready,write_ready,read_busy,write_busy,read_done,write_done;
 wire[3:0]read_error,write_error;reg read_active,write_active,write_finished;
 wire first_start=state==START_ALL&&!stopping&&read_ready&&write_ready;
 wire next_start=state==START_ROW&&!stopping&&read_ready;
 wire read_valid,read_accept,read_row_last,read_command_last,write_accept;
 wire[63:0]read_data;wire[7:0]read_keep;
 assign read_accept=write_accept&&!stopping;
 function automatic[3:0]count_bytes(input[7:0]mask);
  integer j;begin count_bytes=0;for(j=0;j<8;j=j+1)count_bytes=count_bytes+mask[j];end
 endfunction
 axi_dma_reader #(.ID_WIDTH(ID_WIDTH),.READ_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))reader(
  .clk(clk),.rst_n(rst_n),.cmd_valid(first_start||next_start),.cmd_ready(read_ready),.cmd_address(row_address),
  .cmd_row_bytes(row_bytes),.cmd_rows(32'd1),.cmd_stride(row_bytes),.cancel(stopping),.busy(read_busy),.done(read_done),.error(read_error),
  .out_valid(read_valid),.out_ready(read_accept),.out_data(read_data),.out_keep(read_keep),.out_row_last(read_row_last),.out_command_last(read_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(first_start),.cmd_ready(write_ready),.cmd_address(destination),
  .cmd_row_bytes(row_bytes),.cmd_rows(total_rows),.cmd_stride(row_bytes),.cancel(stopping),.busy(write_busy),.done(write_done),.error(write_error),
  .in_valid(read_valid&&!stopping),.in_ready(write_accept),.in_data(read_data),.in_keep(read_keep),.in_row_last(read_row_last),
  .in_command_last(read_command_last&&row_index+1==total_rows),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 integer i;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;axis_index<=0;source<=0;destination<=0;row_bytes<=0;rank<=0;total_rows<=0;source_extent<=0;
   row_address<=0;row_index<=0;row_product<=0;rewind_product<=0;output_bytes<=0;
   read_active<=0;write_active<=0;write_finished<=0;done<=0;error<=0;cycles<=0;streamed_bytes<=0;
   for(i=0;i<4;i=i+1)begin dimensions[i]<=0;strides[i]<=0;positions[i]<=0;rewinds[i]<=0;end
  end else begin
   done<=0;if(busy)cycles<=cycles+1'b1;
   if(read_done)read_active<=0;
   if(write_done)begin write_active<=0;write_finished<=1;end
   if(first_start||next_start)read_active<=1;
   if(first_start)begin write_active<=1;write_finished<=0;end
   if(read_valid&&read_accept&&!stopping)streamed_bytes<=streamed_bytes+count_bytes(read_keep);
   if(error==0&&busy)begin
    if(cancel)error<=4;
    else if(read_active&&read_error!=0)error<={4'h1,read_error};
    else if(write_active&&write_error!=0)error<={4'h2,write_error};
   end
   if(busy&&stopping&&state!=ABORT&&state!=COMPLETE)state<=ABORT;
   else case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     source<=cmd_source;destination<=cmd_destination;row_bytes<=cmd_row_bytes;rank<=cmd_rank;
     total_rows<=1;source_extent<=cmd_row_bytes;row_address<=cmd_source;row_index<=0;axis_index<=0;
     error<=0;cycles<=0;streamed_bytes<=0;write_finished<=0;state<=CHECK;
     for(i=0;i<4;i=i+1)begin dimensions[i]<=cmd_dimensions[i*32+:32];strides[i]<=cmd_source_strides[i*32+:32];positions[i]<=0;rewinds[i]<=0;end
    end
    CHECK:if(rank==0||rank>4||row_bytes==0||row_bytes>RAM_BYTES||source<RAM_BASE||destination<RAM_BASE)begin error<=1;state<=COMPLETE;end else state<=V_CHECK;
    V_CHECK:if(dimensions[axis_index]==0||dimensions[axis_index]>RAM_BYTES||strides[axis_index]>RAM_BYTES)begin error<=1;state<=COMPLETE;end else state<=V_ROWS;
    V_ROWS:begin row_product<=configuration_product;state<=V_REWIND;end
    V_REWIND:begin rewind_product<=configuration_product;state<=V_ACC;end
    V_ACC:if(row_product>RAM_BYTES||next_extent>RAM_BYTES)begin error<=1;state<=COMPLETE;end
     else begin
      total_rows<=row_product[31:0];source_extent<=next_extent[31:0];rewinds[axis_index]<=rewind_product[31:0];
      if(axis_index==rank-1)state<=FINAL_MUL;else begin axis_index<=axis_index+1'b1;state<=V_CHECK;end
     end
    FINAL_MUL:begin output_bytes<=configuration_product;state<=FINAL_CHECK;end
    FINAL_CHECK:if(output_bytes>RAM_BYTES||source_end>ram_end||destination_end>ram_end||
      ({1'b0,source}<destination_end&&{1'b0,destination}<source_end))begin error<=1;state<=COMPLETE;end else state<=START_ALL;
    START_ALL:if(first_start)state<=READ_ROW;
    START_ROW:if(next_start)state<=READ_ROW;
    READ_ROW:if(read_done)begin
     if(row_index+1==total_rows)state<=WAIT_WRITE;
     else begin row_index<=row_index+1;axis_index<=rank-32'd1;state<=ADVANCE;end
    end
    ADVANCE:if(positions[axis_index]+1<dimensions[axis_index])begin
     positions[axis_index]<=positions[axis_index]+1;row_address<=row_address+strides[axis_index];state<=START_ROW;
    end else begin
     positions[axis_index]<=0;row_address<=row_address-rewinds[axis_index];
     if(axis_index==0)begin error<=8'hff;state<=ABORT;end else axis_index<=axis_index-1'b1;
    end
    WAIT_WRITE:if(write_finished||write_done)state<=COMPLETE;
    ABORT:if(!read_busy&&!write_busy)state<=COMPLETE;
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=ABORT;end
   endcase
  end
 end
endmodule
