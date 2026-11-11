`timescale 1ns/1ps
// REFERENCE FUNCTIONAL CONTRACT, not a provider-qualified physical SRAM model.
// 16x8, CE low; WE low writes, WE high reads, both on rising clock edge.
// Q holds during write/disable; no reset or initialization. Read after write.
// No timing specify blocks, no SDF acceptance or physical feature emulation.
`ifdef SYNTHESIS
// Deliberately undefined if this simulation-only file leaks into synthesis.
module ERROR_BEHAVIORAL_SRAM_INCLUDED_IN_SYNTHESIS;
    ERROR_REMOVE_SRAM_REFERENCE_MODEL_FROM_SYNTHESIS invalid_file();
endmodule
`else
module SRAM_6T_CORE_16x8_MC_TB (
    input [7:0] wd_in,
    output reg [7:0] rd_out,
    input [3:0] addr_in,
    input ce_in, we_in, clk
);
    reg [7:0] mem [0:15];
    initial $display("REFERENCE_SRAM_BEHAVIORAL_MODEL instance=%m");
    always @(posedge clk) begin
        if (!ce_in) begin
            if (!we_in) mem[addr_in] <= wd_in;
            else rd_out <= mem[addr_in];
        end
    end
endmodule
`endif
