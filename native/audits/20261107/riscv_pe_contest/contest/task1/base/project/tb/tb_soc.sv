`timescale 1ns/1ps
`include "common_defines.vh"
module tb_soc;
    reg clk = 0;
    reg rst_n = 0;
    real half_period = 5.0;
    integer max_cycles = 200000;
    integer cycles = 0;
    string sdf_file;
    initial begin
        if ($value$plusargs("HALF_PERIOD_NS=%f", half_period)) begin end
        if (half_period <= 0) $fatal(1, "Invalid clock period");
        forever #(half_period) clk = ~clk;
    end
    initial begin
        if ($value$plusargs("MAX_CYCLES=%d", max_cycles)) begin end
        repeat (20) @(negedge clk);
        rst_n = 1;
    end
    always @(posedge clk) if (rst_n) begin
        cycles <= cycles + 1;
        if (cycles >= max_cycles) $fatal(1, "TIMEOUT after %0d cycles", cycles);
    end
    initial begin
`ifdef GATE_SIM
        if ($value$plusargs("SDF=%s", sdf_file))
            $sdf_annotate(sdf_file, dut);
`else
        if ($value$plusargs("SDF=%s", sdf_file))
            $fatal(1, "SDF requires a gate netlist and GATE_SIM");
`endif
        if ($test$plusargs("WAVE")) begin
`ifdef VCS
            $vcdplusfile("build/soc.vpd");
            $vcdpluson(0, tb_soc);
