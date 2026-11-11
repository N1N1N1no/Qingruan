# Apply and save the single-PE tutorial interface assumptions, without compile.
proc constrain_single_pe {project_dir} {
    if {[get_object_name [current_design]] ne "pe_1sram_4mul"} {
        error "Load the single PE first using synth/read_pe.tcl"
    }
    set output_dir [file join $project_dir build dc_pe_io]
    file mkdir $output_dir
    redirect -variable unit_report {report_units}
    if {![regexp {Time_unit\s+:\s+1\.0e-09} $unit_report] ||
        ![regexp {Capacitive_load_unit\s+:\s+1\.0e-15} $unit_report]} {
        error "This tutorial expects ns/fF units; inspect report_units first"
    }
    source [file join $project_dir synth pe_io.sdc]
    if {[sizeof_collection [get_clocks -quiet pe_clk]] != 1 ||
        [get_attribute [get_clocks pe_clk] period] != 10.0} {
        error "Tutorial clock was not applied"
    }
    redirect -file [file join $output_dir units.rpt] {report_units}
    redirect -file [file join $output_dir ports.rpt] {report_port -verbose -nosplit}
    redirect -file [file join $output_dir constraints_check.rpt] {
        check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}
    }
    if {![write_sdc [file join $output_dir pe_constraints.sdc]]} {
        error "Cannot save the applied constraints"
    }
    if {![write -format ddc -hierarchy -output [file join $output_dir pe_constrained.ddc]]} {
        error "Cannot save the constrained PE"
    }
    puts "DC_PE_CONSTRAINTS_APPLIED period_ns=10 input_max_ns=1 output_max_ns=1"
}
constrain_single_pe [file dirname [file dirname [file normalize [info script]]]]
