`timescale 1ns/1fs
// The same independent, port-level model checks RTL and the mapped 16x16 DUT.
module tb_pe_array_gate;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0;
    reg [3:0] sram_addr = 0;
    reg [15:0] sram_ce = 0, sram_we = 0;
    reg [127:0] weight_in = 0;
    reg [511:0] data_in = 0;
    reg [2047:0] psum_in = 0;
    wire [2047:0] psum_out;
    pe_array dut (.*);

`ifdef ARRAY_SDF_SIM
    realtime last_macro_clock = 0, first_macro_delay = 0;
    integer delayed_macro_events = 0;
    always @(posedge dut.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.clk)
        last_macro_clock = $realtime;
    always @(dut.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.rd_out) begin
        if (!$isunknown(dut.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.rd_out) &&
            last_macro_clock > 0 && $realtime - last_macro_clock > 0.5) begin
            if (delayed_macro_events == 0)
                first_macro_delay = $realtime - last_macro_clock;
            delayed_macro_events = delayed_macro_events + 1;
        end
    end
    initial begin
        $sdf_annotate("pe_array.sdf", dut, , "sdf_annotation.log", "MAXIMUM");
        $display("PE_ARRAY_SDF_ANNOTATION_REQUESTED file=pe_array.sdf");
    end
    // A separate run deliberately violates the annotated 1ps macro hold limit.
    // No force is executed by the normal arithmetic/connectivity regression.
    initial begin
        if ($test$plusargs("SDF_TIMING_PROBE")) begin
            @(negedge dut.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.clk);
            force dut.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.wd_in[0] = 1'b0;
            @(posedge dut.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.clk);
            #0.0001;
            force dut.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.wd_in[0] = 1'b1;
            #1;
            $display("PE_ARRAY_SDF_TIMING_PROBE_DONE");
            $finish;
        end
    end
