`timescale 1ns/1ps
`include "common_defines.vh"

// Passive second top, shared by original RTL and the mapped soc_top.
// The unchanged tb_soc supplies clock/reset and external memories. This
// monitor checks the actual CPU program's PE transactions independently of
// the program's return value. Only accepted AXI payloads enter the trace.
module soc_program_monitor;
    localparam [31:0] PE_BASE = 32'h90000000;
    integer trace_fd;
    string trace_path;
    integer cycles = 0;
    integer ifu_reads = 0, ifu_returns = 0;
    integer mem_reads = 0, mem_writes = 0;
    integer pe_writes = 0, pe_responses = 0, pe_reads = 0;
    integer pe_read_requests = 0;
    integer i, read_index;
    integer read_seen [0:63];
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
    reg [31:0] actual_word, expected_word;
    reg [63:0] expected_read_bus;

    // The reference external RAM accepts one write transaction at a time.
    bit mem_aw_pending = 0;
    reg [31:0] mem_awaddr;
    reg [7:0] mem_awleft;
    reg [2:0] mem_awsize;
    reg [1:0] mem_awburst;

    initial begin
        if (!$value$plusargs("TRACE=%s", trace_path)) trace_path = "trace.tsv";
        trace_fd = $fopen(trace_path, "w");
        if (!trace_fd) $fatal(1, "SOC_PROGRAM_MONITOR_FAIL cannot open trace %s", trace_path);
        for (i = 0; i < 64; i = i + 1) read_seen[i] = 0;
        $fdisplay(trace_fd, "SOC_PROGRAM_TRACE_V1");
    end

    function [31:0] expected_write_address(input integer index);
        begin
            if (index == 0) expected_write_address = PE_BASE;
            else if (index == 1) expected_write_address = PE_BASE + 32'h008;
            else if (index >= 2 && index < 6)
                expected_write_address = PE_BASE + 32'h020 + 4*(index-2);
            else if (index == 6) expected_write_address = PE_BASE + 32'h010;
            else if (index >= 7 && index < 71)
                expected_write_address = PE_BASE + 32'h200 + 4*(index-7);
            else if (index >= 71 && index < 87)
                expected_write_address = PE_BASE + 32'h100 + 4*(index-71);
            else expected_write_address = 32'hxxxxxxxx;
        end
    endfunction

    function [31:0] expected_write_value(input integer index);
        begin
            if (index == 1 || index == 6) expected_write_value = 32'h0000ffff;
            else if (index >= 2 && index < 6) expected_write_value = 32'h01010101;
            else if (index >= 71 && index < 87) expected_write_value = 32'h05040302;
            else expected_write_value = 0;
        end
    endfunction

    task check_controls(input string name, input logic valid_bit, input logic ready_bit);
        begin
            if ($isunknown({valid_bit, ready_bit}))
                $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unknown AXI control %s cycle=%0d", name, cycles);
        end
    endtask

    task trace_address(input string name, input logic [31:0] address,
                       input logic [31:0] id, input logic [7:0] len,
                       input logic [2:0] size, input logic [1:0] burst);
        begin
            if ($isunknown({address, id, len, size, burst}))
                $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unknown address payload %s cycle=%0d", name, cycles);
            $fdisplay(trace_fd, "%0d\t%s\taddr=%08x\tid=%08x\tlen=%02x\tsize=%x\tburst=%x",
                      cycles, name, address, id, len, size, burst);
        end
    endtask

    task trace_write(input string name, input logic [63:0] data,
                     input logic [7:0] strb, input logic last_bit);
        reg [63:0] masked;
        integer byte_index;
        begin
            if ($isunknown({strb, last_bit}))
                $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unknown write control %s cycle=%0d", name, cycles);
            masked = 0;
            for (byte_index = 0; byte_index < 8; byte_index = byte_index + 1)
                if (strb[byte_index]) begin
                    if ($isunknown(data[8*byte_index +: 8]))
                        $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unknown active write byte %s cycle=%0d", name, cycles);
                    masked[8*byte_index +: 8] = data[8*byte_index +: 8];
                end
            $fdisplay(trace_fd, "%0d\t%s\tdata=%016x\tstrb=%02x\tlast=%x",
                      cycles, name, masked, strb, last_bit);
        end
    endtask

    task trace_response(input string name, input logic [31:0] id,
                        input logic [1:0] response);
        begin
            if ($isunknown({id, response}))
                $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unknown response payload %s cycle=%0d", name, cycles);
            $fdisplay(trace_fd, "%0d\t%s\tid=%08x\tresp=%x", cycles, name, id, response);
        end
    endtask

    task trace_read(input string name, input logic [63:0] data,
                    input logic [31:0] id, input logic [1:0] response,
                    input logic last_bit);
        begin
            if ($isunknown({data, id, response, last_bit}))
                $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unknown read payload %s cycle=%0d", name, cycles);
            $fdisplay(trace_fd, "%0d\t%s\tdata=%016x\tid=%08x\tresp=%x\tlast=%x",
                      cycles, name, data, id, response, last_bit);
        end
    endtask

    always @(posedge tb_soc.clk) begin
        if (tb_soc.rst_n === 1'b1) begin
            cycles = cycles + 1;
            completion_this_cycle = 0;
            if (completed) $fatal(1, "SOC_PROGRAM_MONITOR_FAIL execution continued after completion");

            // These checks apply to handshake controls every active cycle,
            // while payload checks only apply to transfers that take place.
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
                trace_address("IFU_AR", tb_soc.ifu_axi_araddr, tb_soc.ifu_axi_arid,
                              tb_soc.ifu_axi_arlen, tb_soc.ifu_axi_arsize, tb_soc.ifu_axi_arburst);
                ifu_reads = ifu_reads + 1;
            end
            if (tb_soc.ifu_axi_rvalid && tb_soc.ifu_axi_rready) begin
                trace_read("IFU_R", tb_soc.ifu_axi_rdata, tb_soc.ifu_axi_rid,
                           tb_soc.ifu_axi_rresp, tb_soc.ifu_axi_rlast);
                if (tb_soc.ifu_axi_rresp !== 0)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL instruction RAM response error");
                ifu_returns = ifu_returns + 1;
            end

            if (tb_soc.mem_axi_awvalid && tb_soc.mem_axi_awready) begin
                trace_address("MEM_AW", tb_soc.mem_axi_awaddr, tb_soc.mem_axi_awid,
                              tb_soc.mem_axi_awlen, tb_soc.mem_axi_awsize, tb_soc.mem_axi_awburst);
                if (mem_aw_pending) $fatal(1, "SOC_PROGRAM_MONITOR_FAIL overlapping external RAM writes");
                mem_aw_pending = 1;
                mem_awaddr = tb_soc.mem_axi_awaddr;
                mem_awleft = tb_soc.mem_axi_awlen;
                mem_awsize = tb_soc.mem_axi_awsize;
                mem_awburst = tb_soc.mem_axi_awburst;
            end
            if (tb_soc.mem_axi_wvalid && tb_soc.mem_axi_wready) begin
                trace_write("MEM_W", tb_soc.mem_axi_wdata, tb_soc.mem_axi_wstrb, tb_soc.mem_axi_wlast);
                if (!mem_aw_pending) $fatal(1, "SOC_PROGRAM_MONITOR_FAIL external RAM W without AW");
                if (tb_soc.mem_axi_wlast !== (mem_awleft == 0))
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL external RAM WLAST mismatch");
                mem_writes = mem_writes + 1;
                if ({mem_awaddr[31:3], 3'b000} == 32'hd0580000 &&
                    |tb_soc.mem_axi_wstrb[7:4]) begin
                    if (mem_awsize !== 3'd2 || tb_soc.mem_axi_wstrb !== 8'hf0 ||
                        tb_soc.mem_axi_wdata[63:32] !== 32'h600dcafe)
                        $fatal(1, "SOC_PROGRAM_MONITOR_FAIL CPU completion is not the expected passing store");
                    completion_this_cycle = 1;
                end
                if (mem_awleft == 0) mem_aw_pending = 0;
                else begin
                    mem_awleft = mem_awleft - 1;
                    if (mem_awburst != 0) mem_awaddr = mem_awaddr + (32'd1 << mem_awsize);
                end
            end
            if (tb_soc.mem_axi_bvalid && tb_soc.mem_axi_bready) begin
                trace_response("MEM_B", tb_soc.mem_axi_bid, tb_soc.mem_axi_bresp);
                if (tb_soc.mem_axi_bresp !== 0)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL external RAM write response error");
            end
            if (tb_soc.mem_axi_arvalid && tb_soc.mem_axi_arready) begin
                trace_address("MEM_AR", tb_soc.mem_axi_araddr, tb_soc.mem_axi_arid,
                              tb_soc.mem_axi_arlen, tb_soc.mem_axi_arsize, tb_soc.mem_axi_arburst);
                mem_reads = mem_reads + 1;
            end
            if (tb_soc.mem_axi_rvalid && tb_soc.mem_axi_rready) begin
                trace_read("MEM_R", tb_soc.mem_axi_rdata, tb_soc.mem_axi_rid,
                           tb_soc.mem_axi_rresp, tb_soc.mem_axi_rlast);
                if (tb_soc.mem_axi_rresp !== 0)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL external RAM read response error");
            end

            if (tb_soc.dut.u_pe.s_axi_awvalid && tb_soc.dut.u_pe.s_axi_awready) begin
                trace_address("PE_AW", tb_soc.dut.u_pe.s_axi_awaddr, tb_soc.dut.u_pe.s_axi_awid,
                              tb_soc.dut.u_pe.s_axi_awlen, tb_soc.dut.u_pe.s_axi_awsize,
                              tb_soc.dut.u_pe.s_axi_awburst);
                if (pe_aw_pending || pe_b_pending)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unexpected outstanding PE AW");
                if (tb_soc.dut.u_pe.s_axi_awlen !== 0 || tb_soc.dut.u_pe.s_axi_awsize !== 3'd2 ||
                    tb_soc.dut.u_pe.s_axi_awburst > 1)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE write must be one 32-bit beat");
                pe_awaddr = tb_soc.dut.u_pe.s_axi_awaddr;
                pe_awid = tb_soc.dut.u_pe.s_axi_awid;
                pe_aw_pending = 1;
            end
            if (tb_soc.dut.u_pe.s_axi_wvalid && tb_soc.dut.u_pe.s_axi_wready) begin
                trace_write("PE_W", tb_soc.dut.u_pe.s_axi_wdata, tb_soc.dut.u_pe.s_axi_wstrb,
                            tb_soc.dut.u_pe.s_axi_wlast);
                if (pe_w_pending || pe_b_pending || tb_soc.dut.u_pe.s_axi_wlast !== 1'b1)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unexpected outstanding PE W or WLAST");
                pe_wdata = tb_soc.dut.u_pe.s_axi_wdata;
                pe_wstrb = tb_soc.dut.u_pe.s_axi_wstrb;
                pe_w_pending = 1;
            end
            if (pe_aw_pending && pe_w_pending) begin
                if (pe_writes >= 87 || pe_awaddr !== expected_write_address(pe_writes))
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE configuration address index=%0d actual=%08x expected=%08x",
                           pe_writes, pe_awaddr, expected_write_address(pe_writes));
                if (pe_wstrb !== (pe_awaddr[2] ? 8'hf0 : 8'h0f))
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE byte enables do not match 32-bit CPU store");
                actual_word = pe_awaddr[2] ? pe_wdata[63:32] : pe_wdata[31:0];
                if (actual_word !== expected_write_value(pe_writes))
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE configuration value index=%0d actual=%08x expected=%08x",
                           pe_writes, actual_word, expected_write_value(pe_writes));
                pe_bid = pe_awid;
                pe_writes = pe_writes + 1;
                pe_aw_pending = 0;
                pe_w_pending = 0;
                pe_b_pending = 1;
            end
            if (tb_soc.dut.u_pe.s_axi_bvalid && tb_soc.dut.u_pe.s_axi_bready) begin
                trace_response("PE_B", tb_soc.dut.u_pe.s_axi_bid, tb_soc.dut.u_pe.s_axi_bresp);
                if (!pe_b_pending || tb_soc.dut.u_pe.s_axi_bid !== pe_bid ||
                    tb_soc.dut.u_pe.s_axi_bresp !== 0)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE B response mismatch");
                pe_responses = pe_responses + 1;
                pe_b_pending = 0;
            end
            if (tb_soc.dut.u_pe.s_axi_arvalid && tb_soc.dut.u_pe.s_axi_arready) begin
                trace_address("PE_AR", tb_soc.dut.u_pe.s_axi_araddr, tb_soc.dut.u_pe.s_axi_arid,
                              tb_soc.dut.u_pe.s_axi_arlen, tb_soc.dut.u_pe.s_axi_arsize,
                              tb_soc.dut.u_pe.s_axi_arburst);
                if (pe_read_pending || pe_read_requests >= 64 || pe_writes != 87 || pe_responses != 87)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL unexpected PE result read sequence");
                if (tb_soc.dut.u_pe.s_axi_araddr !== (PE_BASE + 32'h400 + 4*pe_read_requests) ||
                    tb_soc.dut.u_pe.s_axi_arlen !== 0 || tb_soc.dut.u_pe.s_axi_arsize !== 3'd2 ||
                    tb_soc.dut.u_pe.s_axi_arburst > 1)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE result address or geometry mismatch index=%0d", pe_read_requests);
                pe_araddr = tb_soc.dut.u_pe.s_axi_araddr;
                pe_arid = tb_soc.dut.u_pe.s_axi_arid;
                pe_read_pending = 1;
                pe_read_requests = pe_read_requests + 1;
            end
            if (tb_soc.dut.u_pe.s_axi_rvalid && tb_soc.dut.u_pe.s_axi_rready) begin
                trace_read("PE_R", tb_soc.dut.u_pe.s_axi_rdata, tb_soc.dut.u_pe.s_axi_rid,
                           tb_soc.dut.u_pe.s_axi_rresp, tb_soc.dut.u_pe.s_axi_rlast);
                if (!pe_read_pending || tb_soc.dut.u_pe.s_axi_rid !== pe_arid ||
                    tb_soc.dut.u_pe.s_axi_rresp !== 0 || tb_soc.dut.u_pe.s_axi_rlast !== 1'b1)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE result response metadata mismatch");
                read_index = (pe_araddr - PE_BASE - 32'h400) >> 2;
                expected_word = 16*((read_index % 4) + 2);
                expected_read_bus = (read_index % 4 < 2) ? 64'h0000003000000020 : 64'h0000005000000040;
                actual_word = pe_araddr[2] ? tb_soc.dut.u_pe.s_axi_rdata[63:32] : tb_soc.dut.u_pe.s_axi_rdata[31:0];
                if (actual_word !== expected_word || tb_soc.dut.u_pe.s_axi_rdata !== expected_read_bus)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL PE result index=%0d actual=%08x expected=%08x bus=%016x",
                           read_index, actual_word, expected_word, tb_soc.dut.u_pe.s_axi_rdata);
                if (read_seen[read_index] != 0)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL duplicate PE result read");
                read_seen[read_index] = 1;
                $fdisplay(trace_fd, "%0d\tPE_RESULT\tindex=%0d\taddr=%08x\tvalue=%08x",
                          cycles, read_index, pe_araddr, actual_word);
                pe_reads = pe_reads + 1;
                pe_read_pending = 0;
            end

            // axi_ram_model terminates after #0 on the same accepted MMIO
            // write, so this active-region check runs before its $finish.
            if (completion_this_cycle) begin
                if (pe_writes != 87 || pe_responses != 87 || pe_reads != 64 || pe_read_requests != 64 ||
                    pe_aw_pending || pe_w_pending || pe_b_pending || pe_read_pending ||
                    ifu_reads == 0 || ifu_returns == 0 || mem_reads == 0 || mem_writes == 0)
                    $fatal(1, "SOC_PROGRAM_MONITOR_FAIL incomplete program transaction coverage");
                for (i = 0; i < 64; i = i + 1)
                    if (read_seen[i] != 1) $fatal(1, "SOC_PROGRAM_MONITOR_FAIL missing PE word %0d", i);
                $fdisplay(trace_fd, "%0d\tCOMPLETE\tpe_reads=%0d\tpe_writes=%0d\tpe_responses=%0d\tifu_reads=%0d\tmem_reads=%0d\tmem_writes=%0d",
                          cycles, pe_reads, pe_writes, pe_responses, ifu_reads, mem_reads, mem_writes);
                $fclose(trace_fd);
                completed = 1;
                $display("SOC_PROGRAM_MONITOR_PASS pe_reads=%0d pe_writes=%0d pe_responses=%0d ifu_reads=%0d mem_reads=%0d mem_writes=%0d cycles=%0d",
                         pe_reads, pe_writes, pe_responses, ifu_reads, mem_reads, mem_writes, cycles);
            end
        end
    end
endmodule
