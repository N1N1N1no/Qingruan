// Two-pass LayerNorm. First pass reduces x as an ordered stream; second pass
// caches BANKS floats from each of x/scale/bias, then applies normalization.
// The three planes are interleaved by element: 12 consecutive bytes can be
// read from distinct banks in one cycle. 12*BANKS bytes fit the existing
// 16*BANKS PE SRAM bytes, including 128-bank candidates. No private row RAM.
module axi_layernorm_engine #(
 parameter SHARED_ALU=0,ID_WIDTH=3,BANKS=256,BANK_BITS=$clog2(BANKS),
 parameter [ID_WIDTH-1:0]DMA_ID=0,
 parameter [31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_source,cmd_destination,cmd_scale,cmd_bias,cmd_elements,cmd_rows,
 input wire[31:0]cmd_source_stride,cmd_destination_stride,cmd_epsilon,
 input wire cancel,output wire busy,output reg done,output reg[7:0]error,
 output reg[31:0]cycles,streamed_bytes,
 output reg[BANKS*4-1:0]bank_addr,output reg[BANKS-1:0]bank_ce,bank_we,
 output reg[BANKS*8-1:0]bank_write,input wire[BANKS*8-1:0]bank_read,
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
 localparam IDLE=0,CHECK=1,FIRST_START=2,REDUCE=3,CACHE_START=4,CACHE_LOAD=5,FETCH=6,FETCH_WAIT=7,
  APPLY=8,WAIT_VALUE=9,ROW_FINISH=10,WAIT_WRITE=11,ABORT=12,COMPLETE=13;
 reg[3:0]state;reg[1:0]plane;
 reg[31:0]source,destination,scale_base,bias_base,elements,rows,source_stride,destination_stride,epsilon;
 reg[31:0]row_source,row_index,block_start,cache_index,cache_word;
 wire[31:0]block_count=elements-block_start>BANKS?BANKS:elements-block_start;
 wire[31:0]row_bytes=elements<<2;
 wire[63:0]ram_end={32'b0,RAM_BASE}+{32'b0,RAM_BYTES};
 wire[63:0]source_end={32'b0,source}+({32'b0,rows}-1)*{32'b0,source_stride}+{32'b0,row_bytes};
 wire[63:0]destination_end={32'b0,destination}+({32'b0,rows}-1)*{32'b0,destination_stride}+{32'b0,row_bytes};
 wire[63:0]scale_end={32'b0,scale_base}+{32'b0,row_bytes};
 wire[63:0]bias_end={32'b0,bias_base}+{32'b0,row_bytes};
 wire bad_command=elements==0||elements>65535||rows==0||destination_stride<row_bytes||
  source<RAM_BASE||destination<RAM_BASE||scale_base<RAM_BASE||bias_base<RAM_BASE||
  source_end>ram_end||destination_end>ram_end||scale_end>ram_end||bias_end>ram_end||
  ({32'b0,source}<destination_end&&{32'b0,destination}<source_end)||
  ({32'b0,scale_base}<destination_end&&{32'b0,destination}<scale_end)||
  ({32'b0,bias_base}<destination_end&&{32'b0,destination}<bias_end);
 wire stopping=error!=0||cancel;
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE&&!cancel;
 wire read_ready,read_busy,read_done,write_ready,write_busy,write_done;
 wire[3:0]read_error,write_error;reg read_active,write_active,core_active;
 reg read_finished,write_finished,core_finished;
 wire core_ready,core_busy,core_done;wire[7:0]core_error;
 wire first_start=state==FIRST_START&&!stopping&&read_ready&&core_ready&&(row_index!=0||write_ready);
 wire cache_start=state==CACHE_START&&!stopping&&read_ready;
 wire[31:0]read_address=first_start?row_source:(plane==0?row_source:plane==1?scale_base:bias_base)+(block_start<<2);
 wire read_valid,read_accept,read_row_last,read_command_last;
 wire[63:0]read_data;wire[7:0]read_keep;
 reg reduction_buffer_valid,reduction_upper;reg[63:0]reduction_buffer;reg[7:0]reduction_keep;
 wire reduce_ready,stats_valid,apply_ready,core_out_valid,core_out_ready;
 wire reduce_valid=state==REDUCE&&reduction_buffer_valid&&!stopping;
 wire[31:0]reduce_value=reduction_upper?reduction_buffer[63:32]:reduction_buffer[31:0];
 assign read_accept=!stopping&&(state==REDUCE?!reduction_buffer_valid:state==CACHE_LOAD);
 wire stats_ready=state==REDUCE&&!stopping&&read_finished&&!read_busy&&!reduction_buffer_valid;
 wire[31:0]core_mean,core_inverse,core_out;
 reg[31:0]apply_x,apply_scale,apply_bias;
 wire alu_valid,alu_ready,alu_out_valid,alu_out_ready;wire[3:0]alu_op;wire[31:0]alu_a,alu_b,alu_result;wire[4:0]alu_flags;
 fp32_layernorm core(
  .clk(clk),.rst_n(rst_n),.cmd_valid(first_start),.cmd_ready(core_ready),.cmd_elements(elements),.cmd_epsilon(epsilon),
  .cancel(stopping),.busy(core_busy),.done(core_done),.error(core_error),
  .reduce_valid(reduce_valid),.reduce_ready(reduce_ready),.reduce_value(reduce_value),
  .stats_valid(stats_valid),.stats_ready(stats_ready),.mean(core_mean),.inverse(core_inverse),
  .apply_valid(state==APPLY&&!stopping),.apply_ready(apply_ready),.apply_x(apply_x),.apply_scale(apply_scale),.apply_bias(apply_bias),
  .out_valid(core_out_valid),.out_ready(core_out_ready),.out_bits(core_out),
  .alu_valid(alu_valid),.alu_ready(alu_ready),.alu_op(alu_op),.alu_a(alu_a),.alu_b(alu_b),
  .alu_out_valid(alu_out_valid),.alu_out_ready(alu_out_ready),.alu_result(alu_result));
 wire local_alu_ready,local_alu_out_valid;wire[31:0]local_alu_result;wire[4:0]local_alu_flags;
 assign alu_ready=SHARED_ALU?shared_alu_ready:local_alu_ready;
 assign alu_out_valid=SHARED_ALU?shared_alu_out_valid:local_alu_out_valid;
 assign alu_result=SHARED_ALU?shared_alu_result:local_alu_result;
 assign alu_flags=SHARED_ALU?shared_alu_flags:local_alu_flags;
 assign shared_alu_valid=SHARED_ALU&&alu_valid;
 assign shared_alu_op=SHARED_ALU?(alu_op):4'b0;
 assign shared_alu_a=SHARED_ALU?(alu_a):32'b0;
 assign shared_alu_b=SHARED_ALU?(alu_b):32'b0;
 assign shared_alu_out_ready=SHARED_ALU&&alu_out_ready;
 generate if(!SHARED_ALU)begin:PRIVATE
  fp32_alu u_alu(.clk(clk),.rst_n(rst_n),.in_valid(alu_valid),.in_ready(local_alu_ready),.op((alu_op)),.a((alu_a)),.b((alu_b)),
   .out_valid(local_alu_out_valid),.out_ready(alu_out_ready),.result(local_alu_result),.flags(local_alu_flags));
 end else begin:SHARED
  assign local_alu_ready=0;assign local_alu_out_valid=0;assign local_alu_result=0;assign local_alu_flags=0;
 end endgenerate

 axi_dma_reader #(.ID_WIDTH(ID_WIDTH),.READ_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))reader(
  .clk(clk),.rst_n(rst_n),.cmd_valid(first_start||cache_start),.cmd_ready(read_ready),.cmd_address(read_address),
  .cmd_row_bytes(first_start?row_bytes:block_count<<2),.cmd_rows(32'd1),.cmd_stride(row_bytes),
  .cancel(stopping),.busy(read_busy),.done(read_done),.error(read_error),
  .out_valid(read_valid),.out_ready(read_accept),.out_data(read_data),.out_keep(read_keep),.out_row_last(read_row_last),.out_command_last(read_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 reg packed_valid,packed_row_last,packed_command_last;reg[63:0]packed_data;reg[7:0]packed_keep;
 wire packed_ready;
 assign core_out_ready=state==WAIT_VALUE&&!stopping&&(!packed_valid||packed_ready);
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(first_start&&row_index==0),.cmd_ready(write_ready),.cmd_address(destination),
  .cmd_row_bytes(row_bytes),.cmd_rows(rows),.cmd_stride(destination_stride),.cancel(stopping),.busy(write_busy),.done(write_done),.error(write_error),
  .in_valid(packed_valid&&!stopping),.in_ready(packed_ready),.in_data(packed_data),.in_keep(packed_keep),.in_row_last(packed_row_last),.in_command_last(packed_command_last),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 integer j,offset,bank;reg[95:0]cached_values;
 always @*begin
  bank_addr=0;bank_ce=0;bank_we=0;bank_write=0;cached_values=0;
  for(j=0;j<12;j=j+1)begin
   offset=cache_index*12+j;bank=offset&(BANKS-1);cached_values[j*8+:8]=bank_read[bank*8+:8];
   if(state==FETCH&&!stopping)begin bank_addr[bank*4+:4]=offset>>BANK_BITS;bank_ce[bank]=1;end
  end
  if(state==CACHE_LOAD&&read_valid&&read_accept)begin
   for(j=0;j<8;j=j+1)begin
    offset=(cache_word+(j>>2))*12+plane*4+(j&3);bank=offset&(BANKS-1);
    bank_addr[bank*4+:4]=offset>>BANK_BITS;bank_ce[bank]=read_keep[j];bank_we[bank]=read_keep[j];bank_write[bank*8+:8]=read_data[j*8+:8];
   end
  end
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;plane<=0;done<=0;error<=0;cycles<=0;streamed_bytes<=0;
   source<=0;destination<=0;scale_base<=0;bias_base<=0;elements<=0;rows<=0;source_stride<=0;destination_stride<=0;epsilon<=0;
   row_source<=0;row_index<=0;block_start<=0;cache_index<=0;cache_word<=0;
   read_active<=0;write_active<=0;core_active<=0;read_finished<=0;write_finished<=0;core_finished<=0;
   reduction_buffer_valid<=0;reduction_upper<=0;reduction_buffer<=0;reduction_keep<=0;
   apply_x<=0;apply_scale<=0;apply_bias<=0;packed_valid<=0;packed_data<=0;packed_keep<=0;packed_row_last<=0;packed_command_last<=0;
  end else begin
   done<=0;if(busy)cycles<=cycles+1'b1;
   if(read_done)begin read_finished<=1;read_active<=0;end
   if(write_done)begin write_finished<=1;write_active<=0;end
   if(core_done)begin core_finished<=1;core_active<=0;end
   if(first_start||cache_start)begin read_finished<=0;read_active<=1;end
   if(first_start)begin core_finished<=0;core_active<=1;if(row_index==0)begin write_finished<=0;write_active<=1;end end
   if(error==0&&busy)begin
    if(cancel)error<=4;
    else if(read_active&&read_error!=0)error<={4'h1,read_error};
    else if(write_active&&write_error!=0)error<={4'h2,write_error};
    else if(core_active&&core_error!=0)error<=8'h30|core_error;
   end
   if(packed_valid&&packed_ready)packed_valid<=0;
   if(state==REDUCE&&read_valid&&read_accept)begin reduction_buffer<=read_data;reduction_keep<=read_keep;reduction_buffer_valid<=1;reduction_upper<=0;end
   if(reduce_valid&&reduce_ready)begin
    if(!reduction_upper&&reduction_keep==8'hff)reduction_upper<=1;else begin reduction_buffer_valid<=0;reduction_upper<=0;end
   end
   if(busy&&stopping&&state!=ABORT&&state!=COMPLETE)state<=ABORT;
   else case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     source<=cmd_source;destination<=cmd_destination;scale_base<=cmd_scale;bias_base<=cmd_bias;elements<=cmd_elements;rows<=cmd_rows;
     source_stride<=cmd_source_stride;destination_stride<=cmd_destination_stride;epsilon<=cmd_epsilon;
     row_source<=cmd_source;row_index<=0;block_start<=0;cache_index<=0;plane<=0;
     error<=0;cycles<=0;streamed_bytes<=0;reduction_buffer_valid<=0;packed_valid<=0;state<=CHECK;
    end
    CHECK:if(bad_command)begin error<=1;state<=COMPLETE;end else state<=FIRST_START;
    FIRST_START:if(first_start)begin reduction_buffer_valid<=0;state<=REDUCE;end
    REDUCE:if(stats_valid&&stats_ready)begin block_start<=0;plane<=0;state<=CACHE_START;end
    CACHE_START:if(cache_start)begin cache_word<=0;state<=CACHE_LOAD;end
    CACHE_LOAD:begin
     if(read_valid&&read_accept)cache_word<=cache_word+(read_keep==8'hff ? 32'd2 : 32'd1);
     if(read_done)begin
      if(plane==2)begin cache_index<=0;state<=FETCH;end else begin plane<=plane+1'b1;state<=CACHE_START;end
     end
    end
    FETCH:if(apply_ready)state<=FETCH_WAIT;
    FETCH_WAIT:begin apply_x<=cached_values[31:0];apply_scale<=cached_values[63:32];apply_bias<=cached_values[95:64];state<=APPLY;end
    APPLY:if(apply_ready)state<=WAIT_VALUE;
    WAIT_VALUE:if(core_out_valid&&core_out_ready)begin
     streamed_bytes<=streamed_bytes+4;
     if((block_start+cache_index)&1)packed_data[63:32]<=core_out;else packed_data<={32'b0,core_out};
     if(((block_start+cache_index)&1)||block_start+cache_index+1==elements)begin
      packed_valid<=1;packed_keep<=((block_start+cache_index)&1)?8'hff:8'h0f;
      packed_row_last<=block_start+cache_index+1==elements;packed_command_last<=block_start+cache_index+1==elements&&row_index+1==rows;
     end
     if(cache_index+1<block_count)begin cache_index<=cache_index+1;state<=FETCH;end
     else if(block_start+block_count<elements)begin block_start<=block_start+block_count;plane<=0;state<=CACHE_START;end
     else state<=ROW_FINISH;
    end
    ROW_FINISH:if(core_finished||core_done)begin
     if(row_index+1==rows)state<=WAIT_WRITE;
     else begin row_index<=row_index+1;row_source<=row_source+source_stride;state<=FIRST_START;end
    end
    WAIT_WRITE:if(write_finished||write_done)state<=COMPLETE;
    ABORT:begin packed_valid<=0;reduction_buffer_valid<=0;if(!read_busy&&!write_busy&&!core_busy)state<=COMPLETE;end
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=ABORT;end
   endcase
  end
 end
endmodule
