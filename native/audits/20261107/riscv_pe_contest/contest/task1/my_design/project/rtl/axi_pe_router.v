module axi_pe_router #(
    parameter integer ID_WIDTH = 4,
    parameter [31:0] PE_BASE = 32'h9000_0000,
    parameter [31:0] PE_MASK = 32'hFFFF_F000
)(
    input  wire                   clk,
    input  wire                   rst_n,

    // ============================================================
    // Upstream AXI master
    // Connected to VeeR mux_axi_*
    // ============================================================

    // Write address
    input  wire [31:0]            s_axi_awaddr,
    input  wire [ID_WIDTH-1:0]    s_axi_awid,
    input  wire [7:0]             s_axi_awlen,
    input  wire [2:0]             s_axi_awsize,
    input  wire [1:0]             s_axi_awburst,
    input  wire                   s_axi_awvalid,
    output wire                   s_axi_awready,

    // Write data
    input  wire [63:0]            s_axi_wdata,
    input  wire [7:0]             s_axi_wstrb,
    input  wire                   s_axi_wlast,
    input  wire                   s_axi_wvalid,
    output wire                   s_axi_wready,

    // Write response
    output wire [1:0]             s_axi_bresp,
    output wire [ID_WIDTH-1:0]    s_axi_bid,
    output wire                   s_axi_bvalid,
    input  wire                   s_axi_bready,

    // Read address
    input  wire [31:0]            s_axi_araddr,
    input  wire [ID_WIDTH-1:0]    s_axi_arid,
    input  wire [7:0]             s_axi_arlen,
    input  wire [2:0]             s_axi_arsize,
    input  wire [1:0]             s_axi_arburst,
    input  wire                   s_axi_arvalid,
    output wire                   s_axi_arready,

    // Read data
    output wire [63:0]            s_axi_rdata,
    output wire [1:0]             s_axi_rresp,
    output wire [ID_WIDTH-1:0]    s_axi_rid,
    output wire                   s_axi_rlast,
    output wire                   s_axi_rvalid,
    input  wire                   s_axi_rready,


    // ============================================================
    // Memory-side AXI
    //
    // This replaces the original mux_axi connection toward
    // axi_lsu_dma_bridge / lmem.
    // ============================================================

    // Write address
    output wire [31:0]            mem_axi_awaddr,
    output wire [ID_WIDTH-1:0]    mem_axi_awid,
    output wire [7:0]             mem_axi_awlen,
    output wire [2:0]             mem_axi_awsize,
    output wire [1:0]             mem_axi_awburst,
    output wire                   mem_axi_awvalid,
    input  wire                   mem_axi_awready,

    // Write data
    output wire [63:0]            mem_axi_wdata,
    output wire [7:0]             mem_axi_wstrb,
    output wire                   mem_axi_wlast,
    output wire                   mem_axi_wvalid,
    input  wire                   mem_axi_wready,

    // Write response
    input  wire [1:0]             mem_axi_bresp,
    input  wire [ID_WIDTH-1:0]    mem_axi_bid,
    input  wire                   mem_axi_bvalid,
    output wire                   mem_axi_bready,

    // Read address
    output wire [31:0]            mem_axi_araddr,
    output wire [ID_WIDTH-1:0]    mem_axi_arid,
    output wire [7:0]             mem_axi_arlen,
    output wire [2:0]             mem_axi_arsize,
    output wire [1:0]             mem_axi_arburst,
    output wire                   mem_axi_arvalid,
    input  wire                   mem_axi_arready,

    // Read data
    input  wire [63:0]            mem_axi_rdata,
    input  wire [1:0]             mem_axi_rresp,
    input  wire [ID_WIDTH-1:0]    mem_axi_rid,
    input  wire                   mem_axi_rlast,
    input  wire                   mem_axi_rvalid,
    output wire                   mem_axi_rready,


    // ============================================================
    // PE-side AXI
    // Connected to pe_axi_wrapper
    // ============================================================

    // Write address
    output wire [31:0]            pe_axi_awaddr,
    output wire [ID_WIDTH-1:0]    pe_axi_awid,
    output wire [7:0]             pe_axi_awlen,
    output wire [2:0]             pe_axi_awsize,
    output wire [1:0]             pe_axi_awburst,
    output wire                   pe_axi_awvalid,
    input  wire                   pe_axi_awready,

    // Write data
    output wire [63:0]            pe_axi_wdata,
    output wire [7:0]             pe_axi_wstrb,
    output wire                   pe_axi_wlast,
    output wire                   pe_axi_wvalid,
    input  wire                   pe_axi_wready,

    // Write response
    input  wire [1:0]             pe_axi_bresp,
    input  wire [ID_WIDTH-1:0]    pe_axi_bid,
    input  wire                   pe_axi_bvalid,
    output wire                   pe_axi_bready,

    // Read address
    output wire [31:0]            pe_axi_araddr,
    output wire [ID_WIDTH-1:0]    pe_axi_arid,
    output wire [7:0]             pe_axi_arlen,
    output wire [2:0]             pe_axi_arsize,
    output wire [1:0]             pe_axi_arburst,
    output wire                   pe_axi_arvalid,
    input  wire                   pe_axi_arready,

    // Read data
    input  wire [63:0]            pe_axi_rdata,
    input  wire [1:0]             pe_axi_rresp,
    input  wire [ID_WIDTH-1:0]    pe_axi_rid,
    input  wire                   pe_axi_rlast,
    input  wire                   pe_axi_rvalid,
    output wire                   pe_axi_rready
);


    // ============================================================
    // Address decode
    //
    // 0x9000_0000 - 0x9000_0FFF -> PE
    // everything else            -> original memory path
    // ============================================================

    wire aw_to_pe;
    wire ar_to_pe;

    assign aw_to_pe = ((s_axi_awaddr & PE_MASK) == (PE_BASE & PE_MASK));

    assign ar_to_pe = ((s_axi_araddr & PE_MASK) == (PE_BASE & PE_MASK));


    // ============================================================
    // Write transaction routing state
    //
    // AXI AW and W are independent.
    // Therefore the destination selected by AW must be remembered
    // until the corresponding B response completes.
    // ============================================================

    reg wr_active;
    reg wr_to_pe;


    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_active <= 1'b0;
            wr_to_pe  <= 1'b0;
        end
        else begin

            // New write address accepted
            if (!wr_active && s_axi_awvalid && s_axi_awready) begin
                wr_active <= 1'b1;
                wr_to_pe  <= aw_to_pe;
            end

            // Write transaction completed
            if (wr_active && s_axi_bvalid && s_axi_bready) begin
                wr_active <= 1'b0;
            end

        end
    end


    // ============================================================
    // AW channel
    // ============================================================

    assign s_axi_awready = !wr_active ? (aw_to_pe ? pe_axi_awready : mem_axi_awready) : 1'b0;


    assign pe_axi_awvalid = (!wr_active) && s_axi_awvalid && aw_to_pe;

    assign mem_axi_awvalid = (!wr_active) && s_axi_awvalid && !aw_to_pe;


    // AW payload is broadcast.
    // Only selected AWVALID is asserted.
    assign pe_axi_awaddr  = s_axi_awaddr;
    assign pe_axi_awid    = s_axi_awid;
    assign pe_axi_awlen   = s_axi_awlen;
    assign pe_axi_awsize  = s_axi_awsize;
    assign pe_axi_awburst = s_axi_awburst;

    assign mem_axi_awaddr  = s_axi_awaddr;
    assign mem_axi_awid    = s_axi_awid;
    assign mem_axi_awlen   = s_axi_awlen;
    assign mem_axi_awsize  = s_axi_awsize;
    assign mem_axi_awburst = s_axi_awburst;


    // ============================================================
    // W channel
    //
    // W is not accepted until its AW has been accepted.
    //
    // This is legal AXI behavior. If the master presents WVALID
    // earlier, it simply waits for WREADY.
    // ============================================================

    assign pe_axi_wvalid = wr_active && wr_to_pe && s_axi_wvalid;

    assign mem_axi_wvalid = wr_active && !wr_to_pe && s_axi_wvalid;


    assign s_axi_wready = wr_active ? (wr_to_pe ? pe_axi_wready : mem_axi_wready) : 1'b0;


    assign pe_axi_wdata = s_axi_wdata;
    assign pe_axi_wstrb = s_axi_wstrb;
    assign pe_axi_wlast = s_axi_wlast;

    assign mem_axi_wdata = s_axi_wdata;
    assign mem_axi_wstrb = s_axi_wstrb;
    assign mem_axi_wlast = s_axi_wlast;


    // ============================================================
    // B response
    // ============================================================

    assign s_axi_bvalid = wr_active ? (wr_to_pe ? pe_axi_bvalid : mem_axi_bvalid) : 1'b0;

    assign s_axi_bresp = wr_active ? (wr_to_pe ? pe_axi_bresp : mem_axi_bresp) : 2'b00;

    assign s_axi_bid = wr_active ? (wr_to_pe ? pe_axi_bid : mem_axi_bid) : {ID_WIDTH{1'b0}};


    assign pe_axi_bready = wr_active && wr_to_pe && s_axi_bready;

    assign mem_axi_bready = wr_active && !wr_to_pe && s_axi_bready;


    // ============================================================
    // Read transaction routing state
    //
    // Destination selected by AR is remembered until RLAST.
    // ============================================================

    reg rd_active;
    reg rd_to_pe;


    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_active <= 1'b0;
            rd_to_pe  <= 1'b0;
        end
        else begin

            // New read address accepted
            if (!rd_active && s_axi_arvalid && s_axi_arready) begin
                rd_active <= 1'b1;
                rd_to_pe  <= ar_to_pe;
            end


            // Read transaction completed
            if (rd_active && s_axi_rvalid && s_axi_rready && s_axi_rlast) begin
                rd_active <= 1'b0;
            end

        end
    end


    // ============================================================
    // AR channel
    // ============================================================

    assign s_axi_arready = !rd_active ? (ar_to_pe ? pe_axi_arready : mem_axi_arready) : 1'b0;


    assign pe_axi_arvalid = (!rd_active) && s_axi_arvalid && ar_to_pe;

    assign mem_axi_arvalid = (!rd_active) && s_axi_arvalid && !ar_to_pe;


    assign pe_axi_araddr  = s_axi_araddr;
    assign pe_axi_arid    = s_axi_arid;
    assign pe_axi_arlen   = s_axi_arlen;
    assign pe_axi_arsize  = s_axi_arsize;
    assign pe_axi_arburst = s_axi_arburst;

    assign mem_axi_araddr  = s_axi_araddr;
    assign mem_axi_arid    = s_axi_arid;
    assign mem_axi_arlen   = s_axi_arlen;
    assign mem_axi_arsize  = s_axi_arsize;
    assign mem_axi_arburst = s_axi_arburst;


    // ============================================================
    // R channel
    // ============================================================

    assign s_axi_rvalid = rd_active ? (rd_to_pe ? pe_axi_rvalid : mem_axi_rvalid) : 1'b0;

    assign s_axi_rdata = rd_active ? (rd_to_pe ? pe_axi_rdata : mem_axi_rdata) : 64'b0;

    assign s_axi_rresp = rd_active ? (rd_to_pe ? pe_axi_rresp : mem_axi_rresp) : 2'b00;

    assign s_axi_rid = rd_active ? (rd_to_pe ? pe_axi_rid : mem_axi_rid) : {ID_WIDTH{1'b0}};

    assign s_axi_rlast = rd_active ? (rd_to_pe ? pe_axi_rlast : mem_axi_rlast) : 1'b0;


    assign pe_axi_rready = rd_active && rd_to_pe && s_axi_rready;

    assign mem_axi_rready = rd_active && !rd_to_pe && s_axi_rready;


endmodule