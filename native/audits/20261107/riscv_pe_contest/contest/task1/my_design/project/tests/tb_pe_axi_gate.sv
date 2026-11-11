`timescale 1ns/1ps
// One port-only regression is compiled against the RTL and the saved mapped top.
// The wrapper continuously computes; it has no start/done status register.
module tb_pe_axi_gate;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    reg [31:0] s_axi_awaddr = 0, s_axi_araddr = 0;
    reg [2:0] s_axi_awid = 0, s_axi_arid = 0;
    reg [7:0] s_axi_awlen = 0, s_axi_arlen = 0;
    reg [2:0] s_axi_awsize = 3, s_axi_arsize = 3;
    reg [1:0] s_axi_awburst = 1, s_axi_arburst = 1;
    reg s_axi_awvalid = 0, s_axi_wvalid = 0, s_axi_wlast = 0;
    reg s_axi_bready = 0, s_axi_arvalid = 0, s_axi_rready = 0;
    reg [63:0] s_axi_wdata = 0;
    reg [7:0] s_axi_wstrb = 0;
    wire s_axi_awready, s_axi_wready, s_axi_bvalid, s_axi_arready;
    wire s_axi_rlast, s_axi_rvalid;
    wire [1:0] s_axi_bresp, s_axi_rresp;
    wire [2:0] s_axi_bid, s_axi_rid;
    wire [63:0] s_axi_rdata;
`ifdef PE_AXI_GATE_SIM
    sram_multiplier_system_ID_WIDTH3 dut (.*);
