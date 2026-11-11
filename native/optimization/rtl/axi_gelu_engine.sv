// Exact fused GELU command. Process 16 contiguous channels per cached table.
// The 4 KiB table is loaded into the PE SRAM, then all spatial/token rows are
// streamed through it. A new channel block replaces the cache only after both
// AXI engines and the elastic lookup stage have drained.
module axi_gelu_engine #(
 parameter ID_WIDTH=3,BANKS=256,CACHE_CHANNELS=BANKS<256?BANKS/16:16,
 parameter TABLE_INDEX_WIDTH=$clog2(CACHE_CHANNELS*32),
 parameter [ID_WIDTH-1:0]DMA_ID=0,
 parameter [31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_source,cmd_destination,cmd_table,cmd_channels,cmd_rows,
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
 input wire[1:0]m_axi_bresp,input wire m_axi_bvalid,output wire m_axi_bready
);
 localparam IDLE=0,CHECK=1,LOAD_START=2,LOAD=3,RUN_START=4,RUN=5,COMPLETE=6;
 reg[2:0]state;
 reg[31:0]source,destination,table_base,channels,rows,channel_block;
 reg[TABLE_INDEX_WIDTH-1:0]table_word;
 reg read_finished,write_finished;
 wire read_ready,write_ready,read_busy,write_busy,read_done,write_done;
 wire[3:0]read_error,write_error;
 wire read_valid,read_accept,read_row_last,read_command_last;
 wire[63:0]read_data;wire[7:0]read_keep;
 wire lookup_valid,lookup_ready,lookup_row_last,lookup_command_last;
 wire[63:0]lookup_data;wire[7:0]lookup_keep;
 wire load_ready,stream_ready;
 wire stopping=error!=0||cancel;
 wire load_start=state==LOAD_START&&!stopping&&read_ready;
 wire run_start=state==RUN_START&&!stopping&&read_ready&&write_ready;
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE;
 assign read_accept=state==LOAD?load_ready:(state==RUN?stream_ready:1'b0);
 wire[63:0]data_bytes={32'b0,channels}*{32'b0,rows};
 wire[63:0]table_bytes={32'b0,channels}<<8;
 wire[63:0]source_end={32'b0,source}+data_bytes;
 wire[63:0]destination_end={32'b0,destination}+data_bytes;
 wire[63:0]table_end={32'b0,table_base}+table_bytes;
 wire[63:0]ram_end={32'b0,RAM_BASE}+{32'b0,RAM_BYTES};
 wire bad_command=channels==0||(channels&(CACHE_CHANNELS-1))!=0||rows==0||
   source<RAM_BASE||destination<RAM_BASE||table_base<RAM_BASE||
   source_end>ram_end||destination_end>ram_end||table_end>ram_end||
   ({32'b0,source}<destination_end&&{32'b0,destination}<source_end)||
   ({32'b0,table_base}<destination_end&&{32'b0,destination}<table_end);
 axi_dma_reader #(.ID_WIDTH(ID_WIDTH),.READ_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))reader(
  .clk(clk),.rst_n(rst_n),.cmd_valid(load_start||run_start),.cmd_ready(read_ready),
  .cmd_address(load_start?table_base+(channel_block<<8):source+channel_block),
  .cmd_row_bytes(load_start?CACHE_CHANNELS*256:CACHE_CHANNELS),.cmd_rows(load_start?32'd1:rows),.cmd_stride(load_start?CACHE_CHANNELS*256:channels),
  .cancel((state==LOAD||state==RUN)&&stopping),.busy(read_busy),.done(read_done),.error(read_error),
  .out_valid(read_valid),.out_ready(read_accept),.out_data(read_data),.out_keep(read_keep),.out_row_last(read_row_last),.out_command_last(read_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 gelu_lut_stream #(.CHANNELS(CACHE_CHANNELS),.BANKS(BANKS))lookup(
  .clk(clk),.rst_n(rst_n),.stream_reset(state==LOAD_START||state==RUN_START||state==COMPLETE),
  .load_valid(state==LOAD&&read_valid&&!stopping),.load_ready(load_ready),.load_index(table_word),.load_data(read_data),
  .in_valid(state==RUN&&read_valid&&!stopping),.in_ready(stream_ready),.in_data(read_data),.in_keep(read_keep),.in_row_last(read_row_last),.in_command_last(read_command_last),
  .out_valid(lookup_valid),.out_ready(lookup_ready),.out_data(lookup_data),.out_keep(lookup_keep),.out_row_last(lookup_row_last),.out_command_last(lookup_command_last),
  .bank_addr(bank_addr),.bank_ce(bank_ce),.bank_we(bank_we),.bank_write(bank_write),.bank_read(bank_read));
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(run_start),.cmd_ready(write_ready),.cmd_address(destination+channel_block),.cmd_row_bytes(CACHE_CHANNELS),.cmd_rows(rows),.cmd_stride(channels),
  .cancel(state==RUN&&stopping),.busy(write_busy),.done(write_done),.error(write_error),
  .in_valid(state==RUN&&lookup_valid&&!stopping),.in_ready(lookup_ready),.in_data(lookup_data),.in_keep(lookup_keep),.in_row_last(lookup_row_last),.in_command_last(lookup_command_last),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;done<=0;error<=0;cycles<=0;streamed_bytes<=0;
   source<=0;destination<=0;table_base<=0;channels<=0;rows<=0;channel_block<=0;table_word<=0;read_finished<=0;write_finished<=0;
  end else begin
   done<=0;
   if(busy)cycles<=cycles+1'b1;
   if(busy&&cancel&&error==0)error<=4;
   case(state)
    IDLE:if(cmd_valid)begin
     state<=CHECK;source<=cmd_source;destination<=cmd_destination;table_base<=cmd_table;channels<=cmd_channels;rows<=cmd_rows;
     channel_block<=0;error<=0;cycles<=0;streamed_bytes<=0;
    end
    CHECK:begin
     if(stopping)state<=COMPLETE;
     else if(bad_command)begin error<=1;state<=COMPLETE;end
     else state<=LOAD_START;
    end
    LOAD_START:begin
     if(stopping)state<=COMPLETE;
     else if(load_start)begin state<=LOAD;table_word<=0;end
    end
    LOAD:begin
     if(read_valid&&read_accept)table_word<=table_word+1'b1;
     if(read_error!=0&&error==0)error<={4'h1,read_error};
     if(read_done)begin
      if(stopping||read_error!=0)state<=COMPLETE;else state<=RUN_START;
     end
    end
    RUN_START:begin
     if(stopping)state<=COMPLETE;
     else if(run_start)begin state<=RUN;read_finished<=0;write_finished<=0;end
    end
    RUN:begin
     if(lookup_valid&&lookup_ready&&!stopping)streamed_bytes<=streamed_bytes+8;
     if(read_done)read_finished<=1;
     if(write_done)write_finished<=1;
     if(error==0)begin
      if(read_error!=0)error<={4'h1,read_error};
      else if(write_error!=0)error<={4'h2,write_error};
     end
     if((read_finished||read_done)&&(write_finished||write_done))begin
      if(stopping||read_error!=0||write_error!=0||channel_block+CACHE_CHANNELS>=channels)state<=COMPLETE;
      else begin channel_block<=channel_block+CACHE_CHANNELS;state<=LOAD_START;end
     end
    end
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=COMPLETE;end
   endcase
  end
 end
endmodule
