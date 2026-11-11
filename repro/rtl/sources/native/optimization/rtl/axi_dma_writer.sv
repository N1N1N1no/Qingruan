// Packed byte stream -> strided 64-bit AXI4 writes with byte strobes.
// An advertised AW is completed even when cancelled: subsequent beats have
// WSTRB=0. A W beat already stalled on the bus retains its original payload.
module axi_dma_writer #(
 parameter ID_WIDTH=3,
 parameter [ID_WIDTH-1:0]WRITE_ID=0,
 parameter [31:0]RAM_BASE=32'h80000000,
 parameter [31:0]RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_address,cmd_row_bytes,cmd_rows,cmd_stride,
 input wire cancel,output wire busy,output reg done,output reg[3:0]error,
 input wire in_valid,output wire in_ready,input wire[63:0]in_data,
 input wire[7:0]in_keep,input wire in_row_last,in_command_last,
 output wire[31:0]m_axi_awaddr,output wire[ID_WIDTH-1:0]m_axi_awid,
 output wire[7:0]m_axi_awlen,output wire[2:0]m_axi_awsize,
 output wire[1:0]m_axi_awburst,output wire m_axi_awvalid,input wire m_axi_awready,
 output reg[63:0]m_axi_wdata,output reg[7:0]m_axi_wstrb,output reg m_axi_wlast,m_axi_wvalid,
 input wire m_axi_wready,input wire[ID_WIDTH-1:0]m_axi_bid,
 input wire[1:0]m_axi_bresp,input wire m_axi_bvalid,output wire m_axi_bready
);
 localparam IDLE=0,PREPARE=1,ADDRESS=2,SEND=3,RESPONSE=4,FINISH=5,FAIL=6;
 reg[2:0]state;
 reg[31:0]row_base,row_bytes,rows_left,stride,bus_address,words_to_issue;
 reg[31:0]bytes_to_input,bytes_to_build;
 reg[4:0]beats_to_build;
 reg[2:0]skip_bytes;
 reg[127:0]fifo,next_fifo;reg[4:0]fifo_count,next_count;
 wire aborting=error!=0||cancel;
 wire[32:0]row_end={1'b0,row_base}+{1'b0,row_bytes};
 wire[32:0]ram_end={1'b0,RAM_BASE}+{1'b0,RAM_BYTES};
 wire[32:0]next_row={1'b0,row_base}+{1'b0,stride};
 wire[12:0]page_bytes=13'd4096-{1'b0,bus_address[11:0]};
 wire[9:0]page_words=page_bytes>>3;
 reg[4:0]burst_words;
 always @*begin
  burst_words=words_to_issue>16?16:words_to_issue[4:0];
  if(burst_words>page_words)burst_words=page_words[4:0];
 end
 function automatic[63:0]byte_mask(input[3:0]n);
  integer j;begin byte_mask=0;for(j=0;j<8;j=j+1)if(j<n)byte_mask[j*8+:8]=8'hff;end
 endfunction
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE;
 assign m_axi_awaddr=bus_address;assign m_axi_awid=WRITE_ID;
 assign m_axi_awlen={3'b0,burst_words}-1'b1;assign m_axi_awsize=3;
 assign m_axi_awburst=1;assign m_axi_awvalid=state==ADDRESS;
 assign m_axi_bready=state==RESPONSE;
 wire[3:0]input_bytes=bytes_to_input>8?4'd8:bytes_to_input[3:0];
 wire[3:0]space=4'd8-{1'b0,skip_bytes};
 wire[3:0]build_bytes=bytes_to_build>space?space:bytes_to_build[3:0];
 wire build_slot=state==SEND&&beats_to_build!=0&&(!m_axi_wvalid||m_axi_wready);
 wire normal_pop=build_slot&&!aborting&&fifo_count>=build_bytes;
 // An unaligned first bus beat may consume fewer than eight bytes. Readiness
 // must account for its exact byte count, rather than treating any pop as eight.
 wire[5:0]count_after_input={1'b0,fifo_count}-(normal_pop?{2'b0,build_bytes}:6'd0)+{2'b0,input_bytes};
 assign in_ready=(state==ADDRESS||state==SEND)&&!aborting&&bytes_to_input!=0&&count_after_input<=16;
 wire accepted=in_valid&&in_ready;
 wire bad_stream=accepted&&(in_keep!=(8'hff>>(8-input_bytes))||in_row_last!=(bytes_to_input<=8)||in_command_last!=((bytes_to_input<=8)&&(rows_left==1)));
 wire build=build_slot&&(aborting||bad_stream||fifo_count>=build_bytes);
 wire take=build&&!aborting&&!bad_stream;
 always @*begin
  next_fifo=fifo;next_count=fifo_count;
  if(take)begin next_fifo=next_fifo>>(build_bytes*8);next_count=next_count-build_bytes;end
  if(accepted&&!bad_stream)begin
   next_fifo=next_fifo|({64'b0,(in_data&byte_mask(input_bytes))}<<(next_count*8));next_count=next_count+input_bytes;
  end
  if(state==PREPARE||state==FAIL||aborting||bad_stream)begin next_fifo=0;next_count=0;end
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;done<=0;error<=0;row_base<=0;row_bytes<=0;rows_left<=0;stride<=0;bus_address<=0;words_to_issue<=0;
   bytes_to_input<=0;bytes_to_build<=0;beats_to_build<=0;skip_bytes<=0;fifo<=0;fifo_count<=0;
   m_axi_wvalid<=0;m_axi_wlast<=0;m_axi_wstrb<=0;m_axi_wdata<=0;
  end else begin
   done<=0;fifo<=next_fifo;fifo_count<=next_count;
   if(error==0)begin if(bad_stream)error<=3;else if(cancel&&busy)error<=4;end
   if(accepted&&!bad_stream)bytes_to_input<=bytes_to_input-input_bytes;
   if(m_axi_wvalid&&m_axi_wready)m_axi_wvalid<=0;
   if(build)begin
    m_axi_wvalid<=1;m_axi_wlast<=beats_to_build==1;
    m_axi_wstrb<=take?((8'hff>>(8-build_bytes))<<skip_bytes):0;
    m_axi_wdata<=take?((fifo[63:0]&byte_mask(build_bytes))<<(skip_bytes*8)):0;
    beats_to_build<=beats_to_build-1'b1;bytes_to_build<=bytes_to_build-build_bytes;skip_bytes<=0;
   end
   case(state)
    IDLE:if(cmd_valid)begin error<=0;row_base<=cmd_address;row_bytes<=cmd_row_bytes;rows_left<=cmd_rows;stride<=cmd_stride;state<=PREPARE;end
    PREPARE:begin
     if(aborting)state<=FAIL;
     else if(!row_bytes||!rows_left||row_base<RAM_BASE||row_end>ram_end||row_end[32])begin error<=1;state<=FAIL;end
     else begin
      bus_address<={row_base[31:3],3'b0};skip_bytes<=row_base[2:0];words_to_issue<=({1'b0,row_bytes}+row_base[2:0]+7)>>3;
      bytes_to_input<=row_bytes;bytes_to_build<=row_bytes;state<=ADDRESS;
     end
    end
    ADDRESS:if(m_axi_awready)begin
     beats_to_build<=burst_words;words_to_issue<=words_to_issue-burst_words;bus_address<=bus_address+{24'b0,burst_words,3'b0};state<=SEND;
    end
    SEND:if(m_axi_wvalid&&m_axi_wready&&m_axi_wlast)state<=RESPONSE;
    RESPONSE:if(m_axi_bvalid)begin
     if(error==0)begin if(m_axi_bid!=WRITE_ID)error<=3;else if(m_axi_bresp!=0)error<=2;end
     if(aborting||m_axi_bid!=WRITE_ID||m_axi_bresp!=0)state<=FAIL;
     else if(words_to_issue!=0)state<=ADDRESS;
     else if(rows_left==1)state<=FINISH;
     else if(next_row[32])begin error<=1;state<=FAIL;end
     else begin row_base<=next_row[31:0];rows_left<=rows_left-1'b1;state<=PREPARE;end
    end
    FINISH:begin done<=1;state<=IDLE;end
    FAIL:begin done<=1;state<=IDLE;end
    default:begin error<=3;state<=FAIL;end
   endcase
  end
 end
endmodule