`else
    sram_multiplier_system #(.ID_WIDTH(3)) dut (.*);
`endif

    integer writes = 0, reads = 0, results = 0, resets = 0;
    integer aw_first = 0, w_first = 0, error_responses = 0;
    integer byte_checks = 0, retained_results = 0;
    integer trace_file;
    string trace_path;

    task automatic write_bus(input [31:0] address, input [63:0] value,
                             input [7:0] strobes, input [2:0] id,
                             input bit data_first, input [1:0] response,
                             input [7:0] length, input bit last);
        integer count;
        begin
            fork
                begin
                    repeat (data_first ? 3 : 0) @(negedge clk);
                    @(negedge clk);
                    s_axi_awaddr = address;
                    s_axi_awid = id;
                    s_axi_awlen = length;
                    s_axi_awvalid = 1;
                    count = 0;
                    do begin
                        @(posedge clk);
                        count = count + 1;
                        if (count > 40) $fatal(1, "PE_AXI_AW_TIMEOUT address=%h", address);
                    end while (s_axi_awready !== 1'b1);
                    @(negedge clk); s_axi_awvalid = 0;
                end
                begin : send_data
                    integer cycles;
                    repeat (data_first ? 0 : 3) @(negedge clk);
                    @(negedge clk);
                    s_axi_wdata = value;
                    s_axi_wstrb = strobes;
                    s_axi_wlast = last;
                    s_axi_wvalid = 1;
                    cycles = 0;
                    do begin
                        @(posedge clk);
                        cycles = cycles + 1;
                        if (cycles > 40) $fatal(1, "PE_AXI_W_TIMEOUT address=%h", address);
                    end while (s_axi_wready !== 1'b1);
                    @(negedge clk); s_axi_wvalid = 0;
                end
            join
            count = 0;
            while (s_axi_bvalid !== 1'b1) begin
                @(negedge clk);
                count = count + 1;
                if (count > 40) $fatal(1, "PE_AXI_B_TIMEOUT address=%h", address);
            end
            // Hold READY low. Response/ID must remain stable and neither
            // write channel may accept a new request while B is blocked.
            repeat (2) begin
                @(negedge clk);
                if (s_axi_bvalid !== 1'b1 || s_axi_bid !== id ||
                    s_axi_bresp !== response || s_axi_awready !== 1'b0 ||
                    s_axi_wready !== 1'b0)
                    $fatal(1, "PE_AXI_B_FAIL address=%h id=%h/%h resp=%h/%h",
                           address, s_axi_bid, id, s_axi_bresp, response);
            end
            $fdisplay(trace_file, "W %08h %016h %02h %01h %01h %02h %01h",
                      address, value, strobes, id, response, length, last);
            s_axi_bready = 1;
            @(negedge clk); s_axi_bready = 0;
            writes = writes + 1;
            if (data_first) w_first = w_first + 1;
            else aw_first = aw_first + 1;
            if (response != 0) error_responses = error_responses + 1;
        end
    endtask

    task automatic wr64(input [31:0] address, input [63:0] value);
        begin
            write_bus(address, value, 8'hff, writes[2:0], writes[0], 0, 0, 1);
        end
    endtask

    task automatic wr32(input [31:0] address, input [31:0] value);
        begin
            write_bus(address, address[2] ? {value, 32'b0} : {32'b0, value},
                      address[2] ? 8'hf0 : 8'h0f,
                      writes[2:0], writes[0], 0, 0, 1);
        end
    endtask

    task automatic read_expect(input [31:0] address, input [63:0] expected,
                               input [1:0] response, input [7:0] length);
        reg [63:0] saved;
        reg [2:0] id;
        integer count;
        begin
            id = reads[2:0] ^ 3'b101;
            @(negedge clk);
            s_axi_araddr = address;
            s_axi_arid = id;
            s_axi_arlen = length;
            s_axi_arvalid = 1;
            count = 0;
            do begin
                @(posedge clk);
                count = count + 1;
                if (count > 40) $fatal(1, "PE_AXI_AR_TIMEOUT address=%h", address);
            end while (s_axi_arready !== 1'b1);
            @(negedge clk); s_axi_arvalid = 0;
            count = 0;
            while (s_axi_rvalid !== 1'b1) begin
                @(negedge clk);
                count = count + 1;
                if (count > 40) $fatal(1, "PE_AXI_R_TIMEOUT address=%h", address);
            end
            saved = s_axi_rdata;
            repeat (2) begin
                @(negedge clk);
                if (s_axi_rvalid !== 1'b1 || s_axi_rlast !== 1'b1 ||
                    s_axi_rid !== id || s_axi_rresp !== response ||
                    s_axi_rdata !== saved || s_axi_arready !== 1'b0)
                    $fatal(1, "PE_AXI_R_UNSTABLE address=%h", address);
            end
            if (saved !== expected)
                $fatal(1, "PE_AXI_READ_FAIL address=%h got=%016h expected=%016h",
                       address, saved, expected);
            $fdisplay(trace_file, "R %08h %016h %01h %01h %02h",
                      address, saved, id, response, length);
            s_axi_rready = 1;
            @(negedge clk); s_axi_rready = 0;
            reads = reads + 1;
            if (response != 0) error_responses = error_responses + 1;
        end
    endtask

    function automatic integer signed_value(input [7:0] code);
        integer magnitude;
        begin
            magnitude = code[6:0];
            signed_value = code[7] ? -magnitude : magnitude;
        end
    endfunction

    function automatic [7:0] weight_code(input integer row_id, col_id, bank);
        integer magnitude;
        begin
            magnitude = 1 + (row_id*5 + col_id*3 + bank*7) % 23;
            weight_code = ((row_id + col_id + bank) % 3 == 0) ?
                          (8'h80 | magnitude) : magnitude;
            if (bank == 0 && row_id == 3 && col_id == 9) weight_code = 8'h80;
            if (bank == 1 && row_id == 11 && col_id == 4) weight_code = 8'hff;
        end
    endfunction

    function automatic [7:0] data_code(input integer row_id, lane, bank);
        integer magnitude;
        begin
            magnitude = 1 + (row_id*7 + lane*13 + bank*5) % 37;
            data_code = ((row_id + 2*lane + bank) % 4 == 0) ?
                        (8'h80 | magnitude) : magnitude;
            if (row_id == 7 && lane == 2) data_code = 8'h80;
            if (row_id == 5 && lane == 1) data_code = 8'hff;
        end
    endfunction

    function automatic [31:0] incoming_sum(input integer col_id, lane, bank);
        reg [31:0] base;
        begin
            case (lane)
                0: base = 32'hfffffff0;
                1: base = 32'h7fffffff;
                2: base = 32'h80000000;
                3: base = 32'h00000100;
            endcase
            incoming_sum = base + col_id*257 + bank*19;
        end
    endfunction

    // Static input windows eliminate software/pipeline scheduling ambiguity.
    // This independently sums signed-magnitude products modulo 2^32.
    function automatic [31:0] expected_lane(input integer col_id, lane, bank);
        integer row_id;
        reg [31:0] total;
        begin
            total = incoming_sum(col_id, lane, bank);
            for (row_id = 0; row_id < 16; row_id = row_id + 1)
                total = total + signed_value(data_code(row_id, lane, bank)) *
                                signed_value(weight_code(row_id, col_id, bank));
            expected_lane = total;
        end
    endfunction

    task automatic load_weights(input integer bank);
        integer row_id, group_id, byte_id;
        reg [63:0] packed_weights;
        begin
            wr64('h000, bank == 0 ? 0 : 5);
            wr64('h008, 16'hffff);
            for (row_id = 0; row_id < 16; row_id = row_id + 1) begin
                for (group_id = 0; group_id < 2; group_id = group_id + 1) begin
                    for (byte_id = 0; byte_id < 8; byte_id = byte_id + 1)
                        packed_weights[8*byte_id +: 8] =
                            weight_code(row_id, group_id*8 + byte_id, bank);
                    wr64('h020 + group_id*8, packed_weights);
                    read_expect('h020 + group_id*8, packed_weights, 0, 0);
                end
                wr64('h010, 16'b1 << row_id);
                read_expect('h010, 0, 0, 0); // WE is a pulse, not stored state.
            end
        end
    endtask

    task automatic load_inputs(input integer bank);
        integer row_id, col_id, lane;
        reg [31:0] packed_data;
        reg [63:0] packed_pair;
        begin
            for (row_id = 0; row_id < 16; row_id = row_id + 1) begin
                for (lane = 0; lane < 4; lane = lane + 1)
                    packed_data[8*lane +: 8] = data_code(row_id, lane, bank);
                wr32('h100 + row_id*4, packed_data);
            end
            for (row_id = 0; row_id < 16; row_id = row_id + 2) begin
                for (lane = 0; lane < 4; lane = lane + 1) begin
                    packed_pair[8*lane +: 8] = data_code(row_id, lane, bank);
                    packed_pair[32+8*lane +: 8] = data_code(row_id+1, lane, bank);
                end
                read_expect('h100 + row_id*4, packed_pair, 0, 0);
            end
            for (col_id = 0; col_id < 16; col_id = col_id + 1)
                for (lane = 0; lane < 4; lane = lane + 2) begin
                    packed_pair = {incoming_sum(col_id, lane+1, bank),
                                   incoming_sum(col_id, lane, bank)};
                    wr64('h200 + col_id*16 + lane*4, packed_pair);
                    read_expect('h200 + col_id*16 + lane*4, packed_pair, 0, 0);
                end
        end
    endtask

    task automatic check_results(input integer bank, input bit retained);
        integer col_id, lane;
        reg [63:0] expected;
        begin
            // Max horizontal+vertical propagation is shorter than 64 cycles.
            repeat (64) @(negedge clk);
            for (col_id = 0; col_id < 16; col_id = col_id + 1)
                for (lane = 0; lane < 4; lane = lane + 2) begin
                    expected = {expected_lane(col_id, lane+1, bank),
                                expected_lane(col_id, lane, bank)};
                    read_expect('h400 + col_id*16 + lane*4, expected, 0, 0);
                    results = results + 2;
                    if (retained) retained_results = retained_results + 2;
                end
        end
    endtask

    integer bank_id, index;
    initial begin
        if (!$value$plusargs("TRACE_FILE=%s", trace_path))
            $fatal(1, "PE_AXI_TRACE_FILE_REQUIRED");
        trace_file = $fopen(trace_path, "w");
        if (trace_file == 0) $fatal(1, "PE_AXI_TRACE_OPEN_FAILED");
        repeat (4) @(negedge clk); rst_n = 1;
        read_expect('h000, 0, 0, 0);
        read_expect('h008, 0, 0, 0);
        read_expect('h010, 0, 0, 0);

        // Partial-byte writes and CPU-style 32-bit lanes on the 64-bit bus.
        wr64('h020, 64'h1122334455667788);
        write_bus('h020, 64'hffeeddccbbaa0099, 8'h81, 7, 1, 0, 0, 1);
        read_expect('h020, 64'hff22334455667799, 0, 0);
        wr32('h024, 32'h12345678);
        wr32('h020, 32'h9abcdef0);
        read_expect('h020, 64'h123456789abcdef0, 0, 0);
        // This direct wrapper decodes only [11:0]; vector reads return the
        // complete selected 64-bit word even when address bit 2 is set.
        read_expect(32'h90000024, 64'h123456789abcdef0, 0, 0);
        write_bus('h020, 0, 0, 2, 0, 0, 0, 1);
        read_expect('h020, 64'h123456789abcdef0, 0, 0);
        byte_checks = byte_checks + 3;
        wr64('h008, 16'h1234);
        write_bus('h008, 16'habcd, 8'h01, 3, 1, 0, 0, 1);
        read_expect('h008, 16'h12cd, 0, 0);
        write_bus('h008, 16'hab00, 8'h02, 4, 0, 0, 0, 1);
        read_expect('h008, 16'habcd, 0, 0);
        byte_checks = byte_checks + 2;
        wr64('h000, 'hba);
        read_expect('h000, 'ha, 0, 0);

        // Supported behavior is single-beat MMIO, with SLVERR on bad LEN/LAST
        // or invalid/read-only offsets. Unsupported requests must not mutate CE.
        write_bus('h038, 0, 8'hff, 1, 0, 2, 0, 1);
        write_bus('h400, 0, 8'hff, 6, 1, 2, 0, 1);
        write_bus('h008, 0, 8'hff, 2, 0, 2, 1, 1);
        write_bus('h008, 0, 8'hff, 5, 1, 2, 0, 0);
        read_expect('h008, 16'habcd, 0, 0);
        read_expect('h038, 0, 2, 0);
        read_expect('h008, 0, 2, 1);

        for (bank_id = 0; bank_id < 2; bank_id = bank_id + 1) begin
            load_weights(bank_id);
            // A stuck WE pulse would overwrite the last selected row when
            // this buffer changes. The expected SRAM contents stay unchanged.
            wr64('h020, 64'h7f7f7f7f7f7f7f7f);
            wr64('h028, 64'h8181818181818181);
            load_inputs(bank_id);
            check_results(bank_id, 0);
            $display("PE_AXI_PHASE bank=%0d complete_results=%0d", bank_id, results);
        end

        // Interrupt active computation with an AW buffered without W and a
        // blocked read response. Check async reset, not only startup reset.
        @(negedge clk);
        s_axi_awaddr = 'h100; s_axi_awid = 7;
        s_axi_awlen = 0; s_axi_awvalid = 1;
        s_axi_araddr = 'h008; s_axi_arid = 6;
        s_axi_arlen = 0; s_axi_arvalid = 1;
        @(posedge clk);
        if (s_axi_awready !== 1 || s_axi_arready !== 1)
            $fatal(1, "PE_AXI_RESET_SETUP_NOT_READY");
        @(negedge clk);
        s_axi_awvalid = 0; s_axi_arvalid = 0;
        if (s_axi_rvalid !== 1 || s_axi_rid !== 6 || s_axi_rdata !== 16'hffff)
            $fatal(1, "PE_AXI_RESET_SETUP_RESPONSE");
        #2; rst_n = 0;
        #1;
        if (s_axi_bvalid !== 0 || s_axi_rvalid !== 0 ||
            s_axi_rlast !== 0 || s_axi_rdata !== 0)
            $fatal(1, "PE_AXI_ASYNC_RESET_FAIL");
        $fdisplay(trace_file, "RESET buffered_aw=1 blocked_r=1");
        resets = resets + 1;
        repeat (3) @(negedge clk); rst_n = 1;
        read_expect('h000, 0, 0, 0);
        read_expect('h008, 0, 0, 0);
        read_expect('h010, 0, 0, 0);
        for (index = 0; index < 2; index = index + 1)
            read_expect('h020 + index*8, 0, 0, 0);
        for (index = 0; index < 8; index = index + 1)
            read_expect('h100 + index*8, 0, 0, 0);
        for (index = 0; index < 32; index = index + 1)
            read_expect('h200 + index*8, 0, 0, 0);
        repeat (64) @(negedge clk);
        for (index = 0; index < 32; index = index + 1)
            read_expect('h400 + index*8, 0, 0, 0);
        // Restore only control/data, leaving SRAM untouched. W arrives before
        // AW; any forgotten pre-reset AW would create a wrong-ID response.
        write_bus('h000, 5, 8'hff, 1, 1, 0, 0, 1);
        wr64('h008, 16'hffff);
        load_inputs(1);
        check_results(1, 1);

        if (writes != 263 || reads != 402 || results != 192 || resets != 1 ||
            byte_checks != 5 || retained_results != 64 || error_responses != 6 ||
            aw_first < 100 || w_first < 100)
            $fatal(1, "PE_AXI_INCOMPLETE_COVERAGE writes=%0d reads=%0d results=%0d", writes, reads, results);
        $display("PE_AXI_COVERAGE pes=256 sram_addresses=2 byte_checks=%0d errors=%0d aw_first=%0d w_first=%0d retained_results=%0d",
                 byte_checks, error_responses, aw_first, w_first, retained_results);
        $fclose(trace_file);
        $display("PE_AXI_TEST_PASS writes=%0d reads=%0d results=%0d resets=%0d",
                 writes, reads, results, resets);
        $finish;
    end
    initial begin
        #500000;
        $fatal(1, "PE_AXI_TEST_TIMEOUT");
    end
endmodule
