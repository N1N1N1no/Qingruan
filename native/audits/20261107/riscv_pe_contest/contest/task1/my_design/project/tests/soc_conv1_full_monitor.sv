`timescale 1ns/1ps
`include "common_defines.vh"

// Passive, bounded-output check of full Conv1 and first maximum-pool layer.
// Data and weights are independently supplied files, never DUT stimuli.
// Tile order: group*4096 + pool_row*64 + pool_col; each tile has 16 channels
// and four spatial lanes. Both 16+11-term partial sums are checked on AXI.
module soc_conv1_full_monitor;
    localparam [31:0] PE_BASE = 32'h90000000;
    localparam integer TOTAL_TILES = 8192;
    localparam integer OUTPUT_BYTES = 131072;
    localparam integer PARTIAL_BYTES = 4194304;

    reg [7:0] image [0:50699];
    // Term-major layout [27][32], matching generated firmware headers.
    reg [7:0] weights [0:863];
    reg [7:0] partials [0:PARTIAL_BYTES-1];
    reg [7:0] pool_golden [0:OUTPUT_BYTES-1];
    reg [7:0] observed [0:OUTPUT_BYTES-1];
    bit output_seen [0:OUTPUT_BYTES-1];
    integer tile_stores [0:TOTAL_TILES-1];
    integer tile_start = 0, tile_count = TOTAL_TILES;
    integer cycles = 0, pe_writes = 0, pe_responses = 0;
    integer pe_reads = 0, pe_read_requests = 0;
    integer output_writes = 0, tiles_completed = 0;
    integer ifu_reads = 0, ifu_returns = 0;
    integer i, j, handle, count, trace_fd, observed_fd, partial_fd;
    integer local_tile, tile, batch, read_index, pair_index, partial_offset;
    integer byte_index, byte_address, output_index, output_tile, output_column;
    integer required_reads;
    reg [31:0] result_addr;
    string input_path, weight_path, partial_path, pool_path, trace_path, observed_path, observed_partial_path;
    bit completed = 0, completion_this_cycle;

    bit pe_aw_pending = 0, pe_w_pending = 0, pe_b_pending = 0;
    reg [31:0] pe_awaddr;
    reg [`RV_LSU_BUS_TAG-1:0] pe_awid, pe_bid;
    reg [63:0] pe_wdata;
    reg [7:0] pe_wstrb;
    bit pe_read_pending = 0;
    reg [31:0] pe_araddr;
    reg [`RV_LSU_BUS_TAG-1:0] pe_arid;
    reg [31:0] actual_word, expected_word;
    reg [63:0] expected_bus;

    bit mem_aw_pending = 0;
    reg [31:0] mem_awaddr;
    reg [7:0] mem_awleft;
    reg [2:0] mem_awsize;
    reg [1:0] mem_awburst;

    initial begin
        if (!$value$plusargs("TILE_START=%d", tile_start)) tile_start = 0;
        if (!$value$plusargs("TILE_COUNT=%d", tile_count)) tile_count = TOTAL_TILES;
        if (tile_start < 0 || tile_count < 1 || tile_start + tile_count > TOTAL_TILES)
            $fatal(1, "CONV1_FULL_MONITOR_FAIL invalid tile range");
        if (!$value$plusargs("RESULT_ADDR=%h", result_addr) || $isunknown(result_addr) ||
            result_addr < 32'h80000000 || result_addr > 32'h80020000)
            $fatal(1, "CONV1_FULL_MONITOR_FAIL invalid output address");
        if (!$value$plusargs("INPUT_FILE=%s", input_path) ||
            !$value$plusargs("WEIGHT_FILE=%s", weight_path) ||
            !$value$plusargs("PARTIAL_FILE=%s", partial_path) ||
            !$value$plusargs("POOL_FILE=%s", pool_path) ||
            !$value$plusargs("OBSERVED_FILE=%s", observed_path))
            $fatal(1, "CONV1_FULL_MONITOR_FAIL missing binary file argument");
        handle = $fopen(input_path, "rb");
        if (!handle) $fatal(1, "CONV1_FULL_MONITOR_FAIL cannot open input binary");
        count = $fread(image, handle);
        if (count != 50700 || $fgetc(handle) != -1)
            $fatal(1, "CONV1_FULL_MONITOR_FAIL input binary length %0d", count);
        $fclose(handle);
        handle = $fopen(weight_path, "rb");
        if (!handle) $fatal(1, "CONV1_FULL_MONITOR_FAIL cannot open weight binary");
        count = $fread(weights, handle);
        if (count != 864 || $fgetc(handle) != -1)
            $fatal(1, "CONV1_FULL_MONITOR_FAIL weight binary length %0d", count);
        $fclose(handle);
        handle = $fopen(partial_path, "rb");
        if (!handle) $fatal(1, "CONV1_FULL_MONITOR_FAIL cannot open partial binary");
        count = $fread(partials, handle);
        if (count != PARTIAL_BYTES || $fgetc(handle) != -1)
            $fatal(1, "CONV1_FULL_MONITOR_FAIL partial binary length %0d", count);
        $fclose(handle);
        handle = $fopen(pool_path, "rb");
        if (!handle) $fatal(1, "CONV1_FULL_MONITOR_FAIL cannot open pool binary");
        count = $fread(pool_golden, handle);
        if (count != OUTPUT_BYTES || $fgetc(handle) != -1)
            $fatal(1, "CONV1_FULL_MONITOR_FAIL pool binary length %0d", count);
        $fclose(handle);
        for (i = 0; i < OUTPUT_BYTES; i = i + 1) begin
            output_seen[i] = 0;
            observed[i] = 0;
        end
        for (i = 0; i < TOTAL_TILES; i = i + 1) tile_stores[i] = 0;
        if (!$value$plusargs("TRACE=%s", trace_path)) trace_path = "conv1_full_tiles.tsv";
        trace_fd = $fopen(trace_path, "w");
        if (!trace_fd) $fatal(1, "CONV1_FULL_MONITOR_FAIL cannot open tile trace");
        if (!$value$plusargs("OBSERVED_PARTIAL_FILE=%s", observed_partial_path))
            observed_partial_path = "observed_partials.bin";
        partial_fd = $fopen(observed_partial_path, "wb");
        if (!partial_fd) $fatal(1, "CONV1_FULL_MONITOR_FAIL cannot open observed partial binary");
        $fdisplay(trace_fd, "CONV1_FULL_TILE_TRACE_V1");
        $display("CONV1_FULL_MONITOR_ENABLED tile_start=%0d tile_count=%0d output_addr=%08x",
                 tile_start, tile_count, result_addr);
    end

    function [31:0] expected_write_address(input integer n);
        integer k;
        begin
            if (n == 0) expected_write_address = PE_BASE + 32'h008;
            else if (n < 65) expected_write_address = PE_BASE + 32'h200 + 4*(n-1);
            else if (n < 389) begin
                k = (n-65) % 81;
                if (k == 0) expected_write_address = PE_BASE;
                else if ((k-1) % 5 == 4) expected_write_address = PE_BASE + 32'h010;
                else expected_write_address = PE_BASE + 32'h020 + 4*((k-1) % 5);
            end else begin
                k = (n-389) % 17;
                if (k == 0) expected_write_address = PE_BASE;
                else expected_write_address = PE_BASE + 32'h100 + 4*(k-1);
            end
        end
    endfunction

    function [31:0] expected_write_value(input integer n);
        integer k, slot, row, word_number, term, channel, lane;
        integer tile_number, group_number, pool_row, pool_column;
        integer input_channel, kernel_row, kernel_column, source_index;
        reg [31:0] value;
        begin
            value = 0;
            if (n == 0) value = 32'hffff;
            else if (n < 65) value = 0;
            else if (n < 389) begin
                slot = (n-65) / 81;
                k = (n-65) % 81;
                if (k == 0) value = slot;
                else begin
                    row = (k-1) / 5;
                    word_number = (k-1) % 5;
                    if (word_number == 4) value = 32'h1 << row;
                    else begin
                        term = 16*(slot % 2) + row;
                        for (lane = 0; lane < 4; lane = lane + 1) begin
                            channel = (slot/2)*16 + word_number*4 + lane;
                            if (term < 27) value[8*lane +: 8] = weights[term*32+channel];
                        end
                    end
                end
            end else begin
                tile_number = tile_start + (n-389)/34;
                group_number = tile_number / 4096;
                k = (n-389) % 34;
                slot = group_number*2 + k/17;
                if (k % 17 == 0) value = slot;
                else begin
                    row = k % 17 - 1;
                    term = (slot % 2)*16 + row;
                    if (term < 27) begin
                        pool_row = (tile_number % 4096)/64;
                        pool_column = tile_number % 64;
                        input_channel = term/9;
                        kernel_row = (term % 9)/3;
                        kernel_column = term % 3;
                        for (lane = 0; lane < 4; lane = lane + 1) begin
                            source_index = input_channel*16900 +
                                (2*pool_row + lane/2 + kernel_row)*130 +
                                2*pool_column + lane%2 + kernel_column;
                            value[8*lane +: 8] = image[source_index];
                        end
                    end
                end
            end
            expected_write_value = value;
        end
    endfunction

    always @(posedge tb_soc.clk) begin
        if (tb_soc.rst_n === 1'b1) begin
            cycles = cycles + 1;
            completion_this_cycle = 0;
            if (completed) $fatal(1, "CONV1_FULL_MONITOR_FAIL execution after completion");
            // Check payloads only on accepted transactions. Avoid expensive
            // per-cycle helper calls over the millions of full-layer cycles.
            if (tb_soc.ifu_axi_arvalid && tb_soc.ifu_axi_arready) begin
                if ($isunknown({tb_soc.ifu_axi_araddr, tb_soc.ifu_axi_arid, tb_soc.ifu_axi_arlen,
                                tb_soc.ifu_axi_arsize, tb_soc.ifu_axi_arburst}))
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL unknown IFU address");
                ifu_reads = ifu_reads + 1;
            end
            if (tb_soc.ifu_axi_rvalid && tb_soc.ifu_axi_rready) begin
                if ($isunknown({tb_soc.ifu_axi_rdata, tb_soc.ifu_axi_rid,
                                tb_soc.ifu_axi_rresp, tb_soc.ifu_axi_rlast}) || tb_soc.ifu_axi_rresp !== 0)
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL IFU response");
                ifu_returns = ifu_returns + 1;
            end
            if (tb_soc.mem_axi_arvalid && tb_soc.mem_axi_arready &&
                $isunknown({tb_soc.mem_axi_araddr, tb_soc.mem_axi_arid, tb_soc.mem_axi_arlen,
                            tb_soc.mem_axi_arsize, tb_soc.mem_axi_arburst}))
                $fatal(1, "CONV1_FULL_MONITOR_FAIL unknown RAM read request");
            if (tb_soc.mem_axi_rvalid && tb_soc.mem_axi_rready &&
                ($isunknown({tb_soc.mem_axi_rdata, tb_soc.mem_axi_rid,
                             tb_soc.mem_axi_rresp, tb_soc.mem_axi_rlast}) || tb_soc.mem_axi_rresp !== 0))
                $fatal(1, "CONV1_FULL_MONITOR_FAIL RAM read response");
            if (tb_soc.mem_axi_bvalid && tb_soc.mem_axi_bready &&
                ($isunknown({tb_soc.mem_axi_bid, tb_soc.mem_axi_bresp}) || tb_soc.mem_axi_bresp !== 0))
                $fatal(1, "CONV1_FULL_MONITOR_FAIL RAM write response");

            if (tb_soc.dut.u_pe.s_axi_awvalid && tb_soc.dut.u_pe.s_axi_awready) begin
                if ($isunknown({tb_soc.dut.u_pe.s_axi_awaddr, tb_soc.dut.u_pe.s_axi_awid,
                                tb_soc.dut.u_pe.s_axi_awlen, tb_soc.dut.u_pe.s_axi_awsize,
                                tb_soc.dut.u_pe.s_axi_awburst}) || pe_aw_pending || pe_b_pending ||
                    tb_soc.dut.u_pe.s_axi_awlen !== 0 || tb_soc.dut.u_pe.s_axi_awsize !== 3'd2 ||
                    tb_soc.dut.u_pe.s_axi_awburst > 1)
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL PE write address/geometry");
                pe_awaddr = tb_soc.dut.u_pe.s_axi_awaddr;
                pe_awid = tb_soc.dut.u_pe.s_axi_awid;
                pe_aw_pending = 1;
            end
            if (tb_soc.dut.u_pe.s_axi_wvalid && tb_soc.dut.u_pe.s_axi_wready) begin
                if ($isunknown({tb_soc.dut.u_pe.s_axi_wstrb, tb_soc.dut.u_pe.s_axi_wlast}) ||
                    pe_w_pending || pe_b_pending || tb_soc.dut.u_pe.s_axi_wlast !== 1'b1)
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL PE write payload control");
                pe_wdata = tb_soc.dut.u_pe.s_axi_wdata;
                pe_wstrb = tb_soc.dut.u_pe.s_axi_wstrb;
                pe_w_pending = 1;
            end
            if (pe_aw_pending && pe_w_pending) begin
                actual_word = pe_awaddr[2] ? pe_wdata[63:32] : pe_wdata[31:0];
                expected_word = expected_write_value(pe_writes);
                if (pe_writes >= 389+34*tile_count || pe_awaddr !== expected_write_address(pe_writes) ||
                    pe_wstrb !== (pe_awaddr[2] ? 8'hf0 : 8'h0f) || actual_word !== expected_word) begin
                    if (pe_writes >= 65 && pe_writes < 389)
                        $fatal(1, "CONV1_FULL_MONITOR_FAIL weight configuration index=%0d addr=%08x data=%08x expected_addr=%08x expected_data=%08x",
                               pe_writes, pe_awaddr, actual_word, expected_write_address(pe_writes), expected_word);
                    else
                        $fatal(1, "CONV1_FULL_MONITOR_FAIL PE configuration index=%0d addr=%08x data=%08x expected_addr=%08x expected_data=%08x",
                               pe_writes, pe_awaddr, actual_word, expected_write_address(pe_writes), expected_word);
                end
                pe_bid = pe_awid;
                pe_writes = pe_writes + 1;
                pe_aw_pending = 0;
                pe_w_pending = 0;
                pe_b_pending = 1;
            end
            if (tb_soc.dut.u_pe.s_axi_bvalid && tb_soc.dut.u_pe.s_axi_bready) begin
                if (!pe_b_pending || tb_soc.dut.u_pe.s_axi_bid !== pe_bid ||
                    tb_soc.dut.u_pe.s_axi_bresp !== 0)
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL PE B response");
                pe_responses = pe_responses + 1;
                pe_b_pending = 0;
            end
            if (tb_soc.dut.u_pe.s_axi_arvalid && tb_soc.dut.u_pe.s_axi_arready) begin
                local_tile = pe_read_requests/128;
                batch = (pe_read_requests % 128)/64;
                read_index = pe_read_requests % 64;
                if ($isunknown({tb_soc.dut.u_pe.s_axi_arid, tb_soc.dut.u_pe.s_axi_arburst}) ||
                    pe_read_pending || pe_read_requests >= tile_count*128 || pe_reads != pe_read_requests ||
                    pe_writes != 389 + local_tile*34 + (batch+1)*17 || pe_responses != pe_writes ||
                    tb_soc.dut.u_pe.s_axi_araddr !== PE_BASE + 32'h400 + 4*read_index ||
                    tb_soc.dut.u_pe.s_axi_arlen !== 0 || tb_soc.dut.u_pe.s_axi_arsize !== 3'd2 ||
                    tb_soc.dut.u_pe.s_axi_arburst > 1)
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL PE result request index=%0d writes=%0d responses=%0d",
                           pe_read_requests, pe_writes, pe_responses);
                pe_araddr = tb_soc.dut.u_pe.s_axi_araddr;
                pe_arid = tb_soc.dut.u_pe.s_axi_arid;
                pe_read_pending = 1;
                pe_read_requests = pe_read_requests + 1;
            end
            if (tb_soc.dut.u_pe.s_axi_rvalid && tb_soc.dut.u_pe.s_axi_rready) begin
                tile = tile_start + pe_reads/128;
                batch = (pe_reads % 128)/64;
                read_index = pe_reads % 64;
                pair_index = (read_index/2)*2;
                partial_offset = ((tile*2+batch)*64+pair_index)*4;
                expected_bus = 0;
                for (j = 0; j < 8; j = j + 1)
                    expected_bus[8*j +: 8] = partials[partial_offset+j];
                if (!pe_read_pending || pe_reads >= tile_count*128 ||
                    tb_soc.dut.u_pe.s_axi_rid !== pe_arid || tb_soc.dut.u_pe.s_axi_rresp !== 0 ||
                    tb_soc.dut.u_pe.s_axi_rlast !== 1'b1 || tb_soc.dut.u_pe.s_axi_rdata !== expected_bus)
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL PE partial tile=%0d batch=%0d index=%0d actual=%016x expected=%016x",
                           tile, batch, read_index, tb_soc.dut.u_pe.s_axi_rdata, expected_bus);
                actual_word = pe_araddr[2] ? tb_soc.dut.u_pe.s_axi_rdata[63:32] : tb_soc.dut.u_pe.s_axi_rdata[31:0];
                for (j = 0; j < 4; j = j + 1) $fwrite(partial_fd, "%c", actual_word[8*j +: 8]);
                pe_reads = pe_reads + 1;
                pe_read_pending = 0;
            end

            if (tb_soc.mem_axi_awvalid && tb_soc.mem_axi_awready) begin
                if (mem_aw_pending || $isunknown({tb_soc.mem_axi_awaddr, tb_soc.mem_axi_awid,
                    tb_soc.mem_axi_awlen, tb_soc.mem_axi_awsize, tb_soc.mem_axi_awburst}))
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL external RAM AW");
                mem_awaddr = tb_soc.mem_axi_awaddr;
                mem_awleft = tb_soc.mem_axi_awlen;
                mem_awsize = tb_soc.mem_axi_awsize;
                mem_awburst = tb_soc.mem_axi_awburst;
                mem_aw_pending = 1;
            end
            if (tb_soc.mem_axi_wvalid && tb_soc.mem_axi_wready) begin
                if (!mem_aw_pending || $isunknown({tb_soc.mem_axi_wstrb, tb_soc.mem_axi_wlast}) ||
                    tb_soc.mem_axi_wlast !== (mem_awleft == 0))
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL external RAM W");
                for (byte_index = 0; byte_index < 8; byte_index = byte_index + 1)
                    if (tb_soc.mem_axi_wstrb[byte_index]) begin
                        if ($isunknown(tb_soc.mem_axi_wdata[8*byte_index +: 8]))
                            $fatal(1, "CONV1_FULL_MONITOR_FAIL unknown active RAM write byte");
                        byte_address = {mem_awaddr[31:3], 3'b000} + byte_index;
                        if ($unsigned(byte_address) >= result_addr &&
                            $unsigned(byte_address) < result_addr + OUTPUT_BYTES) begin
                            output_index = $unsigned(byte_address)-result_addr;
                            if (pe_writes == 0) begin
                                if (tb_soc.mem_axi_wdata[8*byte_index +: 8] !== 0)
                                    $fatal(1, "CONV1_FULL_MONITOR_FAIL output BSS initialization");
                            end else begin
                                output_tile = (output_index/65536)*4096 + output_index%4096;
                                output_column = (output_index/4096) % 16;
                                required_reads = (output_tile-tile_start)*128 + 64 + 4*(output_column+1);
                                if (output_tile < tile_start || output_tile >= tile_start+tile_count ||
                                    pe_reads < required_reads || output_seen[output_index] ||
                                    tb_soc.mem_axi_wdata[8*byte_index +: 8] !== pool_golden[output_index])
                                    $fatal(1, "CONV1_FULL_MONITOR_FAIL CPU output index=%0d tile=%0d column=%0d actual=%02x expected=%02x reads=%0d required_reads=%0d",
                                           output_index, output_tile, output_column,
                                           tb_soc.mem_axi_wdata[8*byte_index +: 8], pool_golden[output_index],
                                           pe_reads, required_reads);
                                output_seen[output_index] = 1;
                                observed[output_index] = tb_soc.mem_axi_wdata[8*byte_index +: 8];
                                output_writes = output_writes + 1;
                                tile_stores[output_tile] = tile_stores[output_tile] + 1;
                                if (tile_stores[output_tile] == 16) begin
                                    tiles_completed = tiles_completed + 1;
                                    $fdisplay(trace_fd, "%0d\tTILE\tindex=%0d\tcompleted=%0d\toutput_bytes=%0d\tpe_reads=%0d\tpe_writes=%0d",
                                              cycles, output_tile, tiles_completed, output_writes, pe_reads, pe_writes);
                                    if (tiles_completed % 128 == 0 || tiles_completed == tile_count) begin
                                        $display("CONV1_FULL_PROGRESS tiles=%0d/%0d last_tile=%0d output_bytes=%0d cycles=%0d",
                                                 tiles_completed, tile_count, output_tile, output_writes, cycles);
                                        $fflush(trace_fd);
                                    end
                                end
                            end
                        end
                    end
                if ({mem_awaddr[31:3], 3'b000} == 32'hd0580000 && |tb_soc.mem_axi_wstrb[7:4]) begin
                    if (mem_awsize !== 3'd2 || tb_soc.mem_axi_wstrb !== 8'hf0 ||
                        tb_soc.mem_axi_wdata[63:32] !== 32'h600dcafe)
                        $fatal(1, "CONV1_FULL_MONITOR_FAIL CPU completion code=%08x", tb_soc.mem_axi_wdata[63:32]);
                    completion_this_cycle = 1;
                end
                if (mem_awleft == 0) mem_aw_pending = 0;
                else begin
                    mem_awleft = mem_awleft - 1;
                    if (mem_awburst != 0) mem_awaddr = mem_awaddr + (32'd1 << mem_awsize);
                end
            end
            if (completion_this_cycle) begin
                if (pe_writes != 389+34*tile_count || pe_responses != pe_writes ||
                    pe_reads != 128*tile_count || pe_read_requests != pe_reads ||
                    output_writes != 16*tile_count || tiles_completed != tile_count ||
                    pe_aw_pending || pe_w_pending || pe_b_pending || pe_read_pending || mem_aw_pending ||
                    ifu_reads == 0 || ifu_returns == 0)
                    $fatal(1, "CONV1_FULL_MONITOR_FAIL incomplete coverage writes=%0d responses=%0d reads=%0d output_bytes=%0d tiles=%0d",
                           pe_writes, pe_responses, pe_reads, output_writes, tiles_completed);
                for (i = tile_start; i < tile_start+tile_count; i = i + 1)
                    if (tile_stores[i] != 16)
                        $fatal(1, "CONV1_FULL_MONITOR_FAIL missing tile %0d", i);
                observed_fd = $fopen(observed_path, "wb");
                if (!observed_fd) $fatal(1, "CONV1_FULL_MONITOR_FAIL cannot open observed output binary");
                for (i = 0; i < OUTPUT_BYTES; i = i + 1) $fwrite(observed_fd, "%c", observed[i]);
                $fclose(observed_fd);
                $fclose(partial_fd);
                $fdisplay(trace_fd, "%0d\tCOMPLETE\tpe_reads=%0d\tpe_writes=%0d\tpe_responses=%0d\toutput_bytes=%0d\ttiles=%0d",
                          cycles, pe_reads, pe_writes, pe_responses, output_writes, tiles_completed);
                $fclose(trace_fd);
                completed = 1;
                $display("CONV1_FULL_MONITOR_PASS tile_start=%0d tile_count=%0d pe_reads=%0d pe_writes=%0d pe_responses=%0d output_bytes=%0d cycles=%0d",
                         tile_start, tile_count, pe_reads, pe_writes, pe_responses, output_writes, cycles);
            end
        end
    end
endmodule
