// Exact scalar-scale QuantizeLinear: load the compiled 255 thresholds once,
// stream FP32 inputs through the verified 8-stage lookup, pack INT8 writes.
// This candidate uses the measured FF table (no additional SRAM macro).
module axi_quant_engine #(
 parameter ID_WIDTH=3,parameter[ID_WIDTH-1:0]DMA_ID=0,
 parameter[31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_source,cmd_destination,cmd_table,cmd_elements,
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
 localparam IDLE=0,CHECK=1,LOAD_START=2,LOAD=3,RUN_START=4,RUN=5,COMPLETE=6;
 reg[2:0]state;
 reg[31:0]source,destination,table_base,elements;
 reg[63:0]input_words;
 reg[1:0]word_count;
 reg[7:0]table_index;
 reg read_finished,write_finished;
 reg[127:0]fifo,next_fifo;reg[4:0]fifo_count,next_fifo_count;
 reg[31:0]quant_received;
 wire stopping=error!=0||cancel;
 wire[63:0]source_end={32'b0,source}+({32'b0,elements}<<2);
 wire[63:0]destination_end={32'b0,destination}+{32'b0,elements};
 wire[63:0]table_end={32'b0,table_base}+64'd1020;
 wire[63:0]ram_end={32'b0,RAM_BASE}+{32'b0,RAM_BYTES};
 wire bad_command=!elements||source<RAM_BASE||destination<RAM_BASE||table_base<RAM_BASE||
  source_end>ram_end||destination_end>ram_end||table_end>ram_end||
  ({32'b0,source}<destination_end&&{32'b0,destination}<source_end)||
  ({32'b0,table_base}<destination_end&&{32'b0,destination}<table_end);
 wire read_ready,read_busy,read_done,read_valid;wire[3:0]read_error;
 wire[63:0]read_data;wire[7:0]read_keep;wire read_row_last,read_command_last;
 wire write_ready,write_busy,write_done,write_accept;wire[3:0]write_error;
 wire cfg_ready,quant_in_ready,quant_out_valid,quant_invalid;
 wire[7:0]quant_out;
 wire load_start=state==LOAD_START&&!stopping&&read_ready;
 wire run_start=state==RUN_START&&!stopping&&read_ready&&write_ready;
 wire cfg_valid=state==LOAD&&word_count!=0&&!stopping;
 wire quant_in_valid=state==RUN&&word_count!=0&&!stopping;
 wire word_pop=(cfg_valid&&cfg_ready)||(quant_in_valid&&quant_in_ready);
 wire read_accept=(state==LOAD||state==RUN)&&!stopping&&(word_count==0||(word_count==1&&word_pop));
 wire word_fill=read_valid&&read_accept;
 wire quant_out_ready=state==RUN&&!stopping&&fifo_count<16;
 wire quant_push=quant_out_valid&&quant_out_ready&&!quant_invalid;
 wire[31:0]remaining=elements-streamed_bytes;
 wire[3:0]pack_bytes=remaining>=8?4'd8:remaining[3:0];
 wire pack_valid=state==RUN&&!stopping&&(fifo_count>=8||(quant_received==elements&&fifo_count!=0));
 wire pack_pop=pack_valid&&write_accept;
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE;
 axi_dma_reader #(.ID_WIDTH(ID_WIDTH),.READ_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))reader(
  .clk(clk),.rst_n(rst_n),.cmd_valid(load_start||run_start),.cmd_ready(read_ready),
  .cmd_address(load_start?table_base:source),.cmd_row_bytes(load_start?32'd1020:elements<<2),.cmd_rows(32'd1),.cmd_stride(32'd0),
  .cancel((state==LOAD||state==RUN)&&stopping),.busy(read_busy),.done(read_done),.error(read_error),
  .out_valid(read_valid),.out_ready(read_accept),.out_data(read_data),.out_keep(read_keep),.out_row_last(read_row_last),.out_command_last(read_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 wire lookup_rst_n=rst_n&&state!=IDLE&&state!=COMPLETE;
 quant_lookup8 lookup(.clk(clk),.rst_n(lookup_rst_n),.cfg_clear(1'b0),.cfg_valid(cfg_valid),.cfg_ready(cfg_ready),.cfg_index(table_index),.cfg_value(input_words[31:0]),
  .in_valid(quant_in_valid),.in_ready(quant_in_ready),.in_bits(input_words[31:0]),
  .out_valid(quant_out_valid),.out_ready(quant_out_ready),.out_bits(quant_out),.out_invalid(quant_invalid));
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(run_start),.cmd_ready(write_ready),.cmd_address(destination),.cmd_row_bytes(elements),.cmd_rows(32'd1),.cmd_stride(32'd0),
  .cancel(state==RUN&&stopping),.busy(write_busy),.done(write_done),.error(write_error),
  .in_valid(pack_valid),.in_ready(write_accept),.in_data(fifo[63:0]),.in_keep(8'hff>>(8-pack_bytes)),.in_row_last(remaining<=8),.in_command_last(remaining<=8),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 always @*begin
  next_fifo=fifo;next_fifo_count=fifo_count;
  if(pack_pop)begin next_fifo=next_fifo>>(pack_bytes*8);next_fifo_count=next_fifo_count-pack_bytes;end
  if(quant_push)begin next_fifo=next_fifo|({120'b0,quant_out}<<(next_fifo_count*8));next_fifo_count=next_fifo_count+1'b1;end
  if(state!=RUN||stopping)begin next_fifo=0;next_fifo_count=0;end
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;done<=0;error<=0;cycles<=0;streamed_bytes<=0;source<=0;destination<=0;table_base<=0;elements<=0;
   input_words<=0;word_count<=0;table_index<=0;read_finished<=0;write_finished<=0;fifo<=0;fifo_count<=0;quant_received<=0;
  end else begin
   done<=0;fifo<=next_fifo;fifo_count<=next_fifo_count;
   if(busy)cycles<=cycles+1'b1;
   if(busy&&cancel&&error==0)error<=4;
   if(word_pop)begin input_words<=input_words>>32;word_count<=word_count-1'b1;if(state==LOAD)table_index<=table_index+1'b1;end
   if(word_fill)begin input_words<=read_data;word_count<=read_keep==8'hff ? 2 : 1;end
   if(quant_push)quant_received<=quant_received+1'b1;
   if(pack_pop)streamed_bytes<=streamed_bytes+pack_bytes;
   case(state)
    IDLE:if(cmd_valid)begin
     state<=CHECK;source<=cmd_source;destination<=cmd_destination;table_base<=cmd_table;elements<=cmd_elements;error<=0;cycles<=0;streamed_bytes<=0;
    end
    CHECK:if(stopping)state<=COMPLETE;else if(bad_command)begin error<=1;state<=COMPLETE;end else state<=LOAD_START;
    LOAD_START:if(stopping)state<=COMPLETE;else if(load_start)begin state<=LOAD;word_count<=0;table_index<=0;read_finished<=0;end
    LOAD:begin
     if(read_error!=0&&error==0)error<={4'h1,read_error};
     if(read_done)read_finished<=1;
     if(read_finished||read_done)begin
      if(stopping||read_error!=0)state<=COMPLETE;
      else if(word_count==0&&table_index==255)state<=RUN_START;
     end
    end
    RUN_START:if(stopping)state<=COMPLETE;else if(run_start)begin state<=RUN;word_count<=0;quant_received<=0;read_finished<=0;write_finished<=0;end
    RUN:begin
     if(read_done)read_finished<=1;if(write_done)write_finished<=1;
     if(error==0)begin
      if(read_error!=0)error<={4'h1,read_error};else if(write_error!=0)error<={4'h2,write_error};
      else if(quant_out_valid&&quant_out_ready&&quant_invalid)error<=3;
     end
     if((read_finished||read_done)&&(write_finished||write_done))state<=COMPLETE;
    end
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=COMPLETE;end
   endcase
  end
 end
endmodule
