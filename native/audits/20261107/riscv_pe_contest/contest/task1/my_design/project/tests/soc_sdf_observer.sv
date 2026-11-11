`timescale 1ns/1fs

// Third simulation top for the mapped, SDF-annotated whole SoC only.
// tb_soc remains the sole source of the normal clock, reset and AXI traffic;
// soc_program_monitor checks the CPU program independently. This observer
// adds no normal-run stimulus and does not modify either existing top.
module soc_sdf_observer;
    realtime last_macro_clock = 0;
    realtime first_macro_delay = 0;
    realtime macro_delay = 0;
    integer delayed_macro_events = 0;
    bit timing_probe = 0;
    realtime last_interconnect_source_change = 0;
    realtime interconnect_delay = 0;
    realtime minimum_interconnect_delay = 1.0e30;
    realtime maximum_interconnect_delay = 0;
    reg interconnect_source_value = 1'bx;
    integer delayed_interconnect_events = 0;
    integer interconnect_rise_events = 0;
    integer interconnect_fall_events = 0;

    initial begin
        timing_probe = $test$plusargs("SOC_SDF_TIMING_PROBE");
        // Do not also supply +SDF to tb_soc: this is the single annotation
        // request. VCS may perform the fixed-file annotation while compiling.
        $sdf_annotate("soc.sdf", tb_soc.dut, , "sdf_annotation.log", "MAXIMUM");
        $display("SOC_SDF_ANNOTATION_REQUESTED file=soc.sdf selection=MAXIMUM");
    end

    // Representative of the precisely reviewed SDFCOM_SWC paths. The
    // driver's address bit enters u_pe, passes a pure assign alias, and
    // reaches the actual MUX input pin. Observing both pins establishes
    // that the warned INTERCONNECT delay remains active. No stimulus is
    // added; the unchanged CPU program naturally toggles address bit 3.
    always @(tb_soc.dut.U113150.Z) begin
        last_interconnect_source_change = $realtime;
        interconnect_source_value = tb_soc.dut.U113150.Z;
    end

    always @(tb_soc.dut.u_pe.U27776.S) begin
        if (!timing_probe && last_interconnect_source_change > 0 &&
            !$isunknown({interconnect_source_value, tb_soc.dut.u_pe.U27776.S})) begin
            interconnect_delay = $realtime - last_interconnect_source_change;
            if (tb_soc.dut.u_pe.U27776.S !== interconnect_source_value ||
                interconnect_delay <= 0 || interconnect_delay >= 0.2)
                $fatal(1, "SOC_SDF_OBSERVER_FAIL warned INTERCONNECT source=%b destination=%b delay_ns=%0.9f",
                       interconnect_source_value, tb_soc.dut.u_pe.U27776.S, interconnect_delay);
            if (interconnect_delay < minimum_interconnect_delay)
                minimum_interconnect_delay = interconnect_delay;
            if (interconnect_delay > maximum_interconnect_delay)
                maximum_interconnect_delay = interconnect_delay;
            delayed_interconnect_events = delayed_interconnect_events + 1;
            if (tb_soc.dut.u_pe.U27776.S) interconnect_rise_events = interconnect_rise_events + 1;
            else interconnect_fall_events = interconnect_fall_events + 1;
        end
    end

    // The project SRAM's unannotated specify values are all zero. A known
    // output change more than half a nanosecond after its actual pin clock
    // is evidence of an active, nonzero clock-to-output path delay. Observe
    // the macro pins rather than the outer clock or a downstream register.
    always @(posedge tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.clk)
        last_macro_clock = $realtime;

    always @(tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.rd_out) begin
        macro_delay = $realtime - last_macro_clock;
        if (!timing_probe && last_macro_clock > 0 &&
            !$isunknown(tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.rd_out) &&
            macro_delay > 0.5 && macro_delay < 5.0) begin
            if (delayed_macro_events == 0) first_macro_delay = macro_delay;
            delayed_macro_events = delayed_macro_events + 1;
        end
    end

    // completed changes by a blocking assignment in the program monitor's
    // active-region completion check. Run immediately on that event: the
    // unchanged external RAM calls $finish after #0 in the same time slot.
    always @(posedge soc_program_monitor.completed) begin
        if (!timing_probe) begin
            if (delayed_macro_events == 0)
                $fatal(1, "SOC_SDF_OBSERVER_FAIL no nonzero SRAM clock-to-output delay observed");
            $display("SOC_SDF_DELAY_OBSERVED events=%0d first_sram_ns=%0.9f",
                     delayed_macro_events, first_macro_delay);
            if (interconnect_rise_events == 0 || interconnect_fall_events == 0)
                $fatal(1, "SOC_SDF_OBSERVER_FAIL warned INTERCONNECT needs both rise and fall observations");
            $display("SOC_SDF_INTERCONNECT_DELAY_OBSERVED events=%0d min_ns=%0.9f max_ns=%0.9f",
                     delayed_interconnect_events, minimum_interconnect_delay, maximum_interconnect_delay);
        end
    end

    // The same compiled simulator is executed separately with this plusarg.
    // This deliberately violates one annotated SRAM hold check while the
    // CPU is still in reset. Normal execution never reaches either force.
    initial begin
        if ($test$plusargs("SOC_SDF_TIMING_PROBE")) begin
            @(negedge tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.clk);
            if (tb_soc.rst_n !== 1'b0)
                $fatal(1, "SOC_SDF_OBSERVER_FAIL timing probe must run during reset");
            force tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.wd_in[0] = 1'b0;
            @(posedge tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.clk);
            #0.0001;
            force tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro.wd_in[0] = 1'b1;
            #1;
            if (tb_soc.rst_n !== 1'b0)
                $fatal(1, "SOC_SDF_OBSERVER_FAIL timing probe outlasted reset");
            $display("SOC_SDF_TIMING_PROBE_DONE");
            $finish;
        end
    end
endmodule
