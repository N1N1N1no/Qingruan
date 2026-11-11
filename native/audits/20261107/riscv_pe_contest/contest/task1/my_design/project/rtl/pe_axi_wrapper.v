`timescale 1ns/1ps

module sram_multiplier_system #(
    parameter ID_WIDTH = 4,
    parameter M        = 16,
    parameter N        = 16,
    parameter NUM_PE   = M * N
)(
    input  wire                    clk,
    input  wire                    rst_n,

    // ============================================================
    // AXI Write Address Channel
    // ============================================================
    input  wire [31:0]             s_axi_awaddr,
    input  wire [ID_WIDTH-1:0]     s_axi_awid,
    input  wire [7:0]              s_axi_awlen,
    input  wire [2:0]              s_axi_awsize,
    input  wire [1:0]              s_axi_awburst,
    input  wire                    s_axi_awvalid,
    output wire                    s_axi_awready,

    // ============================================================
    // AXI Write Data Channel
    // ============================================================
    input  wire [63:0]             s_axi_wdata,
    input  wire [7:0]              s_axi_wstrb,
    input  wire                    s_axi_wlast,
    input  wire                    s_axi_wvalid,
    output wire                    s_axi_wready,

    // ============================================================
    // AXI Write Response Channel
    // ============================================================
    output reg  [1:0]              s_axi_bresp,
    output reg  [ID_WIDTH-1:0]     s_axi_bid,
    output reg                     s_axi_bvalid,
    input  wire                    s_axi_bready,

    // ============================================================
    // AXI Read Address Channel
    // ============================================================
    input  wire [31:0]             s_axi_araddr,
    input  wire [ID_WIDTH-1:0]     s_axi_arid,
    input  wire [7:0]              s_axi_arlen,
    input  wire [2:0]              s_axi_arsize,
    input  wire [1:0]              s_axi_arburst,
    input  wire                    s_axi_arvalid,
    output wire                    s_axi_arready,

    // ============================================================
    // AXI Read Data Channel
    // ============================================================
    output reg  [63:0]             s_axi_rdata,
    output reg  [1:0]              s_axi_rresp,
    output reg  [ID_WIDTH-1:0]     s_axi_rid,
    output reg                     s_axi_rlast,
    output reg                     s_axi_rvalid,
    input  wire                    s_axi_rready
);


    // ============================================================
    // Width definition
    // ============================================================

    localparam WEIGHT_WIDTH = N * 8;
    localparam DATA_WIDTH   = M * 32;
    localparam PSUM_WIDTH   = N * 128;


    // ============================================================
    // MMIO Register Map
    //
    // 0x000 : SRAM_ADDR
    // 0x008 : SRAM_CE
    // 0x010 : SRAM_WE       one-cycle pulse
    //
    // 0x020 - 0x02F : WEIGHT_IN
    //                  128 bit = 2 x 64 bit
    //
    // 0x100 - 0x13F : DATA_IN
    //                  512 bit = 8 x 64 bit
    //
    // 0x200 - 0x2FF : PSUM_IN
    //                  2048 bit = 32 x 64 bit
    //
    // 0x400 - 0x4FF : PSUM_OUT
    //                  2048 bit = 32 x 64 bit
    //
    //
    // For M=N=16:
    //
    // WEIGHT:
    //
    // 0x020 -> weight_in[ 63:  0]
    // 0x028 -> weight_in[127: 64]
    //
    //
    // DATA:
    //
    // 0x100 -> data_in[ 63:  0]
    // 0x108 -> data_in[127: 64]
    // ...
    // 0x138 -> data_in[511:448]
    //
    //
    // PSUM INPUT:
    //
    // 0x200 -> psum_in[  63:   0]
    // 0x208 -> psum_in[ 127:  64]
    // ...
    // 0x2F8 -> psum_in[2047:1984]
    //
    //
    // PSUM OUTPUT:
    //
    // 0x400 -> psum_out[  63:   0]
    // 0x408 -> psum_out[ 127:  64]
    // ...
    // 0x4F8 -> psum_out[2047:1984]
    //
    // ============================================================


    // ============================================================
    // PE Array control / input registers
    // ============================================================

    reg [3:0]               sram_addr_reg;
    reg [M-1:0]             sram_ce_reg;
    reg [M-1:0]             sram_we_reg;

    reg [WEIGHT_WIDTH-1:0]  weight_reg;
    reg [DATA_WIDTH-1:0]    data_reg;
    reg [PSUM_WIDTH-1:0]    psum_in_reg;


    // ============================================================
    // PE Array output
    //
    // No additional result register.
    //
    // Software must ensure the PE array output is stable before
    // reading the complete result.
    // ============================================================

    wire [PSUM_WIDTH-1:0] psum_out_wire;


    // ============================================================
    // PE Array
    // ============================================================

    pe_array #(
        .M      (M),
        .N      (N),
        .NUM_PE (NUM_PE)
    ) u_pe_array (
        .clk        (clk),
        .rst_n      (rst_n),

        .sram_addr  (sram_addr_reg),
        .sram_ce    (sram_ce_reg),
        .sram_we    (sram_we_reg),
        .weight_in  (weight_reg),

        .data_in    (data_reg),
        .psum_in    (psum_in_reg),

        .psum_out   (psum_out_wire)
    );


    // ============================================================
    // AXI Write Channel Buffer
    //
    // AW and W are independent AXI channels.
    // Hold them until both have arrived.
    // ============================================================

    reg                     aw_pending;
    reg [31:0]              awaddr_reg;
    reg [ID_WIDTH-1:0]      awid_reg;
    reg [7:0]               awlen_reg;

    reg                     w_pending;
    reg [63:0]              wdata_reg;
    reg [7:0]               wstrb_reg;
    reg                     wlast_reg;


    assign s_axi_awready =
        (!aw_pending) && (!s_axi_bvalid);

    assign s_axi_wready =
        (!w_pending) && (!s_axi_bvalid);


    // ============================================================
    // 64-bit byte write helper
    // ============================================================

    function [63:0] write64;

        input [63:0] old_data;
        input [63:0] new_data;
        input [7:0]  strb;

        integer i;

        begin

            write64 = old_data;

            for (i = 0; i < 8; i = i + 1) begin

                if (strb[i])
                    write64[i*8 +: 8] =
                        new_data[i*8 +: 8];

            end

        end

    endfunction


    // ============================================================
    // AXI Write Logic
    // ============================================================

    integer write_index;

    always @(posedge clk or negedge rst_n) begin

        if (!rst_n) begin

            aw_pending <= 1'b0;
            awaddr_reg <= 32'b0;
            awid_reg   <= {ID_WIDTH{1'b0}};
            awlen_reg  <= 8'b0;

            w_pending  <= 1'b0;
            wdata_reg  <= 64'b0;
            wstrb_reg  <= 8'b0;
            wlast_reg  <= 1'b0;

            s_axi_bresp  <= 2'b00;
            s_axi_bid    <= {ID_WIDTH{1'b0}};
            s_axi_bvalid <= 1'b0;

            sram_addr_reg <= 4'b0;
            sram_ce_reg   <= {M{1'b0}};
            sram_we_reg   <= {M{1'b0}};

            weight_reg  <= {WEIGHT_WIDTH{1'b0}};
            data_reg    <= {DATA_WIDTH{1'b0}};
            psum_in_reg <= {PSUM_WIDTH{1'b0}};

        end

        else begin


            // ====================================================
            // SRAM WE automatically returns to zero.
            //
            // Software only needs to write SRAM_WE once for
            // each SRAM write operation.
            // ====================================================

            sram_we_reg <= {M{1'b0}};


            // ====================================================
            // Capture AXI AW
            // ====================================================

            if (s_axi_awvalid && s_axi_awready) begin

                aw_pending <= 1'b1;

                awaddr_reg <= s_axi_awaddr;
                awid_reg   <= s_axi_awid;
                awlen_reg  <= s_axi_awlen;

            end


            // ====================================================
            // Capture AXI W
            // ====================================================

            if (s_axi_wvalid && s_axi_wready) begin

                w_pending <= 1'b1;

                wdata_reg <= s_axi_wdata;
                wstrb_reg <= s_axi_wstrb;
                wlast_reg <= s_axi_wlast;

            end


            // ====================================================
            // Execute AXI Write
            // ====================================================

            if ((!s_axi_bvalid) &&
                aw_pending &&
                w_pending) begin

                aw_pending <= 1'b0;
                w_pending  <= 1'b0;

                s_axi_bid    <= awid_reg;
                s_axi_bvalid <= 1'b1;


                // ------------------------------------------------
                // Only single-beat MMIO transaction supported
                // ------------------------------------------------

                if ((awlen_reg != 8'd0) ||
                    (!wlast_reg)) begin

                    s_axi_bresp <= 2'b10;     // SLVERR

                end

                else begin

                    s_axi_bresp <= 2'b00;     // OKAY


                    // ============================================
                    // SRAM_ADDR
                    // ============================================

                    if (awaddr_reg[11:0] == 12'h000) begin

                        if (wstrb_reg[0]) sram_addr_reg <= wdata_reg[3:0];

                    end


                    // ============================================
                    // SRAM_CE
                    // ============================================

                    else if (awaddr_reg[11:0] == 12'h008) begin

                        for (integer b = 0; b < M; b = b + 1)
                            if (wstrb_reg[b/8]) sram_ce_reg[b] <= wdata_reg[b];

                    end


                    // ============================================
                    // SRAM_WE
                    //
                    // one-cycle pulse
                    // ============================================

                    else if (awaddr_reg[11:0] == 12'h010) begin

                        for (integer b = 0; b < M; b = b + 1)
                            sram_we_reg[b] <= wstrb_reg[b/8] && wdata_reg[b];

                    end


                    // ============================================
                    // WEIGHT BUFFER
                    //
                    // M=N=16:
                    //
                    // 0x020 -> [63:0]
                    // 0x028 -> [127:64]
                    // ============================================

                    else if ((awaddr_reg[11:0] >= 12'h020) &&
                             (awaddr_reg[11:0] <
                              12'h020 +
                              ((WEIGHT_WIDTH + 63)/64)*8)) begin

                        write_index =
                            (awaddr_reg[11:0] - 12'h020) >> 3;

                        weight_reg[
                            write_index*64 +: 64
                        ] <= write64(
                            weight_reg[
                                write_index*64 +: 64
                            ],
                            wdata_reg,
                            wstrb_reg
                        );

                    end


                    // ============================================
                    // DATA INPUT BUFFER
                    //
                    // M=N=16:
                    //
                    // 0x100 -> [63:0]
                    // ...
                    // 0x138 -> [511:448]
                    // ============================================

                    else if ((awaddr_reg[11:0] >= 12'h100) &&
                             (awaddr_reg[11:0] <
                              12'h100 +
                              ((DATA_WIDTH + 63)/64)*8)) begin

                        write_index =
                            (awaddr_reg[11:0] - 12'h100) >> 3;

                        data_reg[
                            write_index*64 +: 64
                        ] <= write64(
                            data_reg[
                                write_index*64 +: 64
                            ],
                            wdata_reg,
                            wstrb_reg
                        );

                    end


                    // ============================================
                    // PSUM INPUT BUFFER
                    //
                    // M=N=16:
                    //
                    // 0x200 -> [63:0]
                    // ...
                    // 0x2F8 -> [2047:1984]
                    // ============================================

                    else if ((awaddr_reg[11:0] >= 12'h200) &&
                             (awaddr_reg[11:0] <
                              12'h200 +
                              ((PSUM_WIDTH + 63)/64)*8)) begin

                        write_index =
                            (awaddr_reg[11:0] - 12'h200) >> 3;

                        psum_in_reg[
                            write_index*64 +: 64
                        ] <= write64(
                            psum_in_reg[
                                write_index*64 +: 64
                            ],
                            wdata_reg,
                            wstrb_reg
                        );

                    end


                    // ============================================
                    // Invalid / read-only address
                    // ============================================

                    else begin

                        s_axi_bresp <= 2'b10;     // SLVERR

                    end

                end

            end


            // ====================================================
            // AXI B handshake
            // ====================================================

            if (s_axi_bvalid &&
                s_axi_bready) begin

                s_axi_bvalid <= 1'b0;

            end

        end

    end


    // ============================================================
    // AXI Read Logic
    //
    // One outstanding read.
    // Single-beat MMIO only.
    // ============================================================

    assign s_axi_arready = !s_axi_rvalid;

    integer read_index;

    always @(posedge clk or negedge rst_n) begin

        if (!rst_n) begin

            s_axi_rdata  <= 64'b0;
            s_axi_rresp  <= 2'b00;
            s_axi_rid    <= {ID_WIDTH{1'b0}};
            s_axi_rlast  <= 1'b0;
            s_axi_rvalid <= 1'b0;

        end

        else begin


            // ====================================================
            // Accept AXI AR
            // ====================================================

            if (s_axi_arvalid &&
                s_axi_arready) begin

                s_axi_rvalid <= 1'b1;
                s_axi_rlast  <= 1'b1;
                s_axi_rid    <= s_axi_arid;

                s_axi_rdata <= 64'b0;
                s_axi_rresp <= 2'b00;


                // ------------------------------------------------
                // Only single-beat transaction
                // ------------------------------------------------

                if (s_axi_arlen != 8'd0) begin

                    s_axi_rresp <= 2'b10;     // SLVERR

                end

                else begin


                    // ============================================
                    // SRAM_ADDR
                    // ============================================

                    if (s_axi_araddr[11:0] == 12'h000) begin

                        s_axi_rdata <= {
                            60'b0,
                            sram_addr_reg
                        };

                    end


                    // ============================================
                    // SRAM_CE
                    // ============================================

                    else if (s_axi_araddr[11:0] == 12'h008) begin

                        s_axi_rdata <= {
                            {(64-M){1'b0}},
                            sram_ce_reg
                        };

                    end


                    // ============================================
                    // SRAM_WE
                    //
                    // pulse-only register
                    // ============================================

                    else if (s_axi_araddr[11:0] == 12'h010) begin

                        s_axi_rdata <= 64'b0;

                    end


                    // ============================================
                    // WEIGHT BUFFER READBACK
                    // ============================================

                    else if ((s_axi_araddr[11:0] >= 12'h020) &&
                             (s_axi_araddr[11:0] <
                              12'h020 +
                              ((WEIGHT_WIDTH + 63)/64)*8)) begin

                        read_index =
                            (s_axi_araddr[11:0] - 12'h020) >> 3;

                        s_axi_rdata <=
                            weight_reg[
                                read_index*64 +: 64
                            ];

                    end


                    // ============================================
                    // DATA BUFFER READBACK
                    // ============================================

                    else if ((s_axi_araddr[11:0] >= 12'h100) &&
                             (s_axi_araddr[11:0] <
                              12'h100 +
                              ((DATA_WIDTH + 63)/64)*8)) begin

                        read_index =
                            (s_axi_araddr[11:0] - 12'h100) >> 3;

                        s_axi_rdata <=
                            data_reg[
                                read_index*64 +: 64
                            ];

                    end


                    // ============================================
                    // PSUM INPUT BUFFER READBACK
                    // ============================================

                    else if ((s_axi_araddr[11:0] >= 12'h200) &&
                             (s_axi_araddr[11:0] <
                              12'h200 +
                              ((PSUM_WIDTH + 63)/64)*8)) begin

                        read_index =
                            (s_axi_araddr[11:0] - 12'h200) >> 3;

                        s_axi_rdata <=
                            psum_in_reg[
                                read_index*64 +: 64
                            ];

                    end


                    // ============================================
                    // PSUM OUTPUT
                    //
                    // Read ALL PE array output results.
                    //
                    // N=16:
                    //
                    // psum_out = 2048 bit
                    //
                    // 0x400 -> [63:0]
                    // 0x408 -> [127:64]
                    // ...
                    // 0x4F8 -> [2047:1984]
                    //
                    // 32 x 64-bit AXI reads.
                    // ============================================

                    else if ((s_axi_araddr[11:0] >= 12'h400) &&
                             (s_axi_araddr[11:0] <
                              12'h400 +
                              ((PSUM_WIDTH + 63)/64)*8)) begin

                        read_index =
                            (s_axi_araddr[11:0] - 12'h400) >> 3;

                        s_axi_rdata <=
                            psum_out_wire[
                                read_index*64 +: 64
                            ];

                    end


                    // ============================================
                    // Invalid address
                    // ============================================

                    else begin

                        s_axi_rdata <= 64'b0;
                        s_axi_rresp <= 2'b10;     // SLVERR

                    end

                end

            end


            // ====================================================
            // AXI R handshake
            // ====================================================

            if (s_axi_rvalid &&
                s_axi_rready) begin

                s_axi_rvalid <= 1'b0;
                s_axi_rlast  <= 1'b0;

            end

        end

    end


    // ============================================================
    // Currently unused AXI attributes
    //
    // This wrapper currently implements single-beat MMIO.
    //
    // s_axi_awsize
    // s_axi_awburst
    // s_axi_arsize
    // s_axi_arburst
    // ============================================================


endmodule