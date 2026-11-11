`timescale 1ns/1ps
// AXI_RESPONSE_DELAY_WRAPPER_V1
// Testbench only. Delay RAM->DUT responses, never DUT->RAM requests.
// Default 0 ns; 1 ns exercises the reference SDC input-delay maximum.
// Optional external setup check below; no SRAM timing model is supplied.
`ifndef AXI_RAM_OUTPUT_DELAY_NS
`define AXI_RAM_OUTPUT_DELAY_NS 0.0
`endif
module axi_ram_model #(
    parameter ID_WIDTH=4,
    parameter ALLOW_MMIO=0,
    parameter [31:0] BASE=32'h80000000,
    parameter MEM_BYTES=67108864,
    parameter real RESPONSE_DELAY_NS=`AXI_RAM_OUTPUT_DELAY_NS
)(
    input wire clk,
    input wire rst_n,
    input wire [31:0] s_axi_awaddr,
    input wire [ID_WIDTH-1:0] s_axi_awid,
    input wire [7:0] s_axi_awlen,
    input wire [2:0] s_axi_awsize,
    input wire [1:0] s_axi_awburst,
    input wire s_axi_awvalid,
    output wire s_axi_awready,
    input wire [63:0] s_axi_wdata,
    input wire [7:0] s_axi_wstrb,
    input wire s_axi_wlast,
    input wire s_axi_wvalid,
    output wire s_axi_wready,
    output wire [1:0] s_axi_bresp,
    output wire [ID_WIDTH-1:0] s_axi_bid,
    output wire s_axi_bvalid,
    input wire s_axi_bready,
    input wire [31:0] s_axi_araddr,
    input wire [ID_WIDTH-1:0] s_axi_arid,
    input wire [7:0] s_axi_arlen,
    input wire [2:0] s_axi_arsize,
    input wire [1:0] s_axi_arburst,
    input wire s_axi_arvalid,
    output wire s_axi_arready,
    output wire [63:0] s_axi_rdata,
    output wire [1:0] s_axi_rresp,
    output wire [ID_WIDTH-1:0] s_axi_rid,
    output wire s_axi_rlast,
    output wire s_axi_rvalid,
    input wire s_axi_rready
);
    initial begin
        if (RESPONSE_DELAY_NS < 0.0 || RESPONSE_DELAY_NS > 1.0)
            $fatal(1, "AXI response delay must be in [0,1] ns for this reference test");
        $display("AXI_RESPONSE_DELAY_NS=%0.3f instance=%m", RESPONSE_DELAY_NS);
    end
    wire  raw_s_axi_awready;
    assign #(RESPONSE_DELAY_NS) s_axi_awready = raw_s_axi_awready;
    wire  raw_s_axi_wready;
    assign #(RESPONSE_DELAY_NS) s_axi_wready = raw_s_axi_wready;
    wire [1:0] raw_s_axi_bresp;
    assign #(RESPONSE_DELAY_NS) s_axi_bresp = raw_s_axi_bresp;
    wire [ID_WIDTH-1:0] raw_s_axi_bid;
    assign #(RESPONSE_DELAY_NS) s_axi_bid = raw_s_axi_bid;
    wire  raw_s_axi_bvalid;
    assign #(RESPONSE_DELAY_NS) s_axi_bvalid = raw_s_axi_bvalid;
    wire  raw_s_axi_arready;
    assign #(RESPONSE_DELAY_NS) s_axi_arready = raw_s_axi_arready;
    wire [63:0] raw_s_axi_rdata;
    assign #(RESPONSE_DELAY_NS) s_axi_rdata = raw_s_axi_rdata;
    wire [1:0] raw_s_axi_rresp;
    assign #(RESPONSE_DELAY_NS) s_axi_rresp = raw_s_axi_rresp;
    wire [ID_WIDTH-1:0] raw_s_axi_rid;
    assign #(RESPONSE_DELAY_NS) s_axi_rid = raw_s_axi_rid;
    wire  raw_s_axi_rlast;
    assign #(RESPONSE_DELAY_NS) s_axi_rlast = raw_s_axi_rlast;
    wire  raw_s_axi_rvalid;
    assign #(RESPONSE_DELAY_NS) s_axi_rvalid = raw_s_axi_rvalid;

// AXI_RECEIVER_SETUP_CHECK_V1
`ifdef AXI_RX_TIMING_CHECK
    initial $display("AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=%m");
    // Handshake controls are checked every active clock. Payload only if VALID.
    axi_receiver_setup_check #(.WIDTH(5)) rx_control (
        .clk(clk), .rst_n(rst_n), .check_enable(1'b1),
        .observed({s_axi_awvalid,s_axi_wvalid,s_axi_arvalid,s_axi_bready,s_axi_rready}));
    axi_receiver_setup_check #(.WIDTH(45+ID_WIDTH)) rx_aw (
        .clk(clk), .rst_n(rst_n), .check_enable(s_axi_awvalid),
        .observed({s_axi_awaddr,s_axi_awid,s_axi_awlen,s_axi_awsize,s_axi_awburst}));
    axi_receiver_setup_check #(.WIDTH(73)) rx_w (
        .clk(clk), .rst_n(rst_n), .check_enable(s_axi_wvalid),
        .observed({s_axi_wdata,s_axi_wstrb,s_axi_wlast}));
    axi_receiver_setup_check #(.WIDTH(45+ID_WIDTH)) rx_ar (
        .clk(clk), .rst_n(rst_n), .check_enable(s_axi_arvalid),
        .observed({s_axi_araddr,s_axi_arid,s_axi_arlen,s_axi_arsize,s_axi_arburst}));
