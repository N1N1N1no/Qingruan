`timescale 1ns/1fs
// Shared functional scoreboard. PE_TIMING_SAMPLE allows mapped propagation;
// PE_SDF_SIM additionally annotates real synthesis delays into the gate DUT.
module tb_pe_gate_functional;
`ifdef PE_TIMING_SAMPLE
    localparam real SAMPLE_DELAY = 4.0;
    localparam real STEP_DELAY = 4.5;
`else
    localparam real SAMPLE_DELAY = 1.0;
    localparam real STEP_DELAY = 2.0;
`endif
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0;
    reg sram_ce = 0, sram_we = 0;
    reg [3:0] sram_addr = 0;
    reg [7:0] sram_wdata = 0;
    reg [31:0] data_in = 0;
    reg [127:0] psum_in = 0;
    wire [31:0] data_out;
    wire [127:0] psum_out;

    pe_1sram_4mul dut (.*);

`ifdef PE_SDF_SIM
    realtime last_sram_clock = 0;
    realtime first_sram_delay = 0;
    integer delayed_sram_events = 0;
    always @(posedge clk) last_sram_clock = $realtime;
    always @(dut.u_sram.u_macro.rd_out) begin
        if (!$isunknown(dut.u_sram.u_macro.rd_out) && last_sram_clock > 0 &&
            $realtime - last_sram_clock > 0.5) begin
            if (delayed_sram_events == 0)
                first_sram_delay = $realtime - last_sram_clock;
            delayed_sram_events = delayed_sram_events + 1;
        end
    end
    initial begin
        // VCS 2016 requires a constant filename at compilation. The runner
        // copies the matching frozen SDF into this isolated case directory.
        $sdf_annotate("pe_mapped.sdf", dut, , "sdf_annotation.log", "MAXIMUM");
        $display("PE_SDF_ANNOTATION_REQUESTED file=pe_mapped.sdf");
    end
    // A separate, deliberately failing run proves the SRAM timing checks and
    // notifier are active. Change a macro data pin 0.1 ps after its actual
    // clock, violating the annotated 1 ps hold limit. Normal runs never force.
    initial begin
        if ($test$plusargs("SDF_TIMING_PROBE")) begin
            @(negedge dut.u_sram.u_macro.clk);
            force dut.u_sram.u_macro.wd_in[0] = 1'b0;
            @(posedge dut.u_sram.u_macro.clk);
            #0.0001;
            force dut.u_sram.u_macro.wd_in[0] = 1'b1;
            #1;
            $display("PE_SDF_TIMING_PROBE_DONE");
            $finish;
        end
    end
`endif

    // Independent arithmetic/state model. The multiplier inputs use sign and
    // magnitude (81 means -1), while partial sums use 32-bit two's complement.
    function automatic [31:0] multiply_sm(input [7:0] a, input [7:0] b);
        reg [31:0] magnitude;
        begin
            magnitude = a[6:0] * b[6:0];
            multiply_sm = (a[7] ^ b[7]) ? (32'b0 - magnitude) : magnitude;
        end
    endfunction

    function automatic [127:0] sums(input integer n);
        begin
            sums = {32'h80000000 + n, 32'h7fffffff - n,
                    32'hfffffff0 + n, 32'h00000000 + n};
        end
    endfunction

    function automatic [31:0] advance(input [31:0] value);
        advance = {value[30:0], value[31] ^ value[21] ^ value[1] ^ value[0]};
    endfunction

    reg [7:0] model_mem [0:15];
    reg [7:0] model_q = 8'hxx, model_weight = 0;
    reg [31:0] model_data = 0;
    reg [31:0] model_product [0:3];
    reg [127:0] expected_sum;
    bit checking = 0;
    bit [65535:0] pairs_seen = '0;
    integer checked_cycles = 0, pair_count = 0, reset_checks = 0;
    integer trace_file, lane, pair_index;
    string trace_path;

    // Blocking assignments here implement a reference model. Products must
    // use the previous registered data and weight before those are updated.
    always @(posedge clk) begin
        if (!rst_n) begin
            model_data = 0;
            model_weight = 0;
            expected_sum = 0;
            for (lane = 0; lane < 4; lane = lane + 1)
                model_product[lane] = 0;
        end else begin
            for (lane = 0; lane < 4; lane = lane + 1) begin
                if (checking) begin
                    pair_index = {model_weight, model_data[8*lane +: 8]};
                    if (!pairs_seen[pair_index]) begin
                        pairs_seen[pair_index] = 1;
                        pair_count = pair_count + 1;
                    end
                end
                model_product[lane] = multiply_sm(model_data[8*lane +: 8], model_weight);
                expected_sum[32*lane +: 32] =
                    psum_in[32*lane +: 32] + model_product[lane];
            end
            model_data = data_in;
            model_weight = model_q;
        end
        // The SRAM clock is independent of register reset. Writes and disable
        // hold its read output; all addresses are initialized by the stimulus.
        if (sram_ce) begin
            if (sram_we) model_mem[sram_addr] = sram_wdata;
            else model_q = model_mem[sram_addr];
        end
        #(SAMPLE_DELAY);
        if (checking) begin
            if ($isunknown({model_data, expected_sum}))
                $fatal(1, "REFERENCE_UNKNOWN time=%0t", $time);
            if (data_out !== model_data || psum_out !== expected_sum)
                $fatal(1, "PE_FUNCTIONAL_FAIL time=%0t data=%h expected_data=%h sum=%h expected_sum=%h",
                       $time, data_out, model_data, psum_out, expected_sum);
            checked_cycles = checked_cycles + 1;
            $fdisplay(trace_file, "%08h %032h", data_out, psum_out);
        end
    end

    task automatic step(input bit ce, input bit we, input [3:0] address,
                        input [7:0] weight, input [31:0] data,
                        input [127:0] partial_sum);
        begin
            @(negedge clk);
            sram_ce = ce;
            sram_we = we;
            sram_addr = address;
            sram_wdata = weight;
            data_in = data;
            psum_in = partial_sum;
            @(posedge clk);
            #(STEP_DELAY);
        end
    endtask

    integer i, w, group_number;
    reg [31:0] vector, random_state = 32'h13579bdf;
    initial begin
        if (!$value$plusargs("TRACE_FILE=%s", trace_path))
            $fatal(1, "TRACE_FILE is required");
        trace_file = $fopen(trace_path, "w");
        if (trace_file == 0) $fatal(1, "Cannot open trace file");

        // Prime every SRAM address while the pipeline is in reset.
        for (i = 0; i < 16; i = i + 1)
            step(1, 1, i[3:0], 8'h80 + i, 0, 0);
        step(1, 0, 0, 0, 0, 0);
        @(negedge clk); rst_n = 1;
        repeat (3) step(1, 0, 0, 0, 0, 0);
        checking = 1;

        // Exhaust all 256 input encodings x 256 weight encodings. Four lanes
        // carry different values; changing partial sums also exercises wrap.
        for (w = 0; w < 256; w = w + 1) begin
            step(1, 1, w[3:0], w[7:0], 0, sums(w));
            repeat (3) step(1, 0, w[3:0], 0, 0, sums(w));
            for (group_number = 0; group_number < 64; group_number = group_number + 1) begin
                for (i = 0; i < 4; i = i + 1)
                    vector[8*i +: 8] = group_number*4 + i;
                step(1, 0, w[3:0], 0, vector, sums(group_number));
            end
            repeat (2) step(1, 0, w[3:0], 0, vector, sums(w));
        end

        // Read/write/disable transitions and changing addresses without waits.
        for (i = 0; i < 512; i = i + 1) begin
            random_state = advance(random_state);
            step(random_state[0], random_state[1], random_state[5:2],
                 random_state[13:6], random_state, sums(i));
        end

        // Asynchronous reset must clear the PE registers while retaining SRAM.
        @(negedge clk); rst_n = 0;
        #(SAMPLE_DELAY);
        if (data_out !== 0 || psum_out !== 0)
            $fatal(1, "ASYNCHRONOUS_RESET_FAIL data=%h sum=%h", data_out, psum_out);
        reset_checks = reset_checks + 1;
        repeat (2) step(1, 0, 5, 0, 32'hff817f00, sums(5));
        @(negedge clk); rst_n = 1;
        for (i = 0; i < 16; i = i + 1)
            step(1, 0, i[3:0], 0, 32'hff817f00, sums(i));
        repeat (4) step(0, 0, 0, 0, 0, 0);

        if (pair_count != 65536 || checked_cycles < 18000 || reset_checks != 1)
            $fatal(1, "INCOMPLETE_COVERAGE cycles=%0d pairs=%0d", checked_cycles, pair_count);
`ifdef PE_SDF_SIM
        if (delayed_sram_events < 100 || first_sram_delay < 1.0 || first_sram_delay > 2.0)
            $fatal(1, "SRAM_SDF_DELAY_NOT_OBSERVED count=%0d first_ns=%0.6f",
                   delayed_sram_events, first_sram_delay);
        $display("PE_SDF_DELAY_OBSERVED events=%0d first_sram_ns=%0.6f",
                 delayed_sram_events, first_sram_delay);
`endif
        $fclose(trace_file);
        $display("PE_FUNCTIONAL_TEST_PASS cycles=%0d pairs=%0d reset_checks=%0d",
                 checked_cycles, pair_count, reset_checks);
        $finish;
    end

    initial begin
        #1000000;
        $fatal(1, "PE_FUNCTIONAL_TIMEOUT");
    end
endmodule
