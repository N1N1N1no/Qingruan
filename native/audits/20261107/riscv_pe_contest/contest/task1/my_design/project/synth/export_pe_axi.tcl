# Export the already mapped AXI system and its synthesis delays.
# Keep the caller's actual design object; no relink, renaming or compilation.
proc export_mapped_pe_axi {project_dir {output_dir ""}} {
    set project_dir [file normalize $project_dir]
    if {[sizeof_collection [current_design]] != 1 ||
        [get_object_name [current_design]] ne "sram_multiplier_system_ID_WIDTH3"} {
        error "Load the mapped AXI system before exporting its delays"
    }
    set system_design [current_design]
    if {$output_dir eq ""} {
        set output_dir [file join $project_dir build dc_pe_axi_export]
    }
    set output_dir [file normalize $output_dir]
    foreach source_dir {dc_pe_axi dc_pe_axi_io dc_pe_axi_mapped} {
        if {$output_dir eq [file join $project_dir build $source_dir]} {
            error "Use a separate export directory; do not overwrite $source_dir"
        }
    }
    if {[sizeof_collection [all_inputs]] != 176 ||
        [sizeof_collection [all_outputs]] != 80 ||
        [sizeof_collection [get_cells -quiet u_pe_array]] != 1} {
        error "Expected the 3-bit-ID AXI interface around a 16x16 array"
    }
    foreach {pattern expected} {s_axi_awid* 3 s_axi_arid* 3 s_axi_bid* 3 s_axi_rid* 3 s_axi_wdata* 64 s_axi_rdata* 64} {
        if {[sizeof_collection [get_ports $pattern]] != $expected} {
            error "Unexpected AXI port width: $pattern"
        }
    }
    if {[sizeof_collection [get_clocks -quiet *]] != 1 ||
        [sizeof_collection [get_clocks -quiet pe_axi_clk]] != 1 ||
        [get_attribute [get_clocks pe_axi_clk] period] != 10.0} {
        error "Expected the already applied unique 10 ns pe_axi_clk"
    }
    redirect -variable unit_report {report_units}
    if {![regexp {Time_unit\s+:\s+1\.0e-09} $unit_report] ||
        ![regexp {Capacitive_load_unit\s+:\s+1\.0e-15} $unit_report]} {
        error "The AXI export expects the previously applied ns/fF boundary assumptions"
    }
    if {![get_attribute $system_design is_mapped] ||
        [sizeof_collection [get_cells -hierarchical \
            -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {
        error "Compile the AXI control layer before exporting the mapped system"
    }
    set array_cell [get_cells u_pe_array]
    set array_reference [get_attribute $array_cell ref_name]
    set array_design [get_designs -hierarchical -exact $array_reference]
    if {[sizeof_collection $array_design] != 1 ||
        ![get_attribute $array_design is_mapped]} {
        error "Expected one actual mapped array design object"
    }
    set pe_cells [get_cells -hierarchical -filter "ref_name =~ *pe_1sram_4mul"]
    set pe_count [sizeof_collection $pe_cells]
    set pe_references [lsort -unique [get_attribute $pe_cells ref_name]]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    set sram_count [sizeof_collection $sram_cells]
    if {$pe_count != 256 || [llength $pe_references] != 1 ||
        $sram_count != 256 || [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected 256 preserved PEs and 256 SRAMs bound to the real library"
    }
    set leaf_cells [get_cells -hierarchical -filter "is_hierarchical == false"]
    set leaf_count [sizeof_collection $leaf_cells]
    set constant_count 0
    array set reference_counts {}
    foreach reference [get_attribute $leaf_cells ref_name] {
        incr reference_counts($reference)
        if {$reference eq "**logic_0**" || $reference eq "**logic_1**"} {
            incr constant_count
        }
    }
    foreach reference [array names reference_counts] {
        if {$reference ne "**logic_0**" && $reference ne "**logic_1**" &&
            [sizeof_collection [get_lib_cells -quiet */$reference]] == 0} {
            error "No technology library cell for mapped leaf reference $reference"
        }
    }
    set input_files [list [file join $project_dir synth export_pe_axi.tcl] \
        [file join $project_dir synth pe_axi_fingerprint.tcl] \
        [file join $project_dir scripts check_pe_axi_export.py]]
    set input_hashes [eval exec sha256sum -- $input_files]
    file mkdir $output_dir
    # A failed new attempt must never leave a previous success manifest visible.
    file delete -force [file join $output_dir artifact_summary.json]
    source [file join $project_dir synth pe_axi_fingerprint.tcl]
    puts "PE_AXI_EXPORT_PHASE=fingerprint_before"
    flush stdout
    set before_hash [pe_axi_array_fingerprint $array_design [file join $output_dir array_before.txt]]
    if {[compare_collections [current_design] $system_design] != 0} {
        error "Array fingerprint did not restore the AXI system context"
    }
    puts "PE_AXI_EXPORT_PHASE=reports"
    flush stdout
    redirect -file [file join $output_dir check_design.rpt] {set design_checked [check_design]}
    if {!$design_checked} {
        error "The mapped AXI system failed check_design"
    }
    redirect -file [file join $output_dir units.rpt] {report_units}
    redirect -file [file join $output_dir clocks.rpt] {
        report_clock -attributes -nosplit
        report_clock -skew -nosplit
    }
    redirect -file [file join $output_dir ports.rpt] {
        report_port -verbose -nosplit -significant_digits 6
    }
    redirect -file [file join $output_dir area.rpt] {report_area}
    redirect -file [file join $output_dir setup.rpt] {
        report_timing -delay_type max -max_paths 5 -significant_digits 6
    }
    redirect -file [file join $output_dir hold.rpt] {
        report_timing -delay_type min -max_paths 5 -significant_digits 6
    }
    redirect -file [file join $output_dir violations.rpt] {
        report_constraint -all_violators -significant_digits 6
    }
    redirect -file [file join $output_dir constraints_check.rpt] {
        check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}
    }
    set setup_paths [get_timing_paths -delay_type max -max_paths 1]
    set hold_paths [get_timing_paths -delay_type min -max_paths 1]
    if {[sizeof_collection $setup_paths] == 0 || [sizeof_collection $hold_paths] == 0} {
        error "Missing constrained AXI setup or hold timing paths"
    }
    set setup_slack 1e30
    set hold_slack 1e30
    foreach_in_collection path $setup_paths {
        set setup_slack [expr {min($setup_slack, [get_attribute $path slack])}]
    }
    foreach_in_collection path $hold_paths {
        set hold_slack [expr {min($hold_slack, [get_attribute $path slack])}]
    }
    puts "PE_AXI_EXPORT_PHASE=write_design"
    flush stdout
    if {![write -format verilog -hierarchy -output [file join $output_dir pe_axi_mapped.v]] ||
        ![write -format ddc -hierarchy -output [file join $output_dir pe_axi_mapped.ddc]] ||
        ![write_sdc [file join $output_dir pe_axi_constraints.sdc]]} {
        error "Cannot export the mapped AXI system and its actual constraints"
    }
    puts "PE_AXI_EXPORT_PHASE=write_sdf"
    flush stdout
    set sdf_file [file join $output_dir pe_axi_mapped.sdf]
    file delete -force $sdf_file
    write_sdf -version 2.1 -significant_digits 6 $sdf_file
    if {![file exists $sdf_file] || [file size $sdf_file] == 0} {
        error "No new AXI system SDF was generated"
    }
    puts "PE_AXI_EXPORT_PHASE=fingerprint_after"
    flush stdout
    set after_hash [pe_axi_array_fingerprint $array_design [file join $output_dir array_after.txt]]
    if {$before_hash ne $after_hash ||
        [compare_collections [current_design] $system_design] != 0 ||
        [get_attribute [get_cells u_pe_array] ref_name] ne $array_reference ||
        [compare_collections [get_designs -hierarchical -exact $array_reference] $array_design] != 0 ||
        [sizeof_collection [get_cells -hierarchical -filter "is_hierarchical == false"]] != $leaf_count ||
        [sizeof_collection [get_cells -hierarchical -filter "ref_name =~ *pe_1sram_4mul"]] != $pe_count ||
        [sizeof_collection [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != $sram_count} {
        error "Design context or preserved array structure changed during export"
    }
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {
        error "An export script changed during this run"
    }
    set stream [open [file join $output_dir cell_counts.rpt] w]
    foreach reference [lsort [array names reference_counts]] {
        puts $stream [list $reference $reference_counts($reference)]
    }
    close $stream
    set stream [open [file join $output_dir export_summary.rpt] w]
    foreach {key value} [list \
        top [get_object_name $system_design] \
        current_design_object $system_design \
        source current_in_memory_mapped_design \
        array_reference $array_reference \
        pe_reference [lindex $pe_references 0] \
        pe_instances $pe_count sram_instances $sram_count \
        mapped_leaf_cells $leaf_count \
        physical_leaf_cells [expr {$leaf_count - $constant_count}] \
        virtual_constant_leaf_cells $constant_count unmapped_leaf_cells 0 \
        period_ns 10 setup_slack_ns $setup_slack hold_slack_ns $hold_slack \
        array_fingerprint_before $before_hash array_fingerprint_after $after_hash \
        high_fanout_net_threshold [get_app_var high_fanout_net_threshold] \
        high_fanout_net_pin_capacitance [get_app_var high_fanout_net_pin_capacitance] \
        sdf_version 2.1 sdf_bytes [file size $sdf_file] \
        compile_performed false names_changed false constraints_reapplied false \
        check_design_success true physical_parasitics false \
        gate_simulation_performed false sdf_annotated false cpu_included false \
        timing_acceptance_established false] {
        puts $stream "$key=$value"
    }
    close $stream
    puts "PE_AXI_EXPORT_PHASE=check_artifacts"
    flush stdout
    set checker [file join $project_dir scripts check_pe_axi_export.py]
    set stream [open [list | python3 $checker $output_dir 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} artifact_error]} {
        error "AXI export artifact check failed; inspect $output_dir: $artifact_error"
    }
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {
        file delete -force [file join $output_dir artifact_summary.json]
        error "An export script changed during artifact verification"
    }
    puts "DC_PE_AXI_EXPORTED top=[get_object_name $system_design] pe_instances=$pe_count sram_instances=$sram_count"
    puts "SDF export alone does not establish timed simulation or timing acceptance."
}
export_mapped_pe_axi [file dirname [file dirname [file normalize [info script]]]]