`endif

    axi_ram_model_core #(.ID_WIDTH(ID_WIDTH), .ALLOW_MMIO(ALLOW_MMIO),
        .BASE(BASE), .MEM_BYTES(MEM_BYTES)) core (
        .clk(clk),
        .rst_n(rst_n),
        .s_axi_awaddr(s_axi_awaddr),
        .s_axi_awid(s_axi_awid),
        .s_axi_awlen(s_axi_awlen),
        .s_axi_awsize(s_axi_awsize),
        .s_axi_awburst(s_axi_awburst),
        .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(raw_s_axi_awready),
        .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wlast(s_axi_wlast),
        .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(raw_s_axi_wready),
        .s_axi_bresp(raw_s_axi_bresp),
        .s_axi_bid(raw_s_axi_bid),
        .s_axi_bvalid(raw_s_axi_bvalid),
        .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr),
        .s_axi_arid(s_axi_arid),
        .s_axi_arlen(s_axi_arlen),
        .s_axi_arsize(s_axi_arsize),
        .s_axi_arburst(s_axi_arburst),
        .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(raw_s_axi_arready),
        .s_axi_rdata(raw_s_axi_rdata),
        .s_axi_rresp(raw_s_axi_rresp),
        .s_axi_rid(raw_s_axi_rid),
        .s_axi_rlast(raw_s_axi_rlast),
        .s_axi_rvalid(raw_s_axi_rvalid),
        .s_axi_rready(s_axi_rready)
    );
endmodule

`timescale 1ns/1ps
// Simulation-only external RAM. 64-bit AXI, one read and one write in flight.
// Supports aligned FIXED/INCR bursts, byte strobes and response backpressure.
// Each instance loads an identical byte image; no self-modifying code support.
module axi_ram_model_core #(
    parameter ID_WIDTH=4,
    parameter ALLOW_MMIO=0,
    parameter [31:0] BASE=32'h80000000,
    parameter MEM_BYTES=67108864
)(
    input wire clk,
    input wire rst_n,
    input wire [31:0] s_axi_awaddr,
    input wire [ID_WIDTH-1:0] s_axi_awid,
    input wire [7:0] s_axi_awlen,
    input wire [2:0] s_axi_awsize,
    input wire [1:0] s_axi_awburst,
    input wire s_axi_awvalid,
    output wire s_axi_awready,
    input wire [63:0] s_axi_wdata,
    input wire [7:0] s_axi_wstrb,
    input wire s_axi_wlast,
    input wire s_axi_wvalid,
    output wire s_axi_wready,
    output reg [1:0] s_axi_bresp,
    output reg [ID_WIDTH-1:0] s_axi_bid,
    output reg s_axi_bvalid,
    input wire s_axi_bready,
    input wire [31:0] s_axi_araddr,
    input wire [ID_WIDTH-1:0] s_axi_arid,
    input wire [7:0] s_axi_arlen,
    input wire [2:0] s_axi_arsize,
    input wire [1:0] s_axi_arburst,
    input wire s_axi_arvalid,
    output wire s_axi_arready,
    output reg [63:0] s_axi_rdata,
    output reg [1:0] s_axi_rresp,
    output reg [ID_WIDTH-1:0] s_axi_rid,
    output reg s_axi_rlast,
    output reg s_axi_rvalid,
    input wire s_axi_rready
);

    reg [7:0] mem [0:MEM_BYTES-1];
    reg wr_active = 0, rd_active = 0;
    reg [31:0] wr_addr, rd_addr;
    reg [7:0] wr_left, rd_left;
    reg [2:0] wr_size, rd_size;
    reg [1:0] wr_burst, rd_burst;
    reg [ID_WIDTH-1:0] wr_id, rd_id;
    string hex_file;
    integer fd, i, lane;
    reg [31:0] word_base;
    reg [31:0] done_value;
    reg [63:0] read_value;
    assign s_axi_awready = rst_n && !wr_active && !s_axi_bvalid;
    assign s_axi_wready  = rst_n && wr_active && !s_axi_bvalid;
    assign s_axi_arready = rst_n && !rd_active && !s_axi_rvalid;

    initial begin
        for (i=0; i<MEM_BYTES; i=i+1) mem[i]=0;
        if (!$value$plusargs("MEM_HEX=%s", hex_file)) hex_file="build/program.hex";
        if (!$test$plusargs("NO_HEX")) begin
            fd=$fopen(hex_file,"r");
            if (!fd) $fatal(1,"Cannot open firmware image %s",hex_file);
            $fclose(fd);
            $readmemh(hex_file,mem);
        end
    end

    function [31:0] next_addr(input [31:0] a, input [2:0] size,
                              input [1:0] burst);
        next_addr = burst==0 ? a : a+(32'd1<<size);
    endfunction

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_active<=0; rd_active<=0;
            s_axi_bvalid<=0; s_axi_bresp<=0; s_axi_bid<=0;
            s_axi_rvalid<=0; s_axi_rresp<=0; s_axi_rid<=0;
            s_axi_rdata<=0; s_axi_rlast<=0;
        end else begin
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid<=0;
            if (s_axi_awvalid && s_axi_awready) begin
                if (s_axi_awsize>3 || s_axi_awburst>1 ||
                    (s_axi_awaddr & ((32'd1<<s_axi_awsize)-1))!=0)
                    $fatal(1,"Unsupported RAM write geometry");
                wr_active<=1; wr_addr<=s_axi_awaddr; wr_id<=s_axi_awid;
                wr_left<=s_axi_awlen; wr_size<=s_axi_awsize; wr_burst<=s_axi_awburst;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                if (s_axi_wlast !== (wr_left==0)) $fatal(1,"WLAST mismatch");
                word_base={wr_addr[31:3],3'b000};
                if (ALLOW_MMIO && word_base==32'hd0580000) begin
                    if (s_axi_wstrb[0]) $write("%c",s_axi_wdata[7:0]);
                    if (|s_axi_wstrb[7:4]) begin
                        if (s_axi_wstrb[7:4]!=4'hf) $fatal(1,"Partial completion write");
                        done_value=s_axi_wdata[63:32];
                        if (done_value===32'h600dcafe) begin
                            $display("\nFULL_MODEL_CPU_RETURN_PASS");
                            #0; // allow receiver checks before successful termination
                            $finish;
                        end else begin #0; $fatal(1,"FULL_MODEL_CPU_RETURN_FAIL code=%08x",done_value); end
                    end
                end else begin
                    if (word_base<BASE || word_base-BASE>MEM_BYTES-8)
                        $fatal(1,"Unmapped write %08x",wr_addr);
                    for (lane=0; lane<8; lane=lane+1)
                        if (s_axi_wstrb[lane]) mem[word_base-BASE+lane]<=s_axi_wdata[8*lane+:8];
                end
                if (wr_left==0) begin
                    wr_active<=0; s_axi_bvalid<=1; s_axi_bresp<=0; s_axi_bid<=wr_id;
                end else begin
                    wr_left<=wr_left-1;
                    wr_addr<=next_addr(wr_addr,wr_size,wr_burst);
                end
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid<=0;
            if (s_axi_arvalid && s_axi_arready) begin
                if (s_axi_arsize>3 || s_axi_arburst>1 ||
                    (s_axi_araddr & ((32'd1<<s_axi_arsize)-1))!=0)
                    $fatal(1,"Unsupported RAM read geometry");
                rd_active<=1; rd_addr<=s_axi_araddr; rd_id<=s_axi_arid;
                rd_left<=s_axi_arlen; rd_size<=s_axi_arsize; rd_burst<=s_axi_arburst;
            end
            if (rd_active && (!s_axi_rvalid || s_axi_rready)) begin
                word_base={rd_addr[31:3],3'b000};
                if (word_base<BASE || word_base-BASE>MEM_BYTES-8)
                    $fatal(1,"Unmapped read %08x",rd_addr);
                for (lane=0; lane<8; lane=lane+1)
                    read_value[8*lane+:8]=mem[word_base-BASE+lane];
                s_axi_rdata<=read_value; s_axi_rid<=rd_id;
                s_axi_rresp<=0; s_axi_rvalid<=1; s_axi_rlast<=(rd_left==0);
                if (rd_left==0) rd_active<=0;
                else begin
                    rd_left<=rd_left-1;
                    rd_addr<=next_addr(rd_addr,rd_size,rd_burst);
                end
            end
        end
    end
endmodule


`timescale 1ns/1ps
// External receiver assumption: setup 1 ns, hold 0 ns, same sampling clock.
// Samples in the active region before NBA updates. This is not a cell model.
module axi_receiver_setup_check #(parameter WIDTH=1, parameter real SETUP_NS=1.0)(
    input wire clk, rst_n, check_enable,
    input wire [WIDTH-1:0] observed
);
    realtime last_change = -1.0e9;
    realtime age_ns;
    always @(observed) last_change = $realtime;
    always @(posedge clk) begin
        if (rst_n === 1'b1 && check_enable === 1'b1) begin
            if ($isunknown(observed))
                $fatal(1, "AXI_RX_UNKNOWN instance=%m time_ns=%0.3f", $realtime);
            age_ns = $realtime - last_change;
            if (age_ns + 0.0001 < SETUP_NS)
                $fatal(1, "AXI_RX_SETUP_FAIL instance=%m age_ns=%0.3f required_ns=%0.3f", age_ns, SETUP_NS);
        end
    end
endmodule
