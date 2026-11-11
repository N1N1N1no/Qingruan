# Export the constrained array built from mapped PEs; no new compile or GLS.
proc export_mapped_pe_array {project_dir} {
    if {[get_object_name [current_design]] ne "pe_array"} {
        error "Load and constrain pe_array before exporting it"
    }
    set output_dir [file join $project_dir build dc_pe_array_export]
    file mkdir $output_dir
    if {[sizeof_collection [get_clocks -quiet *]] != 1 ||
        [sizeof_collection [get_clocks -quiet array_clk]] != 1 ||
        [get_attribute [get_clocks array_clk] period] != 10.0} {
        error "Apply synth/constrain_pe_array.tcl before exporting the array"
    }
    if {![link] || ![get_attribute [current_design] is_mapped]} {
        error "Expected a linked array whose leaf cells are all mapped"
    }
    foreach {pattern expected} {sram_ce* 16 sram_we* 16 weight_in* 128 data_in* 512 psum_in* 2048 psum_out* 2048} {
        if {[sizeof_collection [get_ports $pattern]] != $expected} {
            error "Expected the default 16x16 array interface: $pattern"
        }
    }
    set pe_count [sizeof_collection [get_cells -hierarchical -filter "ref_name == pe_1sram_4mul"]]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    if {$pe_count != 256 || [sizeof_collection $sram_cells] != 256 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected 256 PEs and 256 SRAM instances bound to their library"
    }
    if {[sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {
        error "Unmapped leaf cells remain in the array"
    }
    set leaf_cells [get_cells -hierarchical -filter "is_hierarchical == false"]
    set leaf_count [sizeof_collection $leaf_cells]
    set references [lsort -unique [get_attribute $leaf_cells ref_name]]
    set constant_references {}
    foreach reference $references {
        # DC represents constant connections as virtual leaves. The Verilog
        # writer emits literal 0/1; these are not missing physical cells.
        if {$reference eq "**logic_0**" || $reference eq "**logic_1**"} {
            lappend constant_references $reference
            continue
        }
        if {[sizeof_collection [get_lib_cells -quiet */$reference]] == 0} {
            error "No technology library cell for leaf reference $reference"
        }
    }
    # Generated row/column names must match between the Verilog and SDF.
    if {![change_names -rules verilog -hierarchy]} {
        error "Cannot prepare matching Verilog instance names"
    }
    puts "PE_ARRAY_EXPORT_REPORTS_RUNNING pe_instances=256 sram_instances=256"
    redirect -file [file join $output_dir area.rpt] {report_area}
    redirect -file [file join $output_dir setup.rpt] {
        report_timing -delay_type max -max_paths 3 -significant_digits 4
    }
    redirect -file [file join $output_dir hold.rpt] {
        report_timing -delay_type min -max_paths 3 -significant_digits 4
    }
    redirect -file [file join $output_dir violations.rpt] {report_constraint -all_violators}
    redirect -file [file join $output_dir constraints_check.rpt] {
        check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}
    }
    if {![write -format verilog -hierarchy -output [file join $output_dir pe_array_mapped.v]] ||
        ![write -format ddc -hierarchy -output [file join $output_dir pe_array_mapped.ddc]] ||
        ![write_sdc [file join $output_dir pe_array_constraints.sdc]]} {
        error "Cannot export the mapped array and constraints"
    }
    puts "PE_ARRAY_SDF_WRITING"
    set sdf_file [file join $output_dir pe_array_mapped.sdf]
    write_sdf -version 2.1 -significant_digits 6 $sdf_file
    if {![file exists $sdf_file] || [file size $sdf_file] == 0} {
        error "No array SDF was generated"
    }
    set setup_paths [get_timing_paths -delay_type max -max_paths 1]
    set hold_paths [get_timing_paths -delay_type min -max_paths 1]
    if {[sizeof_collection $setup_paths] == 0 || [sizeof_collection $hold_paths] == 0} {
        error "Missing constrained array timing paths"
    }
    set setup_slack 1e30
    set hold_slack 1e30
    foreach_in_collection path $setup_paths {
        set setup_slack [expr {min($setup_slack, [get_attribute $path slack])}]
    }
    foreach_in_collection path $hold_paths {
        set hold_slack [expr {min($hold_slack, [get_attribute $path slack])}]
    }
    set stream [open [file join $output_dir export_summary.rpt] w]
    puts $stream "scope=16x16 array exported from reused mapped PEs; no new compile or GLS"
    puts $stream "pe_instances=256 sram_instances=256 mapped_leaf_cells=$leaf_count"
    puts $stream "dc_virtual_constant_references=$constant_references"
    puts $stream "period_ns=10 setup_slack_ns=$setup_slack hold_slack_ns=$hold_slack"
    puts $stream "high_fanout_net_threshold=[get_app_var high_fanout_net_threshold]"
    puts $stream "high_fanout_net_pin_capacitance=[get_app_var high_fanout_net_pin_capacitance]"
    puts $stream "sdf_version=2.1 sdf_bytes=[file size $sdf_file] physical_parasitics=false"
    puts $stream "area_includes_engineering_sram_library=true soc_exported=false array_gls_performed=false"
    close $stream
    puts "PE_ARRAY_ARTIFACTS_CHECKING"
    puts [exec python3 [file join $project_dir scripts check_pe_array_export.py] $output_dir]
    puts "DC_PE_ARRAY_EXPORTED top=pe_array pe_instances=256 sram_instances=256"
}
export_mapped_pe_array [file dirname [file dirname [file normalize [info script]]]]
