// 64-bit AXI4 reader with row strides, arbitrary byte alignment and packed output.
// At most one read burst is outstanding. Bursts never cross 4 KiB boundaries.
// Cancellation drains an already advertised burst before reporting completion.
module axi_dma_reader #(
 parameter ID_WIDTH=3,
 parameter [ID_WIDTH-1:0] READ_ID=0,
 parameter [31:0] RAM_BASE=32'h80000000,
 parameter [31:0] RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_address,cmd_row_bytes,cmd_rows,cmd_stride,
 input wire cancel,
 output wire busy,output reg done,output reg[3:0]error,
 output wire out_valid,input wire out_ready,output wire[63:0]out_data,
 output wire[7:0]out_keep,output wire out_row_last,out_command_last,
 output wire[31:0]m_axi_araddr,output wire[ID_WIDTH-1:0]m_axi_arid,
 output wire[7:0]m_axi_arlen,output wire[2:0]m_axi_arsize,
 output wire[1:0]m_axi_arburst,output wire m_axi_arvalid,input wire m_axi_arready,
 input wire[63:0]m_axi_rdata,input wire[ID_WIDTH-1:0]m_axi_rid,
 input wire[1:0]m_axi_rresp,input wire m_axi_rlast,m_axi_rvalid,
 output wire m_axi_rready
);
 localparam IDLE=0,PREPARE=1,ADDRESS=2,RECEIVE=3,DRAIN=4,FINISH=5,FAIL=6;
 reg[2:0]state;
 reg[31:0]row_base,row_bytes,rows_left,stride,bus_address,words_to_issue;
 reg[31:0]bytes_to_load,bytes_to_output;
 reg[4:0]burst_left;
 reg[2:0]skip_bytes;
 reg[127:0]fifo,next_fifo;
 reg[4:0]fifo_count,next_count;
 wire aborting=(error!=0)||cancel;
 wire[32:0]row_end={1'b0,row_base}+{1'b0,row_bytes};
 wire[32:0]ram_end={1'b0,RAM_BASE}+{1'b0,RAM_BYTES};
 wire[32:0]next_row={1'b0,row_base}+{1'b0,stride};
 wire[12:0]page_bytes=13'd4096-{1'b0,bus_address[11:0]};
 wire[9:0]page_words=page_bytes>>3;
 reg[4:0]burst_words;
 always @* begin
  burst_words=words_to_issue>16 ? 16 : words_to_issue[4:0];
  if(burst_words>page_words)burst_words=page_words[4:0];
 end
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE;
 assign m_axi_araddr=bus_address;assign m_axi_arid=READ_ID;
 assign m_axi_arlen={3'b0,burst_words}-1'b1;assign m_axi_arsize=3;
 assign m_axi_arburst=1;assign m_axi_arvalid=state==ADDRESS;
 wire[3:0]pop_bytes=bytes_to_output>8?4'd8:bytes_to_output[3:0];
 wire[3:0]load_capacity=4'd8-{1'b0,skip_bytes};
 wire[3:0]push_bytes=bytes_to_load>load_capacity?load_capacity:bytes_to_load[3:0];
 assign out_valid=!aborting&&((fifo_count>=8)||(bytes_to_load==0&&fifo_count!=0));
 assign out_keep=out_valid?(8'hff>>(8-pop_bytes)):0;
 assign out_row_last=out_valid&&bytes_to_output<=8;
 assign out_command_last=out_row_last&&rows_left==1;
 function automatic[63:0]byte_mask(input[3:0]n);
  integer j;begin byte_mask=0;for(j=0;j<8;j=j+1)if(j<n)byte_mask[j*8+:8]=8'hff;end
 endfunction
 assign out_data=fifo[63:0]&byte_mask(pop_bytes);
 wire pop=out_valid&&out_ready;
 assign m_axi_rready=state==RECEIVE&&(aborting||fifo_count<=8||pop);
 wire received=m_axi_rvalid&&m_axi_rready;
 wire bad_response=received&&m_axi_rresp!=0;
 wire bad_protocol=received&&(m_axi_rid!=READ_ID||m_axi_rlast!=(burst_left==1));
 wire[63:0]incoming=(m_axi_rdata>>(skip_bytes*8))&byte_mask(push_bytes);
 always @* begin
  next_fifo=fifo;next_count=fifo_count;
  if(pop)begin next_fifo=next_fifo>>(pop_bytes*8);next_count=next_count-pop_bytes;end
  if(received&&!aborting&&!bad_response&&!bad_protocol)begin
   next_fifo=next_fifo|({64'b0,incoming}<<(next_count*8));next_count=next_count+push_bytes;
  end
  if(state==PREPARE||state==FAIL||aborting||bad_response||bad_protocol)begin next_fifo=0;next_count=0;end
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;done<=0;error<=0;row_base<=0;row_bytes<=0;rows_left<=0;stride<=0;
   bus_address<=0;words_to_issue<=0;bytes_to_load<=0;bytes_to_output<=0;
   skip_bytes<=0;burst_left<=0;fifo<=0;fifo_count<=0;
  end else begin
   done<=0;fifo<=next_fifo;fifo_count<=next_count;
   if(cancel&&busy&&error==0)error<=4;
   if(pop)bytes_to_output<=bytes_to_output-pop_bytes;
   case(state)
    IDLE:if(cmd_valid)begin
     error<=0;row_base<=cmd_address;row_bytes<=cmd_row_bytes;rows_left<=cmd_rows;stride<=cmd_stride;state<=PREPARE;
    end
    PREPARE:begin
     if(aborting)state<=FAIL;
     else if(!row_bytes||!rows_left||row_base<RAM_BASE||row_end>ram_end||row_end[32])begin error<=1;state<=FAIL;end
     else begin
      bus_address<={row_base[31:3],3'b0};skip_bytes<=row_base[2:0];
      words_to_issue<=({1'b0,row_bytes}+row_base[2:0]+7)>>3;
      bytes_to_load<=row_bytes;bytes_to_output<=row_bytes;state<=ADDRESS;
     end
    end
    ADDRESS:if(m_axi_arready)begin
     burst_left<=burst_words;words_to_issue<=words_to_issue-burst_words;bus_address<=bus_address+{24'b0,burst_words,3'b0};state<=RECEIVE;
    end
    RECEIVE:if(received)begin
     if(error==0)begin if(bad_protocol)error<=3;else if(bad_response)error<=2;end
     if(!aborting&&!bad_protocol&&!bad_response)begin bytes_to_load<=bytes_to_load-push_bytes;skip_bytes<=0;end
     burst_left<=burst_left-1'b1;
     if(burst_left==1)begin
      if(aborting||bad_protocol||bad_response)state<=FAIL;
      else if(words_to_issue!=0)state<=ADDRESS;
      else state<=DRAIN;
     end
    end
    DRAIN:begin
     if(aborting)state<=FAIL;
     else if(pop&&out_row_last)begin
      if(rows_left==1)state<=FINISH;
      else if(next_row[32])begin error<=1;state<=FAIL;end
      else begin row_base<=next_row[31:0];rows_left<=rows_left-1'b1;state<=PREPARE;end
     end
    end
    FINISH:begin done<=1;state<=IDLE;end
    FAIL:begin done<=1;state<=IDLE;end
    default:begin error<=3;state<=FAIL;end
   endcase
  end
 end
endmodule
