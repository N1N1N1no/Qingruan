// Compatibility adapter. Legacy CEN/GWEN are active-low.
// Macro polarity requires source evidence; there are no defaults.
// CLK passes through unchanged; no read/write protocol is assumed here.
module rfsp16x8 (
    output wire [7:0] Q,
    input CLK, CEN, GWEN,
    input [3:0] A,
    input [7:0] D,
    input STOV,
    input [2:0] EMA,
    input [1:0] EMAW,
    input EMAS, RET, QNAP
);
`ifdef PE_REFERENCE_MACRO
    // Reference interface contract, identical in simulation and synthesis.
    // Simulation supplies tb/sram_reference_model.v; synthesis supplies only
    // synth/sram_blackbox.v. This does not certify the physical macro protocol.
`ifdef PE_LEGACY_RAM
    ERROR_SELECT_ONE_RAM_MODE conflicting_modes();
`endif
    SRAM_6T_CORE_16x8_MC_TB u_macro (
        .rd_out(Q), .wd_in(D), .addr_in(A),
        .ce_in(CEN), .we_in(GWEN), .clk(CLK)
    );
`else
`ifdef PE_LEGACY_RAM
`ifdef SYNTHESIS
    ERROR_LEGACY_RAM_MUST_NOT_BE_SYNTHESIZED invalid_mode();
`else
    rfsp16x8_legacy u_legacy (
        .Q(Q), .CLK(CLK), .CEN(CEN), .GWEN(GWEN), .A(A), .D(D),
        .STOV(STOV), .EMA(EMA), .EMAW(EMAW), .EMAS(EMAS), .RET(RET), .QNAP(QNAP)
    );
`endif
`else
`ifndef SRAM_CE_ACTIVE_LOW
    ERROR_CONFIRM_SRAM_CE_POLARITY missing_ce();
`else
`ifndef SRAM_WE_ACTIVE_LOW
    ERROR_CONFIRM_SRAM_WE_POLARITY missing_we();
`else
    wire macro_ce = (`SRAM_CE_ACTIVE_LOW == 1) ? CEN : ~CEN;
    wire macro_we = (`SRAM_WE_ACTIVE_LOW == 1) ? GWEN : ~GWEN;
    generate
        if ((`SRAM_CE_ACTIVE_LOW != 0 && `SRAM_CE_ACTIVE_LOW != 1) ||
            (`SRAM_WE_ACTIVE_LOW != 0 && `SRAM_WE_ACTIVE_LOW != 1)) begin : BAD_POLARITY
            ERROR_SRAM_POLARITY_MUST_BE_ZERO_OR_ONE invalid_value();
        end
    endgenerate
    // Logical view: Liberty pg_pin vdd/gnd are not Verilog signal ports here.
    // A model with explicit power pins requires a reviewed view/adapter.
    SRAM_6T_CORE_16x8_MC_TB u_macro (
        .rd_out(Q), .wd_in(D), .addr_in(A),
        .ce_in(macro_ce), .we_in(macro_we), .clk(CLK)
    );
`endif
`endif
`endif
`endif // PE_REFERENCE_MACRO
endmodule