`else
            $dumpfile("build/soc.vcd");
            $dumpvars(0, tb_soc);
`endif
        end
    end
    wire [31:0] ifu_axi_araddr;
    wire [`RV_IFU_BUS_TAG-1:0] ifu_axi_arid;
    wire [7:0] ifu_axi_arlen;
    wire [2:0] ifu_axi_arsize;
    wire [1:0] ifu_axi_arburst;
    wire ifu_axi_arvalid;
    wire ifu_axi_arready;
    wire [63:0] ifu_axi_rdata;
    wire [1:0] ifu_axi_rresp;
    wire [`RV_IFU_BUS_TAG-1:0] ifu_axi_rid;
    wire ifu_axi_rlast;
    wire ifu_axi_rvalid;
    wire ifu_axi_rready;
    wire [31:0] mem_axi_awaddr;
    wire [`RV_LSU_BUS_TAG-1:0] mem_axi_awid;
    wire [7:0] mem_axi_awlen;
    wire [2:0] mem_axi_awsize;
    wire [1:0] mem_axi_awburst;
    wire mem_axi_awvalid;
    wire mem_axi_awready;
    wire [63:0] mem_axi_wdata;
    wire [7:0] mem_axi_wstrb;
    wire mem_axi_wlast;
    wire mem_axi_wvalid;
    wire mem_axi_wready;
    wire [1:0] mem_axi_bresp;
    wire [`RV_LSU_BUS_TAG-1:0] mem_axi_bid;
    wire mem_axi_bvalid;
    wire mem_axi_bready;
    wire [31:0] mem_axi_araddr;
    wire [`RV_LSU_BUS_TAG-1:0] mem_axi_arid;
    wire [7:0] mem_axi_arlen;
    wire [2:0] mem_axi_arsize;
    wire [1:0] mem_axi_arburst;
    wire mem_axi_arvalid;
    wire mem_axi_arready;
    wire [63:0] mem_axi_rdata;
    wire [1:0] mem_axi_rresp;
    wire [`RV_LSU_BUS_TAG-1:0] mem_axi_rid;
    wire mem_axi_rlast;
    wire mem_axi_rvalid;
    wire mem_axi_rready;
    soc_top dut (
        .clk(clk),
        .rst_n(rst_n),
        .ifu_axi_araddr(ifu_axi_araddr),
        .ifu_axi_arid(ifu_axi_arid),
        .ifu_axi_arlen(ifu_axi_arlen),
        .ifu_axi_arsize(ifu_axi_arsize),
        .ifu_axi_arburst(ifu_axi_arburst),
        .ifu_axi_arvalid(ifu_axi_arvalid),
        .ifu_axi_arready(ifu_axi_arready),
        .ifu_axi_rdata(ifu_axi_rdata),
        .ifu_axi_rresp(ifu_axi_rresp),
        .ifu_axi_rid(ifu_axi_rid),
        .ifu_axi_rlast(ifu_axi_rlast),
        .ifu_axi_rvalid(ifu_axi_rvalid),
        .ifu_axi_rready(ifu_axi_rready),
        .mem_axi_awaddr(mem_axi_awaddr),
        .mem_axi_awid(mem_axi_awid),
        .mem_axi_awlen(mem_axi_awlen),
        .mem_axi_awsize(mem_axi_awsize),
        .mem_axi_awburst(mem_axi_awburst),
        .mem_axi_awvalid(mem_axi_awvalid),
        .mem_axi_awready(mem_axi_awready),
        .mem_axi_wdata(mem_axi_wdata),
        .mem_axi_wstrb(mem_axi_wstrb),
        .mem_axi_wlast(mem_axi_wlast),
        .mem_axi_wvalid(mem_axi_wvalid),
        .mem_axi_wready(mem_axi_wready),
        .mem_axi_bresp(mem_axi_bresp),
        .mem_axi_bid(mem_axi_bid),
        .mem_axi_bvalid(mem_axi_bvalid),
        .mem_axi_bready(mem_axi_bready),
        .mem_axi_araddr(mem_axi_araddr),
        .mem_axi_arid(mem_axi_arid),
        .mem_axi_arlen(mem_axi_arlen),
        .mem_axi_arsize(mem_axi_arsize),
        .mem_axi_arburst(mem_axi_arburst),
        .mem_axi_arvalid(mem_axi_arvalid),
        .mem_axi_arready(mem_axi_arready),
        .mem_axi_rdata(mem_axi_rdata),
        .mem_axi_rresp(mem_axi_rresp),
        .mem_axi_rid(mem_axi_rid),
        .mem_axi_rlast(mem_axi_rlast),
        .mem_axi_rvalid(mem_axi_rvalid),
        .mem_axi_rready(mem_axi_rready)
    );
    axi_ram_model #(.ID_WIDTH(`RV_IFU_BUS_TAG), .ALLOW_MMIO(0)) imem (
        .clk(clk),
        .rst_n(rst_n),
        .s_axi_araddr(ifu_axi_araddr),
        .s_axi_arid(ifu_axi_arid),
        .s_axi_arlen(ifu_axi_arlen),
        .s_axi_arsize(ifu_axi_arsize),
        .s_axi_arburst(ifu_axi_arburst),
        .s_axi_arvalid(ifu_axi_arvalid),
        .s_axi_arready(ifu_axi_arready),
        .s_axi_rdata(ifu_axi_rdata),
        .s_axi_rresp(ifu_axi_rresp),
        .s_axi_rid(ifu_axi_rid),
        .s_axi_rlast(ifu_axi_rlast),
        .s_axi_rvalid(ifu_axi_rvalid),
        .s_axi_rready(ifu_axi_rready),
        .s_axi_awaddr('0),
        .s_axi_awid('0),
        .s_axi_awlen('0),
        .s_axi_awsize('0),
        .s_axi_awburst('0),
        .s_axi_awvalid('0),
        .s_axi_awready(),
        .s_axi_wdata('0),
        .s_axi_wstrb('0),
        .s_axi_wlast('0),
        .s_axi_wvalid('0),
        .s_axi_wready(),
        .s_axi_bresp(),
        .s_axi_bid(),
        .s_axi_bvalid(),
        .s_axi_bready('0)
    );
    axi_ram_model #(.ID_WIDTH(`RV_LSU_BUS_TAG), .ALLOW_MMIO(1)) dmem (
        .clk(clk),
        .rst_n(rst_n),
        .s_axi_awaddr(mem_axi_awaddr),
        .s_axi_awid(mem_axi_awid),
        .s_axi_awlen(mem_axi_awlen),
        .s_axi_awsize(mem_axi_awsize),
        .s_axi_awburst(mem_axi_awburst),
        .s_axi_awvalid(mem_axi_awvalid),
        .s_axi_awready(mem_axi_awready),
        .s_axi_wdata(mem_axi_wdata),
        .s_axi_wstrb(mem_axi_wstrb),
        .s_axi_wlast(mem_axi_wlast),
        .s_axi_wvalid(mem_axi_wvalid),
        .s_axi_wready(mem_axi_wready),
        .s_axi_bresp(mem_axi_bresp),
        .s_axi_bid(mem_axi_bid),
        .s_axi_bvalid(mem_axi_bvalid),
        .s_axi_bready(mem_axi_bready),
        .s_axi_araddr(mem_axi_araddr),
        .s_axi_arid(mem_axi_arid),
        .s_axi_arlen(mem_axi_arlen),
        .s_axi_arsize(mem_axi_arsize),
        .s_axi_arburst(mem_axi_arburst),
        .s_axi_arvalid(mem_axi_arvalid),
        .s_axi_arready(mem_axi_arready),
        .s_axi_rdata(mem_axi_rdata),
        .s_axi_rresp(mem_axi_rresp),
        .s_axi_rid(mem_axi_rid),
        .s_axi_rlast(mem_axi_rlast),
        .s_axi_rvalid(mem_axi_rvalid),
        .s_axi_rready(mem_axi_rready)
    );
endmodule
