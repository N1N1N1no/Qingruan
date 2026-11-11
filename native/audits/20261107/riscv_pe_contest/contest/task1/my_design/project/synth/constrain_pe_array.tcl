# Apply array boundary assumptions and report initial timing; do not compile.
proc constrain_pe_array {project_dir} {
    if {[get_object_name [current_design]] ne "pe_array"} {
        error "Read the array first using synth/read_pe_array.tcl"
    }
    set output_dir [file join $project_dir build dc_pe_array_io]
    file mkdir $output_dir
    redirect -variable unit_report {report_units}
    if {![regexp {Time_unit\s+:\s+1\.0e-09} $unit_report] ||
        ![regexp {Capacitive_load_unit\s+:\s+1\.0e-15} $unit_report]} {
        error "Array tutorial expects ns/fF units; inspect report_units"
    }
    set data_inputs [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
    if {[sizeof_collection $data_inputs] != 2724 ||
        [sizeof_collection [all_inputs]] != 2726 ||
        [sizeof_collection [all_outputs]] != 2048} {
        error "Expected the default 16x16 array interface"
    }
    source [file join $project_dir synth pe_array_io.sdc]
    if {[sizeof_collection [get_clocks -quiet *]] != 1 ||
        [sizeof_collection [get_clocks -quiet array_clk]] != 1 ||
        [get_attribute [get_clocks array_clk] period] != 10.0} {
        error "Expected one 10 ns array clock; remove stale constraints first"
    }
    puts "PE_ARRAY_REPORTS_RUNNING top=pe_array period_ns=10"
    redirect -file [file join $output_dir units.rpt] {report_units}
    redirect -file [file join $output_dir ports.rpt] {report_port -verbose -nosplit}
    redirect -file [file join $output_dir constraints_check.rpt] {
        check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}
    }
    redirect -file [file join $output_dir setup_initial.rpt] {
        report_timing -delay_type max -max_paths 3 -significant_digits 4
    }
    redirect -file [file join $output_dir hold_initial.rpt] {
        report_timing -delay_type min -max_paths 3 -significant_digits 4
    }
    redirect -file [file join $output_dir violations_initial.rpt] {
        report_constraint -all_violators
    }
    if {![write_sdc [file join $output_dir pe_array_constraints.sdc]] ||
        ![write -format ddc -hierarchy -output [file join $output_dir pe_array_constrained.ddc]]} {
        error "Cannot save array constraints and design"
    }
    set setup_slack 1e30
    set hold_slack 1e30
    set setup_paths [get_timing_paths -delay_type max -max_paths 1]
    set hold_paths [get_timing_paths -delay_type min -max_paths 1]
    if {[sizeof_collection $setup_paths] == 0 || [sizeof_collection $hold_paths] == 0} {
        error "Missing array timing paths; inspect the constraints"
    }
    foreach_in_collection path $setup_paths {
        set setup_slack [expr {min($setup_slack, [get_attribute $path slack])}]
    }
    foreach_in_collection path $hold_paths {
        set hold_slack [expr {min($hold_slack, [get_attribute $path slack])}]
    }
    set stream [open [file join $output_dir apply_summary.rpt] w]
    puts $stream "scope=16x16 array initial constraints and STA; no compile or simulation"
    puts $stream "period_ns=10 uncertainty_ns=0.1 clock_transition_ns=0.005"
    puts $stream "data_input_bits=2724 input_max_ns=1 input_min_ns=0 input_transition_ns=0.02"
    puts $stream "reset_release=falling_edge reset_delay_ns=0 output_bits=2048"
    puts $stream "output_max_ns=1 output_min_ns=0 output_pin_load_ff=5"
    puts $stream "high_fanout_net_threshold=[get_app_var high_fanout_net_threshold]"
    puts $stream "high_fanout_net_pin_capacitance=[get_app_var high_fanout_net_pin_capacitance]"
    puts $stream "initial_setup_slack_ns=$setup_slack initial_hold_slack_ns=$hold_slack"
    close $stream
    puts "PE_ARRAY_INITIAL_SETUP_SLACK_NS=$setup_slack"
    puts "PE_ARRAY_INITIAL_HOLD_SLACK_NS=$hold_slack"
    puts "DC_PE_ARRAY_CONSTRAINTS_APPLIED period_ns=10 input_max_ns=1 output_max_ns=1"
}
constrain_pe_array [file dirname [file dirname [file normalize [info script]]]]
