// Row-major raw INT8 GEMM -> exact row-major INT32, including affine zeros.
// B can be KxN or stored NxK. Cache K=ROWS*16, reuse weights across M tiles,
// accumulate all SRAM slots on chip, and move partial sums only per K block.
// Transposed loads on four-row arrays split a beat to avoid bank conflicts.
module axi_affine_gemm #(
 parameter ROWS=8,COLS=32,LANES=4,ID_WIDTH=3,
 parameter BANKS=ROWS*COLS,MAX_K=ROWS*16,
 parameter[ID_WIDTH-1:0]DMA_ID=0,
 parameter[31:0]RAM_BASE=32'h80000000,RAM_BYTES=32'h04000000
)(
 input wire clk,rst_n,
 input wire cmd_valid,output wire cmd_ready,
 input wire[31:0]cmd_a,cmd_b,cmd_c,cmd_m,cmd_n,cmd_k,
 input wire cmd_trans_b,input wire[7:0]cmd_a_zero,cmd_b_zero,
 input wire cancel,output wire busy,output reg done,output reg[7:0]error,
 output reg[31:0]cycles,output_bytes,
 output reg[BANKS*4-1:0]bank_addr,output reg[BANKS-1:0]bank_ce,bank_we,
 output reg[BANKS*8-1:0]bank_write,
 output wire tile_valid,output reg[ROWS*LANES*8-1:0]a_raw,output wire[7:0]a_zero,
 output wire[COLS*8-1:0]b_zero,input wire dot_valid,input wire[COLS*LANES*32-1:0]dot_out,
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
 localparam IDLE=0,CHECK=1,CLEAR_B=2,B_START=3,B_LOAD=4,A_START=5,A_LOAD=6,
  P_START=7,P_LOAD=8,COMPUTE=9,DRAIN=10,W_START=11,W_RUN=12,NEXT=13,COMPLETE=14,B_FLUSH=15;
 reg[3:0]state;
 reg[31:0]a_base,b_base,c_base,m,n,k,m_block,n_block,k_block;
 reg trans_b;reg[7:0]az,bz;
 reg[3:0]clear_slot;
 reg[4:0]launched,returned;
 reg[31:0]load_row,load_position,write_row,write_col;
 reg b_pending;reg[63:0]b_hold_data;reg[7:0]b_hold_keep;
 reg[31:0]b_hold_row,b_hold_position;
 reg[7:0]activation[0:LANES*MAX_K-1];
 reg signed[31:0]partial[0:LANES*COLS-1];
 wire[31:0]active_m=m-m_block>LANES?LANES:m-m_block;
 wire[31:0]active_n=n-n_block>COLS?COLS:n-n_block;
 wire[31:0]active_k=k-k_block>MAX_K?MAX_K:k-k_block;
 wire[31:0]groups=(active_k+ROWS-1)/ROWS;
 wire[31:0]c_address=c_base+((m_block*n+n_block)<<2);
 wire stopping=cancel||error!=0;
 wire[63:0]a_end={32'b0,a_base}+{32'b0,m}*{32'b0,k};
 wire[63:0]b_end={32'b0,b_base}+{32'b0,k}*{32'b0,n};
 wire[63:0]c_end={32'b0,c_base}+({32'b0,m}*{32'b0,n}<<2);
 wire[63:0]ram_end={32'b0,RAM_BASE}+{32'b0,RAM_BYTES};
 wire bad_command=!m||!n||!k||m>65535||n>65535||k>32767||
  a_base<RAM_BASE||b_base<RAM_BASE||c_base<RAM_BASE||a_end>ram_end||b_end>ram_end||c_end>ram_end||
  ({32'b0,a_base}<c_end&&{32'b0,c_base}<a_end)||({32'b0,b_base}<c_end&&{32'b0,c_base}<b_end);
 wire read_ready,read_busy,read_done,read_valid;wire[3:0]read_error;
 wire[63:0]read_data;wire[7:0]read_keep;wire read_row_last,read_command_last;
 wire reading=state==B_LOAD||state==A_LOAD||state==P_LOAD;
 wire split_b=ROWS<8&&trans_b;
 wire read_accept=reading&&!stopping&&!(state==B_LOAD&&split_b&&b_pending);
 wire read_start=(state==B_START||state==A_START||state==P_START)&&!stopping&&read_ready;
 wire[31:0]read_address=state==B_START?(trans_b?b_base+n_block*k+k_block:b_base+k_block*n+n_block):state==A_START?a_base+m_block*k+k_block:c_address;
 wire[31:0]read_row_bytes=state==B_START?(trans_b?active_k:active_n):state==A_START?active_k:active_n<<2;
 wire[31:0]read_rows=state==B_START?(trans_b?active_n:active_k):active_m;
 wire[31:0]read_stride=state==B_START?(trans_b?k:n):state==A_START?k:n<<2;
 axi_dma_reader #(.ID_WIDTH(ID_WIDTH),.READ_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))reader(
  .clk(clk),.rst_n(rst_n),.cmd_valid(read_start),.cmd_ready(read_ready),.cmd_address(read_address),.cmd_row_bytes(read_row_bytes),.cmd_rows(read_rows),.cmd_stride(read_stride),
  .cancel(reading&&stopping),.busy(read_busy),.done(read_done),.error(read_error),
  .out_valid(read_valid),.out_ready(read_accept),.out_data(read_data),.out_keep(read_keep),.out_row_last(read_row_last),.out_command_last(read_command_last),
  .m_axi_araddr(m_axi_araddr),.m_axi_arid(m_axi_arid),.m_axi_arlen(m_axi_arlen),.m_axi_arsize(m_axi_arsize),.m_axi_arburst(m_axi_arburst),.m_axi_arvalid(m_axi_arvalid),.m_axi_arready(m_axi_arready),
  .m_axi_rdata(m_axi_rdata),.m_axi_rid(m_axi_rid),.m_axi_rresp(m_axi_rresp),.m_axi_rlast(m_axi_rlast),.m_axi_rvalid(m_axi_rvalid),.m_axi_rready(m_axi_rready));
 wire write_ready,write_busy,write_done,write_accept;wire[3:0]write_error;
 wire write_start=state==W_START&&!stopping&&write_ready;
 wire[31:0]write_words=active_n-write_col>=2?2:1;
 wire write_row_last=write_col+write_words>=active_n;
 wire[63:0]write_data={write_words==2?partial[write_row*COLS+write_col+1]:32'b0,partial[write_row*COLS+write_col]};
 axi_dma_writer #(.ID_WIDTH(ID_WIDTH),.WRITE_ID(DMA_ID),.RAM_BASE(RAM_BASE),.RAM_BYTES(RAM_BYTES))writer(
  .clk(clk),.rst_n(rst_n),.cmd_valid(write_start),.cmd_ready(write_ready),.cmd_address(c_address),.cmd_row_bytes(active_n<<2),.cmd_rows(active_m),.cmd_stride(n<<2),
  .cancel(state==W_RUN&&stopping),.busy(write_busy),.done(write_done),.error(write_error),
  .in_valid(state==W_RUN&&!stopping&&write_row<active_m),.in_ready(write_accept),.in_data(write_data),.in_keep(write_words==2?8'hff:8'h0f),
  .in_row_last(write_row_last),.in_command_last(write_row_last&&write_row+1==active_m),
  .m_axi_awaddr(m_axi_awaddr),.m_axi_awid(m_axi_awid),.m_axi_awlen(m_axi_awlen),.m_axi_awsize(m_axi_awsize),.m_axi_awburst(m_axi_awburst),.m_axi_awvalid(m_axi_awvalid),.m_axi_awready(m_axi_awready),
  .m_axi_wdata(m_axi_wdata),.m_axi_wstrb(m_axi_wstrb),.m_axi_wlast(m_axi_wlast),.m_axi_wvalid(m_axi_wvalid),.m_axi_wready(m_axi_wready),.m_axi_bid(m_axi_bid),.m_axi_bresp(m_axi_bresp),.m_axi_bvalid(m_axi_bvalid),.m_axi_bready(m_axi_bready));
 assign busy=state!=IDLE;assign cmd_ready=state==IDLE;
 assign tile_valid=state==COMPUTE&&!stopping&&launched<groups;
 assign a_zero=az;assign b_zero={COLS{bz}};
 integer j,bank,kk,cc,r,l;
 reg[3:0]read_bytes;
 wire[63:0]weight_data=split_b?b_hold_data:read_data;
 wire[7:0]weight_keep=split_b?b_hold_keep&8'h0f:read_keep;
 wire[31:0]weight_position=split_b?b_hold_position:load_position;
 wire[31:0]weight_row=split_b?b_hold_row:load_row;
 always @*begin
  read_bytes=0;for(j=0;j<8;j=j+1)read_bytes=read_bytes+read_keep[j];
  bank_addr=0;bank_ce=0;bank_we=0;bank_write={BANKS{bz}};a_raw={ROWS*LANES{az}};
  if(state==CLEAR_B&&!stopping)begin
   for(j=0;j<BANKS;j=j+1)begin bank_ce[j]=1;bank_we[j]=1;bank_addr[j*4+:4]=clear_slot;end
  end else if(!stopping&&((state==B_LOAD&&!split_b&&read_valid)||(split_b&&b_pending&&(state==B_LOAD||state==B_FLUSH))))begin
   for(j=0;j<8;j=j+1)if(weight_keep[j])begin
    kk=trans_b?weight_position+j:weight_row;cc=trans_b?weight_row:weight_position+j;
    bank=(kk%ROWS)*COLS+cc;
    bank_addr[bank*4+:4]=kk/ROWS;bank_ce[bank]=1;bank_we[bank]=1;bank_write[bank*8+:8]=weight_data[j*8+:8];
   end
  end else if(tile_valid)begin
   for(j=0;j<BANKS;j=j+1)begin bank_addr[j*4+:4]=launched[3:0];bank_ce[j]=1;end
   for(r=0;r<ROWS;r=r+1)for(l=0;l<LANES;l=l+1)
    if(l<active_m&&launched*ROWS+r<active_k)a_raw[(r*LANES+l)*8+:8]=activation[l*MAX_K+launched*ROWS+r];
  end
 end
 integer x,y;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;done<=0;error<=0;cycles<=0;output_bytes<=0;
   a_base<=0;b_base<=0;c_base<=0;m<=0;n<=0;k<=0;m_block<=0;n_block<=0;k_block<=0;az<=0;bz<=0;trans_b<=0;
   clear_slot<=0;launched<=0;returned<=0;load_row<=0;load_position<=0;write_row<=0;write_col<=0;
   b_pending<=0;b_hold_data<=0;b_hold_keep<=0;b_hold_row<=0;b_hold_position<=0;
   for(x=0;x<LANES*COLS;x=x+1)partial[x]<=0;
  end else begin
   done<=0;if(busy)cycles<=cycles+1'b1;
   if(busy&&cancel&&error==0)error<=4;
   if(b_pending)begin
    if(stopping||b_hold_keep[7:4]==0)b_pending<=0;
    else begin b_hold_data<=b_hold_data>>32;b_hold_keep<=b_hold_keep>>4;b_hold_position<=b_hold_position+4;end
   end
   if(state==B_LOAD&&split_b&&read_valid&&read_accept)begin
    b_pending<=1;b_hold_data<=read_data;b_hold_keep<=read_keep;b_hold_row<=load_row;b_hold_position<=load_position;
   end
   if(reading&&read_valid&&read_accept)begin
    if(read_row_last)begin load_row<=load_row+1'b1;load_position<=0;end
    else load_position<=load_position+read_bytes;
    if(state==A_LOAD)for(x=0;x<8;x=x+1)if(read_keep[x])activation[load_row*MAX_K+load_position+x]<=read_data[x*8+:8];
    if(state==P_LOAD)for(x=0;x<2;x=x+1)if(read_keep[x*4])partial[load_row*COLS+(load_position>>2)+x]<=read_data[x*32+:32];
   end
   case(state)
    IDLE:if(cmd_valid)begin
     state<=CHECK;a_base<=cmd_a;b_base<=cmd_b;c_base<=cmd_c;m<=cmd_m;n<=cmd_n;k<=cmd_k;az<=cmd_a_zero;bz<=cmd_b_zero;trans_b<=cmd_trans_b;
     m_block<=0;n_block<=0;k_block<=0;error<=0;cycles<=0;output_bytes<=0;
    end
    CHECK:if(stopping)state<=COMPLETE;else if(bad_command)begin error<=1;state<=COMPLETE;end else begin state<=CLEAR_B;clear_slot<=0;end
    CLEAR_B:if(stopping)state<=COMPLETE;else if(clear_slot==15)state<=B_START;else clear_slot<=clear_slot+1'b1;
    B_START,A_START,P_START:begin
     if(stopping)state<=COMPLETE;
     else if(read_start)begin
      load_row<=0;load_position<=0;
      case(state)
       B_START:begin state<=B_LOAD;b_pending<=0;end
       A_START:begin
        state<=A_LOAD;launched<=0;returned<=0;
        if(k_block==0)for(x=0;x<LANES*COLS;x=x+1)partial[x]<=0;
       end
       P_START:state<=P_LOAD;
      endcase
     end
    end
    B_LOAD,A_LOAD,P_LOAD:begin
     if(read_error!=0&&error==0)error<={4'h1,read_error};
     if(read_done)begin
      if(stopping||read_error!=0)state<=COMPLETE;
      else case(state)B_LOAD:state<=b_pending?B_FLUSH:A_START;A_LOAD:state<=k_block==0?COMPUTE:P_START;P_LOAD:state<=COMPUTE;endcase
     end
    end
    B_FLUSH:if(stopping)state<=COMPLETE;else if(!b_pending)state<=A_START;
    COMPUTE:begin
     if(stopping)state<=COMPLETE;
     else if(tile_valid)begin launched<=launched+1'b1;if(launched+1>=groups)state<=DRAIN;end
    end
    DRAIN:if(stopping)state<=COMPLETE;
    W_START:if(stopping)state<=COMPLETE;else if(write_start)begin state<=W_RUN;write_row<=0;write_col<=0;end
    W_RUN:begin
     if(write_error!=0&&error==0)error<={4'h2,write_error};
     if(write_accept&&!stopping&&write_row<active_m)begin
      output_bytes<=output_bytes+(write_words<<2);
      if(write_row_last)begin write_row<=write_row+1'b1;write_col<=0;end else write_col<=write_col+write_words;
     end
     if(write_done)state<=stopping||write_error!=0?COMPLETE:NEXT;
    end
    NEXT:begin
     if(stopping)state<=COMPLETE;
     else if(m_block+LANES<m)begin m_block<=m_block+LANES;state<=A_START;end
     else begin
      m_block<=0;clear_slot<=0;
      if(k_block+MAX_K<k)begin k_block<=k_block+MAX_K;state<=CLEAR_B;end
      else begin
       k_block<=0;
       if(n_block+COLS<n)begin n_block<=n_block+COLS;state<=CLEAR_B;end else state<=COMPLETE;
      end
     end
    end
    COMPLETE:begin done<=1;state<=IDLE;end
    default:begin error<=8'hff;state<=COMPLETE;end
   endcase
   if(dot_valid&&(state==COMPUTE||state==DRAIN)&&!stopping)begin
    for(x=0;x<LANES;x=x+1)for(y=0;y<COLS;y=y+1)partial[x*COLS+y]<=partial[x*COLS+y]+$signed(dot_out[(y*LANES+x)*32+:32]);
    returned<=returned+1'b1;if(returned+1==groups)state<=W_START;
   end
  end
 end
endmodule
