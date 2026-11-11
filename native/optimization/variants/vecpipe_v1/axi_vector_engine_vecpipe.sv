// Vector row DMA: scalar/vector parameters and row-stride broadcast.
// Cache x/B/C interleaved in the existing PE SRAM; no private row RAM.
// Flags bit0 selects immediate B, bit1 immediate bias C.
// POST operations use scalar_b as scaleA, B/scalar_c as scaleB, C/scalar_d as bias.
module axi_vector_engine_vecpipe #(
 parameter SHARED_ALU=0,ID_WIDTH=3,BANKS=256,BANK_BITS=$clog2(BANKS),
 parameter [ID_WIDTH-1:0]DMA_ID=0,
 parameter [31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_a,cmd_b,cmd_c,cmd_destination,cmd_elements,cmd_rows,
 input wire[31:0]cmd_a_stride,cmd_b_stride,cmd_c_stride,cmd_destination_stride,
 input wire[31:0]cmd_operation,cmd_flags,cmd_zero,cmd_scalar_b,cmd_scalar_c,cmd_scalar_d,
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
 localparam IDLE=0,CHECK=1,V_MUL=2,V_CHECK=3,LOAD_START=4,LOAD=5,FETCH=6,FETCH_WAIT=7,APPLY=8,WAIT_VALUE=9,WAIT_WRITE=10,ABORT=11,COMPLETE=12;
 reg[3:0]state;reg[1:0]validation_plane,plane;
 reg[31:0]a_base,b_base,c_base,destination,elements,rows,a_stride,b_stride,c_stride,destination_stride;
 reg[31:0]operation,flags,zero,scalar_b,scalar_c,scalar_d;
 reg[31:0]row_a,row_b,row_c,row_index,block_start,cache_index,cache_word;
 reg initial_write;
 wire a_byte=operation==3||operation==4;
 wire need_b=!flags[0];wire need_c=operation==7&&!flags[1];
 wire[31:0]row_bytes=elements<<2;
 wire[31:0]a_row_bytes=a_byte?elements:row_bytes;
 wire[31:0]block_count=elements-block_start>BANKS?BANKS:elements-block_start;
 wire[31:0]selected_base=validation_plane==0?destination:validation_plane==1?a_base:validation_plane==2?b_base:c_base;
 wire[31:0]selected_stride=validation_plane==0?destination_stride:validation_plane==1?a_stride:validation_plane==2?b_stride:c_stride;
 wire[31:0]selected_bytes=validation_plane==1?a_row_bytes:row_bytes;
 wire[53:0]configuration_product=(rows[26:0]-27'd1)*selected_stride[26:0];
 reg[53:0]span_product;
 wire[54:0]selected_span={1'b0,span_product}+{23'b0,selected_bytes};
 wire[32:0]selected_end={1'b0,selected_base}+{1'b0,selected_span[31:0]};
 wire[32:0]ram_end={1'b0,RAM_BASE}+{1'b0,RAM_BYTES};
 reg[32:0]destination_end;
 wire stopping=error!=0||cancel;
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE&&!cancel;
 wire bad_header=operation>7||flags[31:2]!=0||elements==0||elements>65535||rows==0||rows>RAM_BYTES||
  a_stride>RAM_BYTES||destination_stride>RAM_BYTES||destination_stride<row_bytes||
  (need_b&&b_stride>RAM_BYTES)||(need_c&&c_stride>RAM_BYTES)||(operation==5&&zero!=0);
 wire bad_span=selected_span>RAM_BYTES||selected_base<RAM_BASE||selected_end>ram_end||
  (validation_plane!=0&&{1'b0,selected_base}<destination_end&&{1'b0,destination}<selected_end);
 wire read_ready,read_busy,read_done,write_ready,write_busy,write_done;
 wire[3:0]read_error,write_error;reg read_active,write_active,write_finished;
 wire start=state==LOAD_START&&!stopping&&read_ready&&(!initial_write||write_ready);
 wire[31:0]read_address=(plane==0?row_a:plane==1?row_b:row_c)+(plane==0&&a_byte?block_start:block_start<<2);
 wire[31:0]block_bytes=plane==0&&a_byte?block_count:block_count<<2;
 wire read_valid,read_accept,read_row_last,read_command_last;wire[63:0]read_data;wire[7:0]read_keep;
 assign read_accept=state==LOAD&&!stopping;
 wire core_ready,core_busy,core_out_valid,core_out_ready;wire[31:0]core_out;wire[7:0]core_error;wire[4:0]core_flags;
 reg[95:0]operand_fifo[0:1];
 reg[1:0]fifo_count;reg fifo_rd,fifo_wr,fetch_pending;
 reg[31:0]fetch_index,return_index;
 wire[95:0]front=operand_fifo[fifo_rd];
 wire[31:0]apply_a=front[31:0],apply_b=front[63:32],apply_c=front[95:64];
 wire take_operand=state==FETCH&&!stopping&&fifo_count!=0&&core_ready;
 wire capture_operand=state==FETCH&&!stopping&&fetch_pending;
 wire[2:0]reserved={1'b0,fifo_count}+{2'b0,fetch_pending}-{2'b0,take_operand};
 wire fetch_launch=state==FETCH&&!stopping&&fetch_index<block_count&&reserved<3'd2;
 wire[31:0]operand_b=operation>=6?scalar_b:flags[0]?scalar_b:apply_b;
 wire[31:0]operand_c=flags[0]?scalar_c:apply_b;
 wire[31:0]operand_d=flags[1]?scalar_d:apply_c;
 wire alu_valid,alu_ready,alu_out_valid,alu_out_ready;wire[3:0]alu_op;wire[31:0]alu_a,alu_b,alu_result;wire[4:0]alu_flags;
 fp32_vector_pipeline core(
  .clk(clk),.rst_n(rst_n),.in_valid(state==FETCH&&fifo_count!=0&&!stopping),.in_ready(core_ready),.in_op(operation[3:0]),
  .in_a(apply_a),.in_b(operand_b),.in_c(operand_c),.in_d(operand_d),.in_zero(zero),
  .cancel(stopping),.busy(core_busy),.out_valid(core_out_valid),.out_ready(core_out_ready),.out_bits(core_out),.out_error(core_error),.out_flags(core_flags),
  .alu_valid(alu_valid),.alu_ready(alu_ready),.alu_op(alu_op),.alu_a(alu_a),.alu_b(alu_b),
  .alu_out_valid(alu_out_valid),.alu_out_ready(alu_out_ready),.alu_result(alu_result),.alu_flags(alu_flags));
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
  .clk(clk),.rst_n(rst_n),.cmd_valid(start),.cmd_ready(read_ready),.cmd_address(read_address),
  .cmd_row_bytes(block_bytes),.cmd_rows(32'd1),.cmd_stride(block_bytes),.cancel(stopping),.busy(read_busy),.done(read_done),.error(read_error),
  .out_valid(read_valid),.out_ready(read_accept),.out_data(read_data),.out_keep(read_keep),.out_row_last(read_row_last),.out_command_last(read_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 reg packed_valid,packed_row_last,packed_command_last;reg[63:0]packed_data;reg[7:0]packed_keep;wire packed_ready;
 assign core_out_ready=state==ABORT||(state==FETCH&&!stopping&&(!packed_valid||packed_ready));
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(start&&initial_write),.cmd_ready(write_ready),.cmd_address(destination),
  .cmd_row_bytes(row_bytes),.cmd_rows(rows),.cmd_stride(destination_stride),.cancel(stopping),.busy(write_busy),.done(write_done),.error(write_error),
  .in_valid(packed_valid&&!stopping),.in_ready(packed_ready),.in_data(packed_data),.in_keep(packed_keep),.in_row_last(packed_row_last),.in_command_last(packed_command_last),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 integer j,offset,bank;reg[95:0]cached_values;
 always @*begin
  bank_addr=0;bank_ce=0;bank_we=0;bank_write=0;cached_values=0;
  for(j=0;j<12;j=j+1)begin
   offset=return_index*12+j;bank=offset&(BANKS-1);cached_values[j*8+:8]=bank_read[bank*8+:8];
   if(fetch_launch)begin
    offset=fetch_index*12+j;bank=offset&(BANKS-1);
    bank_addr[bank*4+:4]=offset>>BANK_BITS;bank_ce[bank]=1;
   end
  end
  if(state==LOAD&&read_valid&&read_accept)begin
   for(j=0;j<8;j=j+1)begin
    offset=plane==0&&a_byte?(cache_word+j)*12:(cache_word+(j>>2))*12+plane*4+(j&3);
    bank=offset&(BANKS-1);bank_addr[bank*4+:4]=offset>>BANK_BITS;
    bank_ce[bank]=read_keep[j];bank_we[bank]=read_keep[j];bank_write[bank*8+:8]=read_data[j*8+:8];
   end
  end
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;validation_plane<=0;plane<=0;done<=0;error<=0;cycles<=0;streamed_bytes<=0;
   a_base<=0;b_base<=0;c_base<=0;destination<=0;elements<=0;rows<=0;a_stride<=0;b_stride<=0;c_stride<=0;destination_stride<=0;
   operation<=0;flags<=0;zero<=0;scalar_b<=0;scalar_c<=0;scalar_d<=0;row_a<=0;row_b<=0;row_c<=0;row_index<=0;
   block_start<=0;cache_index<=0;cache_word<=0;initial_write<=0;span_product<=0;destination_end<=0;
   read_active<=0;write_active<=0;write_finished<=0;fifo_count<=0;fifo_rd<=0;fifo_wr<=0;fetch_pending<=0;fetch_index<=0;return_index<=0;
   operand_fifo[0]<=0;operand_fifo[1]<=0;
   packed_valid<=0;packed_data<=0;packed_keep<=0;packed_row_last<=0;packed_command_last<=0;
  end else begin
   done<=0;if(busy)cycles<=cycles+1'b1;
   if(read_done)read_active<=0;if(write_done)begin write_active<=0;write_finished<=1;end
   if(start)begin read_active<=1;if(initial_write)begin write_active<=1;write_finished<=0;initial_write<=0;end end
   if(error==0&&busy)begin
    if(cancel)error<=4;
    else if(read_active&&read_error!=0)error<={4'h1,read_error};
    else if(write_active&&write_error!=0)error<={4'h2,write_error};
    else if(core_out_valid&&core_error!=0)error<=8'h30|core_error;
   end
   if(packed_valid&&packed_ready)packed_valid<=0;
   if(state==FETCH&&!stopping)begin
    fetch_pending<=fetch_launch;
    if(fetch_launch)begin return_index<=fetch_index;fetch_index<=fetch_index+1'b1;end
    if(capture_operand)begin operand_fifo[fifo_wr]<=cached_values;fifo_wr<=~fifo_wr;end
    if(take_operand)fifo_rd<=~fifo_rd;
    case({capture_operand,take_operand})
     2'b10:fifo_count<=fifo_count+1'b1;
     2'b01:fifo_count<=fifo_count-1'b1;
     default:begin end
    endcase
   end else begin
    fetch_pending<=0;
    if(stopping)begin fifo_count<=0;fifo_rd<=0;fifo_wr<=0;end
   end
   if(busy&&stopping&&state!=ABORT&&state!=COMPLETE)state<=ABORT;
   else case(state)
    IDLE:if(cmd_valid&&cmd_ready)begin
     a_base<=cmd_a;b_base<=cmd_b;c_base<=cmd_c;destination<=cmd_destination;elements<=cmd_elements;rows<=cmd_rows;
     a_stride<=cmd_a_stride;b_stride<=cmd_b_stride;c_stride<=cmd_c_stride;destination_stride<=cmd_destination_stride;
     operation<=cmd_operation;flags<=cmd_flags;zero<=cmd_zero;scalar_b<=cmd_scalar_b;scalar_c<=cmd_scalar_c;scalar_d<=cmd_scalar_d;
     row_a<=cmd_a;row_b<=cmd_b;row_c<=cmd_c;row_index<=0;block_start<=0;cache_index<=0;plane<=0;validation_plane<=0;
     error<=0;cycles<=0;streamed_bytes<=0;initial_write<=1;packed_valid<=0;write_finished<=0;
     fifo_count<=0;fifo_rd<=0;fifo_wr<=0;fetch_pending<=0;fetch_index<=0;return_index<=0;state<=CHECK;
    end
    CHECK:if(bad_header)begin error<=1;state<=COMPLETE;end else state<=V_MUL;
    V_MUL:begin span_product<=configuration_product;state<=V_CHECK;end
    V_CHECK:if(bad_span)begin error<=1;state<=COMPLETE;end else begin
     if(validation_plane==0)begin destination_end<=selected_end;validation_plane<=1;state<=V_MUL;end
     else if(validation_plane==1&&need_b)begin validation_plane<=2;state<=V_MUL;end
     else if(validation_plane<3&&need_c)begin validation_plane<=3;state<=V_MUL;end
     else state<=LOAD_START;
    end
    LOAD_START:if(start)begin cache_word<=0;state<=LOAD;end
    LOAD:begin
     if(read_valid&&read_accept)cache_word<=cache_word+(plane==0&&a_byte?32'd8:32'd2);
     if(read_done)begin
      if(plane==0&&need_b)begin plane<=1;state<=LOAD_START;end
      else if(plane<2&&need_c)begin plane<=2;state<=LOAD_START;end
      else begin
       cache_index<=0;fifo_count<=0;fifo_rd<=0;fifo_wr<=0;fetch_pending<=0;fetch_index<=0;return_index<=0;
       state<=FETCH;
      end
     end
    end
    FETCH:if(core_out_valid&&core_out_ready)begin
     if(core_error!=0)state<=ABORT;
     else begin
      streamed_bytes<=streamed_bytes+4;
      if((block_start+cache_index)&1)packed_data[63:32]<=core_out;else packed_data<={32'b0,core_out};
      if(((block_start+cache_index)&1)||block_start+cache_index+1==elements)begin
       packed_valid<=1;packed_keep<=((block_start+cache_index)&1)?8'hff:8'h0f;
       packed_row_last<=block_start+cache_index+1==elements;packed_command_last<=block_start+cache_index+1==elements&&row_index+1==rows;
      end
      if(cache_index+1<block_count)begin cache_index<=cache_index+1;end
      else if(block_start+block_count<elements)begin block_start<=block_start+block_count;plane<=0;state<=LOAD_START;end
      else if(row_index+1<rows)begin
       row_index<=row_index+1;row_a<=row_a+a_stride;row_b<=row_b+b_stride;row_c<=row_c+c_stride;
       block_start<=0;plane<=0;state<=LOAD_START;
      end else state<=WAIT_WRITE;
     end
    end
    WAIT_WRITE:if(write_finished||write_done)state<=COMPLETE;
    ABORT:begin packed_valid<=0;if(!read_busy&&!write_busy&&!core_busy)state<=COMPLETE;end
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=ABORT;end
   endcase
  end
 end
 // synopsys translate_off
 always@(posedge clk)if(rst_n&&state==FETCH&&!stopping)begin
  if(fifo_count>2||{1'b0,fifo_count}+{2'b0,fetch_pending}>3'd2)
   $fatal(1,"VECPIPE_FIFO_FAIL reservation overflow");
  if(capture_operand&&fifo_count==2&&!take_operand)$fatal(1,"VECPIPE_FIFO_FAIL capture overflow");
  if(core_out_valid&&core_out_ready&&cache_index>=block_count)$fatal(1,"VECPIPE_FIFO_FAIL excess result");
 end
 // synopsys translate_on
endmodule
