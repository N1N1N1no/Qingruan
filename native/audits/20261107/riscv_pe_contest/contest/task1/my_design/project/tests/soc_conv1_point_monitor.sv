`timescale 1ns/1ps
`include "common_defines.vh"

// Passive observation of real CPU AXI transfers.  No PE input or result is
// driven here, and expected values come from the independent Python vector.
module soc_conv1_point_monitor;
    localparam [31:0] PE_BASE = 32'h90000000;
    integer golden_fd, trace_fd, rc, i, j, index;
    integer cycles = 0;
    integer pe_writes = 0, pe_responses = 0;
    integer pe_read_requests = 0, pe_reads = 0;
    integer ifu_reads = 0, ifu_returns = 0;
    integer result_seen [0:2];
    reg [31:0] golden [0:2];
    reg [31:0] result_addr;
    string golden_path, trace_path;
    bit completed = 0;
    bit completion_this_cycle;

    bit pe_aw_pending = 0, pe_w_pending = 0, pe_b_pending = 0;
    reg [31:0] pe_awaddr;
    reg [`RV_LSU_BUS_TAG-1:0] pe_awid, pe_bid;
    reg [63:0] pe_wdata;
    reg [7:0] pe_wstrb;
    bit pe_read_pending = 0;
    reg [31:0] pe_araddr;
    reg [`RV_LSU_BUS_TAG-1:0] pe_arid;
    reg [31:0] actual_word;

    bit mem_aw_pending = 0;
    reg [31:0] mem_awaddr, word_address;
    reg [7:0] mem_awleft;
    reg [2:0] mem_awsize;
    reg [1:0] mem_awburst;
    reg [3:0] word_strb;
    reg [31:0] word_data;

    initial begin
        if (!$value$plusargs("GOLDEN_FILE=%s", golden_path))
            $fatal(1, "CONV1_POINT_MONITOR_FAIL missing GOLDEN_FILE");
        if (!$value$plusargs("RESULT_ADDR=%h", result_addr) ||
            $isunknown(result_addr) || result_addr[1:0] != 0 ||
            result_addr < 32'h80000000 || result_addr > 32'h8003fff4)
            $fatal(1, "CONV1_POINT_MONITOR_FAIL invalid RESULT_ADDR");
        golden_fd = $fopen(golden_path, "r");
        if (!golden_fd) $fatal(1, "CONV1_POINT_MONITOR_FAIL cannot open golden file");
        for (i = 0; i < 3; i = i + 1) begin
            rc = $fscanf(golden_fd, "%h", golden[i]);
            if (rc != 1 || $isunknown(golden[i]))
                $fatal(1, "CONV1_POINT_MONITOR_FAIL invalid golden word %0d", i);
            result_seen[i] = 0;
        end
        $fclose(golden_fd);
        if (!$value$plusargs("TRACE=%s", trace_path)) trace_path = "conv1_point_trace.tsv";
        trace_fd = $fopen(trace_path, "w");
        if (!trace_fd) $fatal(1, "CONV1_POINT_MONITOR_FAIL cannot open trace");
        $fdisplay(trace_fd, "CONV1_POINT_TRACE_V1");
        $display("CONV1_POINT_MONITOR_ENABLED result_addr=%08x partial0=%0d partial1=%0d acc=%0d",
                 result_addr, $signed(golden[0]), $signed(golden[1]), $signed(golden[2]));
    end

    task check_controls(input string name, input logic valid_bit, input logic ready_bit);
        if ($isunknown({valid_bit, ready_bit}))
            $fatal(1, "CONV1_POINT_MONITOR_FAIL unknown AXI control %s cycle=%0d", name, cycles);
    endtask

    function [31:0] expected_write_address(input integer n);
        integer local_index;
        begin
            if (n == 0) expected_write_address = PE_BASE;
            else if (n == 1) expected_write_address = PE_BASE + 32'h008;
            else if (n < 6) expected_write_address = PE_BASE + 32'h020 + 4*(n-2);
            else if (n < 70) expected_write_address = PE_BASE + 32'h200 + 4*(n-6);
            else begin
                local_index = (n-70) % 48;
                if (local_index < 32)
                    expected_write_address = PE_BASE + ((local_index % 2 == 0) ? 32'h020 : 32'h010);
                else expected_write_address = PE_BASE + 32'h100 + 4*(local_index-32);
            end
        end
    endfunction

    always @(posedge tb_soc.clk) begin
        if (tb_soc.rst_n === 1'b1) begin
            cycles = cycles + 1;
            completion_this_cycle = 0;
            if (completed) $fatal(1, "CONV1_POINT_MONITOR_FAIL execution after completion");
            check_controls("IFU_AR", tb_soc.ifu_axi_arvalid, tb_soc.ifu_axi_arready);
            check_controls("IFU_R", tb_soc.ifu_axi_rvalid, tb_soc.ifu_axi_rready);
            check_controls("MEM_AW", tb_soc.mem_axi_awvalid, tb_soc.mem_axi_awready);
            check_controls("MEM_W", tb_soc.mem_axi_wvalid, tb_soc.mem_axi_wready);
            check_controls("MEM_B", tb_soc.mem_axi_bvalid, tb_soc.mem_axi_bready);
            check_controls("MEM_AR", tb_soc.mem_axi_arvalid, tb_soc.mem_axi_arready);
            check_controls("MEM_R", tb_soc.mem_axi_rvalid, tb_soc.mem_axi_rready);
            check_controls("PE_AW", tb_soc.dut.u_pe.s_axi_awvalid, tb_soc.dut.u_pe.s_axi_awready);
            check_controls("PE_W", tb_soc.dut.u_pe.s_axi_wvalid, tb_soc.dut.u_pe.s_axi_wready);
            check_controls("PE_B", tb_soc.dut.u_pe.s_axi_bvalid, tb_soc.dut.u_pe.s_axi_bready);
            check_controls("PE_AR", tb_soc.dut.u_pe.s_axi_arvalid, tb_soc.dut.u_pe.s_axi_arready);
            check_controls("PE_R", tb_soc.dut.u_pe.s_axi_rvalid, tb_soc.dut.u_pe.s_axi_rready);
            if (tb_soc.ifu_axi_arvalid && tb_soc.ifu_axi_arready) begin
                if ($isunknown({tb_soc.ifu_axi_araddr, tb_soc.ifu_axi_arid, tb_soc.ifu_axi_arlen,
                                tb_soc.ifu_axi_arsize, tb_soc.ifu_axi_arburst}))
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL unknown IFU address");
                ifu_reads = ifu_reads + 1;
            end
            if (tb_soc.ifu_axi_rvalid && tb_soc.ifu_axi_rready) begin
                if ($isunknown({tb_soc.ifu_axi_rdata, tb_soc.ifu_axi_rid,
                                tb_soc.ifu_axi_rresp, tb_soc.ifu_axi_rlast}) || tb_soc.ifu_axi_rresp !== 0)
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL IFU response");
                ifu_returns = ifu_returns + 1;
            end
            if (tb_soc.mem_axi_arvalid && tb_soc.mem_axi_arready &&
                $isunknown({tb_soc.mem_axi_araddr, tb_soc.mem_axi_arid, tb_soc.mem_axi_arlen,
                            tb_soc.mem_axi_arsize, tb_soc.mem_axi_arburst}))
                $fatal(1, "CONV1_POINT_MONITOR_FAIL unknown RAM read request");
            if (tb_soc.mem_axi_rvalid && tb_soc.mem_axi_rready &&
                ($isunknown({tb_soc.mem_axi_rdata, tb_soc.mem_axi_rid,
                             tb_soc.mem_axi_rresp, tb_soc.mem_axi_rlast}) || tb_soc.mem_axi_rresp !== 0))
                $fatal(1, "CONV1_POINT_MONITOR_FAIL RAM read response");
            if (tb_soc.mem_axi_bvalid && tb_soc.mem_axi_bready &&
                ($isunknown({tb_soc.mem_axi_bid, tb_soc.mem_axi_bresp}) || tb_soc.mem_axi_bresp !== 0))
                $fatal(1, "CONV1_POINT_MONITOR_FAIL RAM write response");

            if (tb_soc.dut.u_pe.s_axi_awvalid && tb_soc.dut.u_pe.s_axi_awready) begin
                if ($isunknown({tb_soc.dut.u_pe.s_axi_awaddr, tb_soc.dut.u_pe.s_axi_awid,
                                tb_soc.dut.u_pe.s_axi_awlen, tb_soc.dut.u_pe.s_axi_awsize,
                                tb_soc.dut.u_pe.s_axi_awburst}) || pe_aw_pending || pe_b_pending ||
                    tb_soc.dut.u_pe.s_axi_awlen !== 0 || tb_soc.dut.u_pe.s_axi_awsize !== 3'd2 ||
                    tb_soc.dut.u_pe.s_axi_awburst > 1)
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL PE write address/geometry");
                pe_awaddr = tb_soc.dut.u_pe.s_axi_awaddr;
                pe_awid = tb_soc.dut.u_pe.s_axi_awid;
                pe_aw_pending = 1;
            end
            if (tb_soc.dut.u_pe.s_axi_wvalid && tb_soc.dut.u_pe.s_axi_wready) begin
                if ($isunknown({tb_soc.dut.u_pe.s_axi_wstrb, tb_soc.dut.u_pe.s_axi_wlast}) ||
                    pe_w_pending || pe_b_pending || tb_soc.dut.u_pe.s_axi_wlast !== 1'b1)
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL PE write payload control");
                pe_wdata = tb_soc.dut.u_pe.s_axi_wdata;
                pe_wstrb = tb_soc.dut.u_pe.s_axi_wstrb;
                pe_w_pending = 1;
            end
            if (pe_aw_pending && pe_w_pending) begin
                actual_word = pe_awaddr[2] ? pe_wdata[63:32] : pe_wdata[31:0];
                if (pe_writes >= 166 || pe_awaddr !== expected_write_address(pe_writes) ||
                    pe_wstrb !== (pe_awaddr[2] ? 8'hf0 : 8'h0f) || $isunknown(actual_word))
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL PE configuration index=%0d addr=%08x data=%08x",
                           pe_writes, pe_awaddr, actual_word);
                if (pe_writes < 70) begin
                    if (actual_word !== ((pe_writes == 1) ? 32'hffff : 32'h0))
                        $fatal(1, "CONV1_POINT_MONITOR_FAIL PE initial configuration");
                end else begin
                    j = (pe_writes-70) % 48;
                    if (j < 32 && j % 2 == 1) begin
                        if (actual_word !== (32'h1 << (j/2)))
                            $fatal(1, "CONV1_POINT_MONITOR_FAIL row write enable");
                    end else begin
                        if (actual_word[31:8] !== 0 || (j >= 32 && actual_word[7] !== 0))
                            $fatal(1, "CONV1_POINT_MONITOR_FAIL inactive weight/input lanes");
                        if (pe_writes >= 118 && ((j < 32 && j/2 >= 11) || j >= 43) && actual_word !== 0)
                            $fatal(1, "CONV1_POINT_MONITOR_FAIL nonzero padding");
                    end
                end
                $fdisplay(trace_fd, "%0d\tPE_WRITE\tindex=%0d\taddr=%08x\tvalue=%08x",
                          cycles, pe_writes, pe_awaddr, actual_word);
                pe_bid = pe_awid;
                pe_writes = pe_writes + 1;
                pe_aw_pending = 0;
                pe_w_pending = 0;
                pe_b_pending = 1;
            end
            if (tb_soc.dut.u_pe.s_axi_bvalid && tb_soc.dut.u_pe.s_axi_bready) begin
                if (!pe_b_pending || tb_soc.dut.u_pe.s_axi_bid !== pe_bid ||
                    tb_soc.dut.u_pe.s_axi_bresp !== 0)
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL PE B response");
                pe_responses = pe_responses + 1;
                pe_b_pending = 0;
            end
            if (tb_soc.dut.u_pe.s_axi_arvalid && tb_soc.dut.u_pe.s_axi_arready) begin
                if ($isunknown({tb_soc.dut.u_pe.s_axi_arid, tb_soc.dut.u_pe.s_axi_arburst}) ||
                    pe_read_pending || pe_read_requests >= 2 || pe_reads != pe_read_requests ||
                    pe_writes != 118 + 48*pe_read_requests || pe_responses != pe_writes ||
                    tb_soc.dut.u_pe.s_axi_araddr !== PE_BASE + 32'h400 ||
                    tb_soc.dut.u_pe.s_axi_arlen !== 0 || tb_soc.dut.u_pe.s_axi_arsize !== 3'd2 ||
                    tb_soc.dut.u_pe.s_axi_arburst > 1)
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL PE result request sequence");
                pe_araddr = tb_soc.dut.u_pe.s_axi_araddr;
                pe_arid = tb_soc.dut.u_pe.s_axi_arid;
                pe_read_pending = 1;
                pe_read_requests = pe_read_requests + 1;
            end
            if (tb_soc.dut.u_pe.s_axi_rvalid && tb_soc.dut.u_pe.s_axi_rready) begin
                if (!pe_read_pending || pe_reads >= 2 || tb_soc.dut.u_pe.s_axi_rid !== pe_arid ||
                    tb_soc.dut.u_pe.s_axi_rresp !== 0 || tb_soc.dut.u_pe.s_axi_rlast !== 1'b1 ||
                    tb_soc.dut.u_pe.s_axi_rdata !== {32'h0, golden[pe_reads]})
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL PE partial index=%0d actual=%016x expected=%08x",
                           pe_reads, tb_soc.dut.u_pe.s_axi_rdata, golden[pe_reads]);
                $fdisplay(trace_fd, "%0d\tPE_RESULT\tindex=%0d\tvalue=%08x", cycles,
                          pe_reads, tb_soc.dut.u_pe.s_axi_rdata[31:0]);
                $display("CONV1_POINT_PE_PARTIAL index=%0d value=%0d", pe_reads,
                         $signed(tb_soc.dut.u_pe.s_axi_rdata[31:0]));
                pe_reads = pe_reads + 1;
                pe_read_pending = 0;
            end

            if (tb_soc.mem_axi_awvalid && tb_soc.mem_axi_awready) begin
                if (mem_aw_pending || $isunknown({tb_soc.mem_axi_awaddr, tb_soc.mem_axi_awid,
                    tb_soc.mem_axi_awlen, tb_soc.mem_axi_awsize, tb_soc.mem_axi_awburst}))
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL external RAM AW");
                mem_awaddr = tb_soc.mem_axi_awaddr;
                mem_awleft = tb_soc.mem_axi_awlen;
                mem_awsize = tb_soc.mem_axi_awsize;
                mem_awburst = tb_soc.mem_axi_awburst;
                mem_aw_pending = 1;
            end
            if (tb_soc.mem_axi_wvalid && tb_soc.mem_axi_wready) begin
                if (!mem_aw_pending || $isunknown({tb_soc.mem_axi_wstrb, tb_soc.mem_axi_wlast}) ||
                    tb_soc.mem_axi_wlast !== (mem_awleft == 0))
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL external RAM W");
                for (i = 0; i < 8; i = i + 1)
                    if (tb_soc.mem_axi_wstrb[i] && $isunknown(tb_soc.mem_axi_wdata[8*i +: 8]))
                        $fatal(1, "CONV1_POINT_MONITOR_FAIL unknown active RAM write byte");
                for (i = 0; i < 2; i = i + 1) begin
                    word_address = {mem_awaddr[31:3], 3'b000} + 4*i;
                    word_strb = tb_soc.mem_axi_wstrb[4*i +: 4];
                    word_data = tb_soc.mem_axi_wdata[32*i +: 32];
                    if (word_strb != 0 && word_address >= result_addr && word_address <= result_addr + 8) begin
                        index = (word_address-result_addr) >> 2;
                        // The CPU may coalesce adjacent ordinary-RAM stores
                        // into a 64-bit transfer, unlike side-effect PE MMIO.
                        // Inspect each fully enabled target word in any legal
                        // 32/64-bit beat, including beats within a RAM burst.
                        if (word_strb !== 4'hf ||
                            (mem_awsize !== 3'd2 && mem_awsize !== 3'd3))
                            $fatal(1, "CONV1_POINT_MONITOR_FAIL result store geometry awaddr=%08x size=%0d lenleft=%0d strb=%02x wordaddr=%08x wordstrb=%x",
                                   mem_awaddr, mem_awsize, mem_awleft, tb_soc.mem_axi_wstrb,
                                   word_address, word_strb);
                        // start.S clears these BSS words before it calls main.
                        if (pe_reads == 0) begin
                            if (word_data !== 0) $fatal(1, "CONV1_POINT_MONITOR_FAIL BSS initialization");
                        end else begin
                            if (result_seen[index] != 0 || word_data !== golden[index] ||
                                (index == 0 && pe_reads != 1) || (index != 0 && pe_reads != 2))
                                $fatal(1, "CONV1_POINT_MONITOR_FAIL CPU result index=%0d actual=%08x expected=%08x",
                                       index, word_data, golden[index]);
                            result_seen[index] = 1;
                            $fdisplay(trace_fd, "%0d\tCPU_RESULT\tindex=%0d\taddr=%08x\tvalue=%08x",
                                      cycles, index, word_address, word_data);
                            $display("CONV1_POINT_CPU_RESULT index=%0d value=%0d", index, $signed(word_data));
                        end
                    end
                end
                if ({mem_awaddr[31:3], 3'b000} == 32'hd0580000 && |tb_soc.mem_axi_wstrb[7:4]) begin
                    if (mem_awsize !== 3'd2 || tb_soc.mem_axi_wstrb !== 8'hf0 ||
                        tb_soc.mem_axi_wdata[63:32] !== 32'h600dcafe)
                        $fatal(1, "CONV1_POINT_MONITOR_FAIL CPU completion code=%08x", tb_soc.mem_axi_wdata[63:32]);
                    completion_this_cycle = 1;
                end
                if (mem_awleft == 0) mem_aw_pending = 0;
                else begin
                    mem_awleft = mem_awleft - 1;
                    if (mem_awburst != 0) mem_awaddr = mem_awaddr + (32'd1 << mem_awsize);
                end
            end
            if (completion_this_cycle) begin
                if (pe_writes != 166 || pe_responses != 166 || pe_read_requests != 2 || pe_reads != 2 ||
                    pe_aw_pending || pe_w_pending || pe_b_pending || pe_read_pending || mem_aw_pending ||
                    ifu_reads == 0 || ifu_returns == 0)
                    $fatal(1, "CONV1_POINT_MONITOR_FAIL incomplete CPU transaction coverage");
                for (i = 0; i < 3; i = i + 1)
                    if (result_seen[i] != 1) $fatal(1, "CONV1_POINT_MONITOR_FAIL missing CPU result %0d", i);
                $fdisplay(trace_fd, "%0d\tCOMPLETE\tpe_reads=%0d\tpe_writes=%0d\tpe_responses=%0d\tacc=%08x",
                          cycles, pe_reads, pe_writes, pe_responses, golden[2]);
                $fclose(trace_fd);
                completed = 1;
                $display("CONV1_POINT_MONITOR_PASS pe_reads=%0d pe_writes=%0d pe_responses=%0d cycles=%0d acc=%0d",
                         pe_reads, pe_writes, pe_responses, cycles, $signed(golden[2]));
            end
        end
    end
endmodule
