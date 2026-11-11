`include "common_defines.vh"
module soc_accel_top (
    input wire clk,
    input wire rst_n,
    output wire [31:0] ifu_axi_araddr,
    output wire [`RV_IFU_BUS_TAG-1:0] ifu_axi_arid,
    output wire [7:0] ifu_axi_arlen,
    output wire [2:0] ifu_axi_arsize,
    output wire [1:0] ifu_axi_arburst,
    output wire ifu_axi_arvalid,
    input wire ifu_axi_arready,
    input wire [63:0] ifu_axi_rdata,
    input wire [1:0] ifu_axi_rresp,
    input wire [`RV_IFU_BUS_TAG-1:0] ifu_axi_rid,
    input wire ifu_axi_rlast,
    input wire ifu_axi_rvalid,
    output wire ifu_axi_rready,
    output wire [31:0] mem_axi_awaddr,
    output wire [`RV_LSU_BUS_TAG-1:0] mem_axi_awid,
    output wire [7:0] mem_axi_awlen,
    output wire [2:0] mem_axi_awsize,
    output wire [1:0] mem_axi_awburst,
    output wire mem_axi_awvalid,
    input wire mem_axi_awready,
    output wire [63:0] mem_axi_wdata,
    output wire [7:0] mem_axi_wstrb,
    output wire mem_axi_wlast,
    output wire mem_axi_wvalid,
    input wire mem_axi_wready,
    input wire [1:0] mem_axi_bresp,
    input wire [`RV_LSU_BUS_TAG-1:0] mem_axi_bid,
    input wire mem_axi_bvalid,
    output wire mem_axi_bready,
    output wire [31:0] mem_axi_araddr,
    output wire [`RV_LSU_BUS_TAG-1:0] mem_axi_arid,
    output wire [7:0] mem_axi_arlen,
    output wire [2:0] mem_axi_arsize,
    output wire [1:0] mem_axi_arburst,
    output wire mem_axi_arvalid,
    input wire mem_axi_arready,
    input wire [63:0] mem_axi_rdata,
    input wire [1:0] mem_axi_rresp,
    input wire [`RV_LSU_BUS_TAG-1:0] mem_axi_rid,
    input wire mem_axi_rlast,
    input wire mem_axi_rvalid,
    output wire mem_axi_rready
);
wire [31:0] lsu_axi_awaddr;
wire [`RV_LSU_BUS_TAG-1:0] lsu_axi_awid;
wire [7:0] lsu_axi_awlen;
wire [2:0] lsu_axi_awsize;
wire [1:0] lsu_axi_awburst;
wire  lsu_axi_awvalid;
wire  lsu_axi_awready;
wire [63:0] lsu_axi_wdata;
wire [7:0] lsu_axi_wstrb;
wire  lsu_axi_wlast;
wire  lsu_axi_wvalid;
wire  lsu_axi_wready;
wire [`RV_LSU_BUS_TAG-1:0] lsu_axi_bid;
wire [1:0] lsu_axi_bresp;
wire  lsu_axi_bvalid;
wire  lsu_axi_bready;
wire [31:0] lsu_axi_araddr;
wire [`RV_LSU_BUS_TAG-1:0] lsu_axi_arid;
wire [7:0] lsu_axi_arlen;
wire [2:0] lsu_axi_arsize;
wire [1:0] lsu_axi_arburst;
wire  lsu_axi_arvalid;
wire  lsu_axi_arready;
wire [63:0] lsu_axi_rdata;
wire [`RV_LSU_BUS_TAG-1:0] lsu_axi_rid;
wire [1:0] lsu_axi_rresp;
wire  lsu_axi_rlast;
wire  lsu_axi_rvalid;
wire  lsu_axi_rready;
wire [31:0] cpu_axi_awaddr;
wire [`RV_LSU_BUS_TAG-1:0] cpu_axi_awid;
wire [7:0] cpu_axi_awlen;
wire [2:0] cpu_axi_awsize;
wire [1:0] cpu_axi_awburst;
wire  cpu_axi_awvalid;
wire  cpu_axi_awready;
wire [63:0] cpu_axi_wdata;
wire [7:0] cpu_axi_wstrb;
wire  cpu_axi_wlast;
wire  cpu_axi_wvalid;
wire  cpu_axi_wready;
wire [`RV_LSU_BUS_TAG-1:0] cpu_axi_bid;
wire [1:0] cpu_axi_bresp;
wire  cpu_axi_bvalid;
wire  cpu_axi_bready;
wire [31:0] cpu_axi_araddr;
wire [`RV_LSU_BUS_TAG-1:0] cpu_axi_arid;
wire [7:0] cpu_axi_arlen;
wire [2:0] cpu_axi_arsize;
wire [1:0] cpu_axi_arburst;
wire  cpu_axi_arvalid;
wire  cpu_axi_arready;
wire [63:0] cpu_axi_rdata;
wire [`RV_LSU_BUS_TAG-1:0] cpu_axi_rid;
wire [1:0] cpu_axi_rresp;
wire  cpu_axi_rlast;
wire  cpu_axi_rvalid;
wire  cpu_axi_rready;
wire [31:0] control_axi_awaddr;
wire [`RV_LSU_BUS_TAG-1:0] control_axi_awid;
wire [7:0] control_axi_awlen;
wire [2:0] control_axi_awsize;
wire [1:0] control_axi_awburst;
wire  control_axi_awvalid;
wire  control_axi_awready;
wire [63:0] control_axi_wdata;
wire [7:0] control_axi_wstrb;
wire  control_axi_wlast;
wire  control_axi_wvalid;
wire  control_axi_wready;
wire [`RV_LSU_BUS_TAG-1:0] control_axi_bid;
wire [1:0] control_axi_bresp;
wire  control_axi_bvalid;
wire  control_axi_bready;
wire [31:0] control_axi_araddr;
wire [`RV_LSU_BUS_TAG-1:0] control_axi_arid;
wire [7:0] control_axi_arlen;
wire [2:0] control_axi_arsize;
wire [1:0] control_axi_arburst;
wire  control_axi_arvalid;
wire  control_axi_arready;
wire [63:0] control_axi_rdata;
wire [`RV_LSU_BUS_TAG-1:0] control_axi_rid;
wire [1:0] control_axi_rresp;
wire  control_axi_rlast;
wire  control_axi_rvalid;
wire  control_axi_rready;
wire [31:0] dma_axi_awaddr;
wire [`RV_LSU_BUS_TAG-1:0] dma_axi_awid;
wire [7:0] dma_axi_awlen;
wire [2:0] dma_axi_awsize;
wire [1:0] dma_axi_awburst;
wire  dma_axi_awvalid;
wire  dma_axi_awready;
wire [63:0] dma_axi_wdata;
wire [7:0] dma_axi_wstrb;
wire  dma_axi_wlast;
wire  dma_axi_wvalid;
wire  dma_axi_wready;
wire [`RV_LSU_BUS_TAG-1:0] dma_axi_bid;
wire [1:0] dma_axi_bresp;
wire  dma_axi_bvalid;
wire  dma_axi_bready;
wire [31:0] dma_axi_araddr;
wire [`RV_LSU_BUS_TAG-1:0] dma_axi_arid;
wire [7:0] dma_axi_arlen;
wire [2:0] dma_axi_arsize;
wire [1:0] dma_axi_arburst;
wire  dma_axi_arvalid;
wire  dma_axi_arready;
wire [63:0] dma_axi_rdata;
wire [`RV_LSU_BUS_TAG-1:0] dma_axi_rid;
wire [1:0] dma_axi_rresp;
wire  dma_axi_rlast;
wire  dma_axi_rvalid;
wire  dma_axi_rready;
wire request_valid,request_ready,cancel,engine_busy,engine_done;
wire[7:0]request_opcode,engine_error;
wire[511:0]arguments;
wire[31:0]engine_cycles,engine_bytes;
    veer_wrapper u_cpu (
        .clk(clk),
        .rst_l(rst_n),
        .dbg_rst_l(rst_n),
        .rst_vec(31'h40000000),
        .nmi_int('0),
        .nmi_vec(31'h40000000),
        .jtag_id(31'h00000045),
        .trace_rv_i_insn_ip(),
        .trace_rv_i_address_ip(),
        .trace_rv_i_valid_ip(),
        .trace_rv_i_exception_ip(),
        .trace_rv_i_ecause_ip(),
        .trace_rv_i_interrupt_ip(),
        .trace_rv_i_tval_ip(),
        .lsu_axi_awvalid(lsu_axi_awvalid),
        .lsu_axi_awready(lsu_axi_awready),
        .lsu_axi_awid(lsu_axi_awid),
        .lsu_axi_awaddr(lsu_axi_awaddr),
        .lsu_axi_awregion(),
        .lsu_axi_awlen(lsu_axi_awlen),
        .lsu_axi_awsize(lsu_axi_awsize),
        .lsu_axi_awburst(lsu_axi_awburst),
        .lsu_axi_awlock(),
        .lsu_axi_awcache(),
        .lsu_axi_awprot(),
        .lsu_axi_awqos(),
        .lsu_axi_wvalid(lsu_axi_wvalid),
        .lsu_axi_wready(lsu_axi_wready),
        .lsu_axi_wdata(lsu_axi_wdata),
        .lsu_axi_wstrb(lsu_axi_wstrb),
        .lsu_axi_wlast(lsu_axi_wlast),
        .lsu_axi_bvalid(lsu_axi_bvalid),
        .lsu_axi_bready(lsu_axi_bready),
        .lsu_axi_bresp(lsu_axi_bresp),
        .lsu_axi_bid(lsu_axi_bid),
        .lsu_axi_arvalid(lsu_axi_arvalid),
        .lsu_axi_arready(lsu_axi_arready),
        .lsu_axi_arid(lsu_axi_arid),
        .lsu_axi_araddr(lsu_axi_araddr),
        .lsu_axi_arregion(),
        .lsu_axi_arlen(lsu_axi_arlen),
        .lsu_axi_arsize(lsu_axi_arsize),
        .lsu_axi_arburst(lsu_axi_arburst),
        .lsu_axi_arlock(),
        .lsu_axi_arcache(),
        .lsu_axi_arprot(),
        .lsu_axi_arqos(),
        .lsu_axi_rvalid(lsu_axi_rvalid),
        .lsu_axi_rready(lsu_axi_rready),
        .lsu_axi_rid(lsu_axi_rid),
        .lsu_axi_rdata(lsu_axi_rdata),
        .lsu_axi_rresp(lsu_axi_rresp),
        .lsu_axi_rlast(lsu_axi_rlast),
        .ifu_axi_awvalid(),
        .ifu_axi_awready('0),
        .ifu_axi_awid(),
        .ifu_axi_awaddr(),
        .ifu_axi_awregion(),
        .ifu_axi_awlen(),
        .ifu_axi_awsize(),
        .ifu_axi_awburst(),
        .ifu_axi_awlock(),
        .ifu_axi_awcache(),
        .ifu_axi_awprot(),
        .ifu_axi_awqos(),
        .ifu_axi_wvalid(),
        .ifu_axi_wready('0),
        .ifu_axi_wdata(),
        .ifu_axi_wstrb(),
        .ifu_axi_wlast(),
        .ifu_axi_bvalid('0),
        .ifu_axi_bready(),
        .ifu_axi_bresp('0),
        .ifu_axi_bid('0),
        .ifu_axi_arvalid(ifu_axi_arvalid),
        .ifu_axi_arready(ifu_axi_arready),
        .ifu_axi_arid(ifu_axi_arid),
        .ifu_axi_araddr(ifu_axi_araddr),
        .ifu_axi_arregion(),
        .ifu_axi_arlen(ifu_axi_arlen),
        .ifu_axi_arsize(ifu_axi_arsize),
        .ifu_axi_arburst(ifu_axi_arburst),
        .ifu_axi_arlock(),
        .ifu_axi_arcache(),
        .ifu_axi_arprot(),
        .ifu_axi_arqos(),
        .ifu_axi_rvalid(ifu_axi_rvalid),
        .ifu_axi_rready(ifu_axi_rready),
        .ifu_axi_rid(ifu_axi_rid),
        .ifu_axi_rdata(ifu_axi_rdata),
        .ifu_axi_rresp(ifu_axi_rresp),
        .ifu_axi_rlast(ifu_axi_rlast),
        .sb_axi_awvalid(),
        .sb_axi_awready('0),
        .sb_axi_awid(),
        .sb_axi_awaddr(),
        .sb_axi_awregion(),
        .sb_axi_awlen(),
        .sb_axi_awsize(),
        .sb_axi_awburst(),
        .sb_axi_awlock(),
        .sb_axi_awcache(),
        .sb_axi_awprot(),
        .sb_axi_awqos(),
        .sb_axi_wvalid(),
        .sb_axi_wready('0),
        .sb_axi_wdata(),
        .sb_axi_wstrb(),
        .sb_axi_wlast(),
        .sb_axi_bvalid('0),
        .sb_axi_bready(),
        .sb_axi_bresp('0),
        .sb_axi_bid('0),
        .sb_axi_arvalid(),
        .sb_axi_arready('0),
        .sb_axi_arid(),
        .sb_axi_araddr(),
        .sb_axi_arregion(),
        .sb_axi_arlen(),
        .sb_axi_arsize(),
        .sb_axi_arburst(),
        .sb_axi_arlock(),
        .sb_axi_arcache(),
        .sb_axi_arprot(),
        .sb_axi_arqos(),
        .sb_axi_rvalid('0),
        .sb_axi_rready(),
        .sb_axi_rid('0),
        .sb_axi_rdata('0),
        .sb_axi_rresp('0),
        .sb_axi_rlast('0),
        .dma_axi_awvalid('0),
        .dma_axi_awready(),
        .dma_axi_awid('0),
        .dma_axi_awaddr('0),
        .dma_axi_awsize('0),
        .dma_axi_awprot('0),
        .dma_axi_awlen('0),
        .dma_axi_awburst('0),
        .dma_axi_wvalid('0),
        .dma_axi_wready(),
        .dma_axi_wdata('0),
        .dma_axi_wstrb('0),
        .dma_axi_wlast('0),
        .dma_axi_bvalid(),
        .dma_axi_bready('0),
        .dma_axi_bresp(),
        .dma_axi_bid(),
        .dma_axi_arvalid('0),
        .dma_axi_arready(),
        .dma_axi_arid('0),
        .dma_axi_araddr('0),
        .dma_axi_arsize('0),
        .dma_axi_arprot('0),
        .dma_axi_arlen('0),
        .dma_axi_arburst('0),
        .dma_axi_rvalid(),
        .dma_axi_rready('0),
        .dma_axi_rid(),
        .dma_axi_rdata(),
        .dma_axi_rresp(),
        .dma_axi_rlast(),
        .lsu_bus_clk_en(1'b1),
        .ifu_bus_clk_en(1'b1),
        .dbg_bus_clk_en(1'b1),
        .dma_bus_clk_en(1'b1),
        .timer_int('0),
        .soft_int('0),
        .extintsrc_req('0),
        .dec_tlu_perfcnt0(),
        .dec_tlu_perfcnt1(),
        .dec_tlu_perfcnt2(),
        .dec_tlu_perfcnt3(),
        .jtag_tck('0),
        .jtag_tms(1'b1),
        .jtag_tdi('0),
        .jtag_trst_n('0),
        .jtag_tdo(),
        .jtag_tdoEn(),
`ifdef SOC_CPU_HAS_CORE_ID
        .core_id('0),
`endif
        .mem_clk(),
        .iccm_clken(),
        .iccm_wren_bank(),
        .iccm_addr_bank(),
        .iccm_bank_wr_data(),
        .iccm_bank_wr_ecc(),
        .iccm_bank_dout('0),
        .iccm_bank_ecc('0),
        .dccm_clken(),
        .dccm_wren_bank(),
        .dccm_addr_bank(),
        .dccm_wr_data_bank(),
        .dccm_wr_ecc_bank(),
        .dccm_bank_dout('0),
        .dccm_bank_ecc('0),
        .ic_b_sb_wren(),
        .ic_b_sb_bit_en_vec(),
        .ic_sb_wr_data(),
        .ic_rw_addr_bank_q(),
        .ic_bank_way_clken_final(),
        .ic_bank_way_clken_final_up(),
        .wb_packeddout_pre('0),
        .wb_dout_pre_up('0),
        .ic_tag_clken_final(),
        .ic_tag_wren_q(),
        .ic_tag_wren_biten_vec(),
        .ic_tag_wr_data(),
        .ic_rw_addr_q(),
        .ic_tag_data_raw_pre('0),
        .ic_tag_data_raw_packed_pre('0),
        .iccm_ecc_single_error(),
        .iccm_ecc_double_error(),
        .dccm_ecc_single_error(),
        .dccm_ecc_double_error(),
        .dccm_write_readback_error(),
        .mpc_debug_halt_req('0),
        .mpc_debug_run_req('0),
        .mpc_reset_run_req(1'b1),
        .mpc_debug_halt_ack(),
        .mpc_debug_run_ack(),
        .debug_brkpt_status(),
        .i_cpu_halt_req('0),
        .o_cpu_halt_ack(),
        .o_cpu_halt_status(),
        .o_debug_mode_status(),
        .i_cpu_run_req('0),
        .o_cpu_run_ack(),
        .scan_mode('0),
        .mbist_mode('0),
        .dmi_core_enable('0),
        .dmi_uncore_enable('0),
        .dmi_uncore_en(),
        .dmi_uncore_wr_en(),
        .dmi_uncore_addr(),
        .dmi_uncore_wdata(),
        .dmi_uncore_rdata('0)
    );
axi_pe_router #(.ID_WIDTH(`RV_LSU_BUS_TAG),.PE_BASE(32'h90001000)) u_command_router(
 .clk(clk),
 .rst_n(rst_n),
 .s_axi_awaddr(lsu_axi_awaddr),
 .mem_axi_awaddr(cpu_axi_awaddr),
 .pe_axi_awaddr(control_axi_awaddr),
 .s_axi_awid(lsu_axi_awid),
 .mem_axi_awid(cpu_axi_awid),
 .pe_axi_awid(control_axi_awid),
 .s_axi_awlen(lsu_axi_awlen),
 .mem_axi_awlen(cpu_axi_awlen),
 .pe_axi_awlen(control_axi_awlen),
 .s_axi_awsize(lsu_axi_awsize),
 .mem_axi_awsize(cpu_axi_awsize),
 .pe_axi_awsize(control_axi_awsize),
 .s_axi_awburst(lsu_axi_awburst),
 .mem_axi_awburst(cpu_axi_awburst),
 .pe_axi_awburst(control_axi_awburst),
 .s_axi_awvalid(lsu_axi_awvalid),
 .mem_axi_awvalid(cpu_axi_awvalid),
 .pe_axi_awvalid(control_axi_awvalid),
 .s_axi_awready(lsu_axi_awready),
 .mem_axi_awready(cpu_axi_awready),
 .pe_axi_awready(control_axi_awready),
 .s_axi_wdata(lsu_axi_wdata),
 .mem_axi_wdata(cpu_axi_wdata),
 .pe_axi_wdata(control_axi_wdata),
 .s_axi_wstrb(lsu_axi_wstrb),
 .mem_axi_wstrb(cpu_axi_wstrb),
 .pe_axi_wstrb(control_axi_wstrb),
 .s_axi_wlast(lsu_axi_wlast),
 .mem_axi_wlast(cpu_axi_wlast),
 .pe_axi_wlast(control_axi_wlast),
 .s_axi_wvalid(lsu_axi_wvalid),
 .mem_axi_wvalid(cpu_axi_wvalid),
 .pe_axi_wvalid(control_axi_wvalid),
 .s_axi_wready(lsu_axi_wready),
 .mem_axi_wready(cpu_axi_wready),
 .pe_axi_wready(control_axi_wready),
 .s_axi_bid(lsu_axi_bid),
 .mem_axi_bid(cpu_axi_bid),
 .pe_axi_bid(control_axi_bid),
 .s_axi_bresp(lsu_axi_bresp),
 .mem_axi_bresp(cpu_axi_bresp),
 .pe_axi_bresp(control_axi_bresp),
 .s_axi_bvalid(lsu_axi_bvalid),
 .mem_axi_bvalid(cpu_axi_bvalid),
 .pe_axi_bvalid(control_axi_bvalid),
 .s_axi_bready(lsu_axi_bready),
 .mem_axi_bready(cpu_axi_bready),
 .pe_axi_bready(control_axi_bready),
 .s_axi_araddr(lsu_axi_araddr),
 .mem_axi_araddr(cpu_axi_araddr),
 .pe_axi_araddr(control_axi_araddr),
 .s_axi_arid(lsu_axi_arid),
 .mem_axi_arid(cpu_axi_arid),
 .pe_axi_arid(control_axi_arid),
 .s_axi_arlen(lsu_axi_arlen),
 .mem_axi_arlen(cpu_axi_arlen),
 .pe_axi_arlen(control_axi_arlen),
 .s_axi_arsize(lsu_axi_arsize),
 .mem_axi_arsize(cpu_axi_arsize),
 .pe_axi_arsize(control_axi_arsize),
 .s_axi_arburst(lsu_axi_arburst),
 .mem_axi_arburst(cpu_axi_arburst),
 .pe_axi_arburst(control_axi_arburst),
 .s_axi_arvalid(lsu_axi_arvalid),
 .mem_axi_arvalid(cpu_axi_arvalid),
 .pe_axi_arvalid(control_axi_arvalid),
 .s_axi_arready(lsu_axi_arready),
 .mem_axi_arready(cpu_axi_arready),
 .pe_axi_arready(control_axi_arready),
 .s_axi_rdata(lsu_axi_rdata),
 .mem_axi_rdata(cpu_axi_rdata),
 .pe_axi_rdata(control_axi_rdata),
 .s_axi_rid(lsu_axi_rid),
 .mem_axi_rid(cpu_axi_rid),
 .pe_axi_rid(control_axi_rid),
 .s_axi_rresp(lsu_axi_rresp),
 .mem_axi_rresp(cpu_axi_rresp),
 .pe_axi_rresp(control_axi_rresp),
 .s_axi_rlast(lsu_axi_rlast),
 .mem_axi_rlast(cpu_axi_rlast),
 .pe_axi_rlast(control_axi_rlast),
 .s_axi_rvalid(lsu_axi_rvalid),
 .mem_axi_rvalid(cpu_axi_rvalid),
 .pe_axi_rvalid(control_axi_rvalid),
 .s_axi_rready(lsu_axi_rready),
 .mem_axi_rready(cpu_axi_rready),
 .pe_axi_rready(control_axi_rready)
);
axi_accel_mmio #(.ID_WIDTH(`RV_LSU_BUS_TAG),.SUPPORTED_OPS(32'h1fe)) u_control(
 .clk(clk),
 .rst_n(rst_n),
 .s_axi_awaddr(control_axi_awaddr),
 .s_axi_awid(control_axi_awid),
 .s_axi_awlen(control_axi_awlen),
 .s_axi_awsize(control_axi_awsize),
 .s_axi_awburst(control_axi_awburst),
 .s_axi_awvalid(control_axi_awvalid),
 .s_axi_awready(control_axi_awready),
 .s_axi_wdata(control_axi_wdata),
 .s_axi_wstrb(control_axi_wstrb),
 .s_axi_wlast(control_axi_wlast),
 .s_axi_wvalid(control_axi_wvalid),
 .s_axi_wready(control_axi_wready),
 .s_axi_bid(control_axi_bid),
 .s_axi_bresp(control_axi_bresp),
 .s_axi_bvalid(control_axi_bvalid),
 .s_axi_bready(control_axi_bready),
 .s_axi_araddr(control_axi_araddr),
 .s_axi_arid(control_axi_arid),
 .s_axi_arlen(control_axi_arlen),
 .s_axi_arsize(control_axi_arsize),
 .s_axi_arburst(control_axi_arburst),
 .s_axi_arvalid(control_axi_arvalid),
 .s_axi_arready(control_axi_arready),
 .s_axi_rdata(control_axi_rdata),
 .s_axi_rid(control_axi_rid),
 .s_axi_rresp(control_axi_rresp),
 .s_axi_rlast(control_axi_rlast),
 .s_axi_rvalid(control_axi_rvalid),
 .s_axi_rready(control_axi_rready),
 .request_valid(request_valid),
 .request_ready(request_ready),
 .request_opcode(request_opcode),
 .cancel(cancel),
 .engine_busy(engine_busy),
 .engine_done(engine_done),
 .engine_error(engine_error),
 .engine_cycles(engine_cycles),
 .engine_bytes(engine_bytes),
 .request_arguments(arguments)
);
axi_accel_backend_vecpipe #(.ROWS(8),.COLS(16),.LANES(4),.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend(
 .clk(clk),
 .rst_n(rst_n),
 .request_valid(request_valid),
 .request_ready(request_ready),
 .request_opcode(request_opcode),
 .cancel(cancel),
 .engine_busy(engine_busy),
 .engine_done(engine_done),
 .engine_error(engine_error),
 .engine_cycles(engine_cycles),
 .engine_bytes(engine_bytes),
 .request_arguments(arguments),
 .m_axi_awaddr(dma_axi_awaddr),
 .m_axi_awid(dma_axi_awid),
 .m_axi_awlen(dma_axi_awlen),
 .m_axi_awsize(dma_axi_awsize),
 .m_axi_awburst(dma_axi_awburst),
 .m_axi_awvalid(dma_axi_awvalid),
 .m_axi_awready(dma_axi_awready),
 .m_axi_wdata(dma_axi_wdata),
 .m_axi_wstrb(dma_axi_wstrb),
 .m_axi_wlast(dma_axi_wlast),
 .m_axi_wvalid(dma_axi_wvalid),
 .m_axi_wready(dma_axi_wready),
 .m_axi_bid(dma_axi_bid),
 .m_axi_bresp(dma_axi_bresp),
 .m_axi_bvalid(dma_axi_bvalid),
 .m_axi_bready(dma_axi_bready),
 .m_axi_araddr(dma_axi_araddr),
 .m_axi_arid(dma_axi_arid),
 .m_axi_arlen(dma_axi_arlen),
 .m_axi_arsize(dma_axi_arsize),
 .m_axi_arburst(dma_axi_arburst),
 .m_axi_arvalid(dma_axi_arvalid),
 .m_axi_arready(dma_axi_arready),
 .m_axi_rdata(dma_axi_rdata),
 .m_axi_rid(dma_axi_rid),
 .m_axi_rresp(dma_axi_rresp),
 .m_axi_rlast(dma_axi_rlast),
 .m_axi_rvalid(dma_axi_rvalid),
 .m_axi_rready(dma_axi_rready)
);
axi_dma_arbiter #(.ID_WIDTH(`RV_LSU_BUS_TAG)) u_memory_arbiter(
 .clk(clk),
 .rst_n(rst_n),
 .s_axi_awaddr({dma_axi_awaddr,cpu_axi_awaddr}),
 .m_axi_awaddr(mem_axi_awaddr),
 .s_axi_awid({dma_axi_awid,cpu_axi_awid}),
 .m_axi_awid(mem_axi_awid),
 .s_axi_awlen({dma_axi_awlen,cpu_axi_awlen}),
 .m_axi_awlen(mem_axi_awlen),
 .s_axi_awsize({dma_axi_awsize,cpu_axi_awsize}),
 .m_axi_awsize(mem_axi_awsize),
 .s_axi_awburst({dma_axi_awburst,cpu_axi_awburst}),
 .m_axi_awburst(mem_axi_awburst),
 .s_axi_awvalid({dma_axi_awvalid,cpu_axi_awvalid}),
 .m_axi_awvalid(mem_axi_awvalid),
 .s_axi_awready({dma_axi_awready,cpu_axi_awready}),
 .m_axi_awready(mem_axi_awready),
 .s_axi_wdata({dma_axi_wdata,cpu_axi_wdata}),
 .m_axi_wdata(mem_axi_wdata),
 .s_axi_wstrb({dma_axi_wstrb,cpu_axi_wstrb}),
 .m_axi_wstrb(mem_axi_wstrb),
 .s_axi_wlast({dma_axi_wlast,cpu_axi_wlast}),
 .m_axi_wlast(mem_axi_wlast),
 .s_axi_wvalid({dma_axi_wvalid,cpu_axi_wvalid}),
 .m_axi_wvalid(mem_axi_wvalid),
 .s_axi_wready({dma_axi_wready,cpu_axi_wready}),
 .m_axi_wready(mem_axi_wready),
 .s_axi_bid({dma_axi_bid,cpu_axi_bid}),
 .m_axi_bid(mem_axi_bid),
 .s_axi_bresp({dma_axi_bresp,cpu_axi_bresp}),
 .m_axi_bresp(mem_axi_bresp),
 .s_axi_bvalid({dma_axi_bvalid,cpu_axi_bvalid}),
 .m_axi_bvalid(mem_axi_bvalid),
 .s_axi_bready({dma_axi_bready,cpu_axi_bready}),
 .m_axi_bready(mem_axi_bready),
 .s_axi_araddr({dma_axi_araddr,cpu_axi_araddr}),
 .m_axi_araddr(mem_axi_araddr),
 .s_axi_arid({dma_axi_arid,cpu_axi_arid}),
 .m_axi_arid(mem_axi_arid),
 .s_axi_arlen({dma_axi_arlen,cpu_axi_arlen}),
 .m_axi_arlen(mem_axi_arlen),
 .s_axi_arsize({dma_axi_arsize,cpu_axi_arsize}),
 .m_axi_arsize(mem_axi_arsize),
 .s_axi_arburst({dma_axi_arburst,cpu_axi_arburst}),
 .m_axi_arburst(mem_axi_arburst),
 .s_axi_arvalid({dma_axi_arvalid,cpu_axi_arvalid}),
 .m_axi_arvalid(mem_axi_arvalid),
 .s_axi_arready({dma_axi_arready,cpu_axi_arready}),
 .m_axi_arready(mem_axi_arready),
 .s_axi_rdata({dma_axi_rdata,cpu_axi_rdata}),
 .m_axi_rdata(mem_axi_rdata),
 .s_axi_rid({dma_axi_rid,cpu_axi_rid}),
 .m_axi_rid(mem_axi_rid),
 .s_axi_rresp({dma_axi_rresp,cpu_axi_rresp}),
 .m_axi_rresp(mem_axi_rresp),
 .s_axi_rlast({dma_axi_rlast,cpu_axi_rlast}),
 .m_axi_rlast(mem_axi_rlast),
 .s_axi_rvalid({dma_axi_rvalid,cpu_axi_rvalid}),
 .m_axi_rvalid(mem_axi_rvalid),
 .s_axi_rready({dma_axi_rready,cpu_axi_rready}),
 .m_axi_rready(mem_axi_rready)
);
endmodule