`endif

    // Decode the actual signed-magnitude multiplier format independently.
    // Partial sums and returned products are 32-bit two's-complement words.
    function automatic [31:0] multiply_sm(input [7:0] a, input [7:0] b);
        integer left_value, right_value;
        begin
            left_value = a[6:0];
            right_value = b[6:0];
            if (a[7]) left_value = -left_value;
            if (b[7]) right_value = -right_value;
            multiply_sm = left_value * right_value;
        end
    endfunction

    function automatic [31:0] advance(input [31:0] value);
        advance = {value[30:0], value[31] ^ value[21] ^ value[1] ^ value[0]};
    endfunction

    function automatic [7:0] weight_code(input integer row_id, col_id, address);
        integer tag;
        begin
            tag = row_id*37 + col_id*13 + address*19;
            case ((row_id + 3*col_id + 5*address) % 9)
                0: weight_code = 8'h00;
                1: weight_code = 8'h80;
                2: weight_code = 8'h81;
                3: weight_code = 8'hff;
                4: weight_code = 8'h7f;
                default: weight_code = tag[7:0];
            endcase
        end
    endfunction

    function automatic [127:0] weight_vector(input integer row_id, address);
        integer col_id;
        begin
            for (col_id = 0; col_id < 16; col_id = col_id + 1)
                weight_vector[8*col_id +: 8] = weight_code(row_id, col_id, address);
        end
    endfunction

    function automatic [511:0] data_vector(input integer sequence_id);
        integer row_id, lane_id, tag;
        begin
            for (row_id = 0; row_id < 16; row_id = row_id + 1)
                for (lane_id = 0; lane_id < 4; lane_id = lane_id + 1) begin
                    tag = sequence_id*11 + row_id*29 + lane_id*43;
                    case ((sequence_id + row_id + lane_id) % 11)
                        0: data_vector[32*row_id+8*lane_id +: 8] = 8'h80;
                        1: data_vector[32*row_id+8*lane_id +: 8] = 8'h81;
                        2: data_vector[32*row_id+8*lane_id +: 8] = 8'hff;
                        3: data_vector[32*row_id+8*lane_id +: 8] = 8'h7f;
                        default: data_vector[32*row_id+8*lane_id +: 8] = tag[7:0];
                    endcase
                end
        end
    endfunction

    function automatic [2047:0] sums_vector(input integer sequence_id);
        integer col_id, lane_id;
        reg [31:0] base;
        begin
            for (col_id = 0; col_id < 16; col_id = col_id + 1)
                for (lane_id = 0; lane_id < 4; lane_id = lane_id + 1) begin
                    case (lane_id)
                        0: base = 32'hfffffff0;
                        1: base = 32'h7fffffff;
                        2: base = 32'h80000000;
                        3: base = 32'h00000001;
                    endcase
                    sums_vector[128*col_id+32*lane_id +: 32] =
                        base + sequence_id*17 + col_id*257;
                end
        end
    endfunction

    // All state is independent of DUT internals. The two phases first compute
    // every PE's next state from the old entire array, then commit all PEs.
    reg [7:0] model_mem [0:255][0:15];
    reg [7:0] model_q [0:255], model_weight [0:255], next_weight [0:255];
    reg [31:0] model_data [0:255], next_data [0:255];
    reg [127:0] model_product [0:255], next_product [0:255];
    reg [127:0] model_psum [0:255], next_psum [0:255], old_output [0:255];
    reg [2047:0] expected_output;
    bit [4095:0] writes_seen = '0, reads_seen = '0;
    bit [255:0] contributing_pes = '0;
    bit [15:0] read_rows_seen = 0, write_rows_seen = 0, disabled_rows_seen = 0;
    bit checking = 0;
    integer checked_cycles = 0, checked_outputs = 0, reset_checks = 0;
    integer negative_operands = 0, negative_zero_operands = 0, wrap_events = 0;
    integer trace_file;
    string trace_path;
    integer mr, mc, ml, mp;
    reg [32:0] wide_sum;

    always @(posedge clk) begin
        for (mp = 0; mp < 256; mp = mp + 1)
            for (ml = 0; ml < 4; ml = ml + 1)
                old_output[mp][32*ml +: 32] =
                    model_psum[mp][32*ml +: 32] + model_product[mp][32*ml +: 32];
        for (mr = 0; mr < 16; mr = mr + 1) begin
            for (mc = 0; mc < 16; mc = mc + 1) begin
                mp = mr*16 + mc;
                if (!rst_n) begin
                    next_data[mp] = 0;
                    next_weight[mp] = 0;
                    next_product[mp] = 0;
                    next_psum[mp] = 0;
                end else begin
                    next_data[mp] = (mc == 0) ? data_in[32*mr +: 32] : model_data[mp-1];
                    next_weight[mp] = model_q[mp];
                    next_psum[mp] = (mr == 0) ? psum_in[128*mc +: 128] : old_output[mp-16];
                    for (ml = 0; ml < 4; ml = ml + 1) begin
                        next_product[mp][32*ml +: 32] =
                            multiply_sm(model_data[mp][8*ml +: 8], model_weight[mp]);
                        if (checking) begin
                            if (next_product[mp][32*ml +: 32] != 0)
                                contributing_pes[mp] = 1;
                            if ((model_data[mp][8*ml+7] && model_data[mp][8*ml +: 7] != 0) ||
                                (model_weight[mp][7] && model_weight[mp][6:0] != 0))
                                negative_operands = negative_operands + 1;
                            if (model_data[mp][8*ml +: 8] == 8'h80 || model_weight[mp] == 8'h80)
                                negative_zero_operands = negative_zero_operands + 1;
                            wide_sum = {1'b0, next_psum[mp][32*ml +: 32]} +
                                       {1'b0, next_product[mp][32*ml +: 32]};
                            if (wide_sum[32]) wrap_events = wrap_events + 1;
                        end
                    end
                end
                // SRAM is clocked even while the PE registers are in reset.
                if (sram_ce[mr]) begin
                    if (sram_we[mr]) begin
                        model_mem[mp][sram_addr] = weight_in[8*mc +: 8];
                        writes_seen[mp*16+sram_addr] = 1;
                        write_rows_seen[mr] = 1;
                    end else begin
                        model_q[mp] = model_mem[mp][sram_addr];
                        reads_seen[mp*16+sram_addr] = 1;
                        read_rows_seen[mr] = 1;
                    end
                end else disabled_rows_seen[mr] = 1;
            end
        end
        for (mp = 0; mp < 256; mp = mp + 1) begin
            model_data[mp] = next_data[mp];
            model_weight[mp] = next_weight[mp];
            model_product[mp] = next_product[mp];
            model_psum[mp] = next_psum[mp];
        end
        for (mc = 0; mc < 16; mc = mc + 1)
            for (ml = 0; ml < 4; ml = ml + 1)
                expected_output[128*mc+32*ml +: 32] =
                    model_psum[240+mc][32*ml +: 32] + model_product[240+mc][32*ml +: 32];
        #4;
        if (checking) begin
            if ($isunknown(expected_output)) $fatal(1, "PE_ARRAY_REFERENCE_UNKNOWN time=%0t", $time);
            for (mc = 0; mc < 16; mc = mc + 1)
                for (ml = 0; ml < 4; ml = ml + 1)
                    if (psum_out[128*mc+32*ml +: 32] !== expected_output[128*mc+32*ml +: 32])
                        $fatal(1, "PE_ARRAY_FAIL time=%0t col=%0d lane=%0d got=%08h expected=%08h",
                               $time, mc, ml, psum_out[128*mc+32*ml +: 32],
                               expected_output[128*mc+32*ml +: 32]);
            checked_cycles = checked_cycles + 1;
            checked_outputs = checked_outputs + 64;
            $fdisplay(trace_file, "%0512h", psum_out);
        end
    end

    task automatic step(input [15:0] ce, we, input [3:0] address,
                        input [127:0] weights, input [511:0] data,
                        input [2047:0] partial_sums);
        begin
            @(negedge clk);
            sram_ce = ce;
            sram_we = we;
            sram_addr = address;
            weight_in = weights;
            data_in = data;
            psum_in = partial_sums;
            @(posedge clk);
            #4.5;
        end
    endtask

    integer row_number, address_number, sequence_number;
    reg [31:0] random_state = 32'h42a5c317;
    reg [511:0] pulse_vector;
    initial begin
        if (!$value$plusargs("TRACE_FILE=%s", trace_path)) $fatal(1, "TRACE_FILE is required");
        trace_file = $fopen(trace_path, "w");
        if (trace_file == 0) $fatal(1, "Cannot open array trace file");

        // One row at a time permits row-specific data despite the shared
        // per-column weight bus. All 4096 SRAM byte locations are initialized.
        for (row_number = 0; row_number < 16; row_number = row_number + 1)
            for (address_number = 0; address_number < 16; address_number = address_number + 1)
                step(16'b1 << row_number, 16'b1 << row_number, address_number[3:0],
                     weight_vector(row_number, address_number), 0, 0);
        step(16'hffff, 0, 0, 0, 0, 0);
        @(negedge clk); rst_n = 1;
        repeat (3) step(16'hffff, 0, 0, 0, 0, 0);
        checking = 1;

        for (address_number = 0; address_number < 16; address_number = address_number + 1)
            step(16'hffff, 0, address_number[3:0], 0,
                 data_vector(address_number), sums_vector(address_number));
        for (sequence_number = 0; sequence_number < 192; sequence_number = sequence_number + 1)
            step(16'hffff, 0, sequence_number[3:0], 0,
                 data_vector(sequence_number), sums_vector(sequence_number));

        // Mixed independently selected row reads, writes and disables at full rate.
        for (sequence_number = 0; sequence_number < 384; sequence_number = sequence_number + 1) begin
            random_state = advance(random_state);
            step(random_state[15:0], random_state[31:16], random_state[7:4],
                 weight_vector(sequence_number, random_state[7:4]),
                 data_vector(random_state[15:0]), sums_vector(sequence_number));
        end

        // One injected input row at a time exposes data moving right and sums
        // moving down; distinct column weights make column swaps observable.
        for (row_number = 0; row_number < 16; row_number = row_number + 1) begin
            pulse_vector = 0;
            pulse_vector[32*row_number +: 32] = 32'hff817f01 + row_number;
            step(16'hffff, 0, row_number[3:0], 0, pulse_vector, sums_vector(row_number));
            repeat (33) step(16'hffff, 0, row_number[3:0], 0, 0, sums_vector(row_number));
        end
        for (sequence_number = 0; sequence_number < 64; sequence_number = sequence_number + 1)
            step(0, sequence_number[15:0], sequence_number[3:0],
                 weight_vector(sequence_number, sequence_number),
                 data_vector(sequence_number), sums_vector(sequence_number));

        // Check asynchronous clearing before the next rising edge, then read
        // every retained address again while new nonzero data flows through.
        @(negedge clk); rst_n = 0;
        #4;
        if (psum_out !== 0) $fatal(1, "PE_ARRAY_ASYNC_RESET_FAIL sum=%h", psum_out);
        reset_checks = reset_checks + 1;
        repeat (2) step(16'hffff, 0, 5, 0, data_vector(5), sums_vector(5));
        @(negedge clk); rst_n = 1;
        for (address_number = 0; address_number < 16; address_number = address_number + 1)
            step(16'hffff, 0, address_number[3:0], 0,
                 data_vector(address_number+1000), sums_vector(address_number+1000));
        for (sequence_number = 0; sequence_number < 96; sequence_number = sequence_number + 1)
            step(16'hffff, 0, sequence_number[3:0], 0,
                 data_vector(sequence_number+1100), sums_vector(sequence_number+1100));
        repeat (40) step(0, 0, 0, 0, 0, 0);

        if (writes_seen !== {4096{1'b1}} || reads_seen !== {4096{1'b1}} ||
            contributing_pes !== {256{1'b1}} || read_rows_seen !== 16'hffff ||
            write_rows_seen !== 16'hffff || disabled_rows_seen !== 16'hffff ||
            negative_operands == 0 || negative_zero_operands == 0 || wrap_events == 0 ||
            checked_cycles < 1200 || reset_checks != 1)
            $fatal(1, "PE_ARRAY_INCOMPLETE_COVERAGE cycles=%0d active_pe=%h", checked_cycles, contributing_pes);
        $display("PE_ARRAY_COVERAGE pes=256 sram_writes=4096 sram_reads=4096 read_rows=16 write_rows=16 disabled_rows=16 negative_operands=%0d negative_zero_operands=%0d wrap_events=%0d",
                 negative_operands, negative_zero_operands, wrap_events);
`ifdef ARRAY_SDF_SIM
        if (delayed_macro_events < 10 || first_macro_delay < 1.0 || first_macro_delay > 2.0)
            $fatal(1, "PE_ARRAY_SDF_DELAY_NOT_OBSERVED events=%0d first_ns=%0.6f", delayed_macro_events, first_macro_delay);
        $display("PE_ARRAY_SDF_DELAY_OBSERVED events=%0d first_sram_ns=%0.6f", delayed_macro_events, first_macro_delay);
`endif
        $fclose(trace_file);
        $display("PE_ARRAY_TEST_PASS cycles=%0d checked_outputs=%0d reset_checks=%0d",
                 checked_cycles, checked_outputs, reset_checks);
        $finish;
    end
    initial begin
        #100000;
        $fatal(1, "PE_ARRAY_TIMEOUT");
    end
endmodule
