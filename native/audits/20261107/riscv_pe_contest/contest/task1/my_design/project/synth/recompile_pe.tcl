# Recompile the current single PE using the tutorial interface constraints.
# This remains a single-corner, pre-layout exercise, not SoC signoff.
proc recompile_pe_with_io {project_dir} {
    if {[get_object_name [current_design]] ne "pe_1sram_4mul"} {
        error "Load the single PE before running this script"
    }
    set output_dir [file join $project_dir build dc_pe_io]
    file mkdir $output_dir
    source [file join $project_dir synth constrain_pe.tcl]
    # Request real short-path repair; keep the input min delay and uncertainty.
    set_fix_hold [get_clocks pe_clk]
    if {![compile]} {
        error "PE synthesis with interface constraints failed"
    }
    if {![get_attribute [current_design] is_mapped]} {
        error "The PE has not been mapped"
    }
    if {[sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {
        error "Unmapped logic remains"
    }
    set sram_cells [get_cells -hierarchical \
        -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    if {[sizeof_collection $sram_cells] != 1 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected one SRAM instance bound to its library"
    }
    redirect -file [file join $output_dir area.rpt] {report_area}
    redirect -file [file join $output_dir setup.rpt] {
        report_timing -delay_type max -max_paths 3 -significant_digits 4
    }
    redirect -file [file join $output_dir hold.rpt] {
        report_timing -delay_type min -max_paths 3 -significant_digits 4
    }
    redirect -file [file join $output_dir violations.rpt] {
        report_constraint -all_violators
    }
    redirect -file [file join $output_dir constraints_check.rpt] {
        check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}
    }
    if {![change_names -rules verilog -hierarchy]} {
        error "Cannot prepare Verilog names for export"
    }
    if {![write -format verilog -hierarchy -output [file join $output_dir pe_mapped.v]] ||
        ![write -format ddc -hierarchy -output [file join $output_dir pe_mapped.ddc]] ||
        ![write_sdc [file join $output_dir pe_constraints.sdc]]} {
        error "Cannot save the recompiled PE"
    }
    set setup_paths [get_timing_paths -delay_type max -max_paths 1]
    set hold_paths [get_timing_paths -delay_type min -max_paths 1]
    if {[sizeof_collection $setup_paths] == 0 || [sizeof_collection $hold_paths] == 0} {
        error "Missing timing paths; inspect the constraints"
    }
    set setup_slack 1e30
    foreach_in_collection path $setup_paths {
        set setup_slack [expr {min($setup_slack, [get_attribute $path slack])}]
    }
    set hold_slack 1e30
    foreach_in_collection path $hold_paths {
        set hold_slack [expr {min($hold_slack, [get_attribute $path slack])}]
    }
    puts "PE_IO_SETUP_SLACK_NS=$setup_slack"
    puts "PE_IO_HOLD_SLACK_NS=$hold_slack"
    puts "DC_PE_IO_SYNTHESIS_DONE mapped=true sram_instances=1"
    puts "Review setup.rpt, hold.rpt and violations.rpt in $output_dir"
}
recompile_pe_with_io [file dirname [file dirname [file normalize [info script]]]]
