// Two AXI masters -> one memory port. Read and write grants are independent.
// Grants lock before publishing an address and persist through the final
// response. W is gated after WLAST, even if its owner's next write is queued.
module axi_dma_arbiter #(parameter ID_WIDTH=3)(
 input wire clk,rst_n,
 input wire[63:0]s_axi_awaddr,input wire[2*ID_WIDTH-1:0]s_axi_awid,
 input wire[15:0]s_axi_awlen,input wire[5:0]s_axi_awsize,input wire[3:0]s_axi_awburst,
 input wire[1:0]s_axi_awvalid,output wire[1:0]s_axi_awready,
 input wire[127:0]s_axi_wdata,input wire[15:0]s_axi_wstrb,input wire[1:0]s_axi_wlast,s_axi_wvalid,
 output wire[1:0]s_axi_wready,output wire[2*ID_WIDTH-1:0]s_axi_bid,
 output wire[3:0]s_axi_bresp,output wire[1:0]s_axi_bvalid,input wire[1:0]s_axi_bready,
 input wire[63:0]s_axi_araddr,input wire[2*ID_WIDTH-1:0]s_axi_arid,
 input wire[15:0]s_axi_arlen,input wire[5:0]s_axi_arsize,input wire[3:0]s_axi_arburst,
 input wire[1:0]s_axi_arvalid,output wire[1:0]s_axi_arready,
 output wire[127:0]s_axi_rdata,output wire[2*ID_WIDTH-1:0]s_axi_rid,
 output wire[3:0]s_axi_rresp,output wire[1:0]s_axi_rlast,s_axi_rvalid,input wire[1:0]s_axi_rready,
 output wire[31:0]m_axi_awaddr,output wire[ID_WIDTH-1:0]m_axi_awid,
 output wire[7:0]m_axi_awlen,output wire[2:0]m_axi_awsize,output wire[1:0]m_axi_awburst,
 output wire m_axi_awvalid,input wire m_axi_awready,
 output wire[63:0]m_axi_wdata,output wire[7:0]m_axi_wstrb,output wire m_axi_wlast,m_axi_wvalid,input wire m_axi_wready,
 input wire[ID_WIDTH-1:0]m_axi_bid,input wire[1:0]m_axi_bresp,input wire m_axi_bvalid,output wire m_axi_bready,
 output wire[31:0]m_axi_araddr,output wire[ID_WIDTH-1:0]m_axi_arid,
 output wire[7:0]m_axi_arlen,output wire[2:0]m_axi_arsize,output wire[1:0]m_axi_arburst,
 output wire m_axi_arvalid,input wire m_axi_arready,
 input wire[63:0]m_axi_rdata,input wire[ID_WIDTH-1:0]m_axi_rid,
 input wire[1:0]m_axi_rresp,input wire m_axi_rlast,m_axi_rvalid,output wire m_axi_rready
);
 localparam IDLE=0,ADDRESS=1,DATA=2,RESPONSE=3;
 reg[1:0]write_state,read_state;reg write_owner,read_owner,write_next,read_next;
 assign m_axi_awaddr=s_axi_awaddr[write_owner*32+:32];assign m_axi_awid=s_axi_awid[write_owner*ID_WIDTH+:ID_WIDTH];
 assign m_axi_awlen=s_axi_awlen[write_owner*8+:8];assign m_axi_awsize=s_axi_awsize[write_owner*3+:3];assign m_axi_awburst=s_axi_awburst[write_owner*2+:2];
 assign m_axi_awvalid=write_state==ADDRESS&&s_axi_awvalid[write_owner];
 assign s_axi_awready=write_state==ADDRESS?((2'b01<<write_owner)&{2{m_axi_awready}}):0;
 assign m_axi_wdata=s_axi_wdata[write_owner*64+:64];assign m_axi_wstrb=s_axi_wstrb[write_owner*8+:8];assign m_axi_wlast=s_axi_wlast[write_owner];
 assign m_axi_wvalid=write_state==DATA&&s_axi_wvalid[write_owner];
 assign s_axi_wready=write_state==DATA?((2'b01<<write_owner)&{2{m_axi_wready}}):0;
 assign s_axi_bid={2{m_axi_bid}};assign s_axi_bresp={2{m_axi_bresp}};
 assign s_axi_bvalid=write_state==RESPONSE?((2'b01<<write_owner)&{2{m_axi_bvalid}}):0;
 assign m_axi_bready=write_state==RESPONSE&&s_axi_bready[write_owner];
 assign m_axi_araddr=s_axi_araddr[read_owner*32+:32];assign m_axi_arid=s_axi_arid[read_owner*ID_WIDTH+:ID_WIDTH];
 assign m_axi_arlen=s_axi_arlen[read_owner*8+:8];assign m_axi_arsize=s_axi_arsize[read_owner*3+:3];assign m_axi_arburst=s_axi_arburst[read_owner*2+:2];
 assign m_axi_arvalid=read_state==ADDRESS&&s_axi_arvalid[read_owner];
 assign s_axi_arready=read_state==ADDRESS?((2'b01<<read_owner)&{2{m_axi_arready}}):0;
 assign s_axi_rdata={2{m_axi_rdata}};assign s_axi_rid={2{m_axi_rid}};assign s_axi_rresp={2{m_axi_rresp}};assign s_axi_rlast={2{m_axi_rlast}};
 assign s_axi_rvalid=read_state==DATA?((2'b01<<read_owner)&{2{m_axi_rvalid}}):0;
 assign m_axi_rready=read_state==DATA&&s_axi_rready[read_owner];
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin write_state<=IDLE;read_state<=IDLE;write_owner<=0;read_owner<=0;write_next<=0;read_next<=0;end
  else begin
   case(write_state)
    IDLE:if(|s_axi_awvalid)begin write_owner<=(&s_axi_awvalid)?write_next:s_axi_awvalid[1];write_state<=ADDRESS;end
    ADDRESS:if(m_axi_awvalid&&m_axi_awready)write_state<=DATA;
    DATA:if(m_axi_wvalid&&m_axi_wready&&m_axi_wlast)write_state<=RESPONSE;
    RESPONSE:if(m_axi_bvalid&&m_axi_bready)begin write_state<=IDLE;write_next<=~write_owner;end
   endcase
   case(read_state)
    IDLE:if(|s_axi_arvalid)begin read_owner<=(&s_axi_arvalid)?read_next:s_axi_arvalid[1];read_state<=ADDRESS;end
    ADDRESS:if(m_axi_arvalid&&m_axi_arready)read_state<=DATA;
    DATA:if(m_axi_rvalid&&m_axi_rready&&m_axi_rlast)begin read_state<=IDLE;read_next<=~read_owner;end
   endcase
  end
 end
endmodule
