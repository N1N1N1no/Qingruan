# Map the AXI control RTL while retaining the already verified mapped array.
proc compile_pe_axi_control {project_dir {output_dir ""}} {
    if {[get_object_name [current_design]] ne "sram_multiplier_system_ID_WIDTH3"} {
        error "Load the AXI-controlled array using synth/read_pe_axi.tcl first"
    }
    set system_design [current_design]
    if {$output_dir eq ""} {
        set output_dir [file join $project_dir build dc_pe_axi_mapped]
    }
    file mkdir $output_dir
    if {[sizeof_collection [all_inputs]] != 176 ||
        [sizeof_collection [all_outputs]] != 80 ||
        [sizeof_collection [get_cells u_pe_array]] != 1 ||
        [sizeof_collection [get_clocks -quiet *]] != 1 ||
        [sizeof_collection [get_clocks -quiet pe_axi_clk]] != 1 ||
        [get_attribute [get_clocks pe_axi_clk] period] != 10.0} {
        error "Apply the AXI boundary assumptions using synth/constrain_pe_axi.tcl first"
    }
    set array_cell [get_cells u_pe_array]
    set array_reference [get_attribute $array_cell ref_name]
    set array_design [get_designs -hierarchical -exact $array_reference]
    if {[sizeof_collection $array_design] != 1 ||
        ![get_attribute $array_design is_mapped]} {
        error "Expected a unique mapped array object"
    }
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    if {[sizeof_collection $sram_cells] != 256 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected 256 SRAMs bound to the real library"
    }
    # Reapply the same budgets; never relax them to remove a violation.
    source [file join $project_dir synth pe_axi_io.sdc]
    set input_files [list [file join $project_dir synth pe_axi_io.sdc] \
        [file join $project_dir synth pe_axi_fingerprint.tcl]]
    set input_hashes [eval exec sha256sum -- $input_files]
    source [file join $project_dir synth pe_axi_fingerprint.tcl]
    puts "PE_AXI_SYNTH_PHASE=fingerprint_before"
    flush stdout
    set before_hash [pe_axi_array_fingerprint $array_design [file join $output_dir array_before.txt]]
    if {[compare_collections [current_design] $system_design] != 0} {
        error "The fingerprint check did not restore the system design"
    }
    set_dont_touch $array_cell true
    set_ungroup $array_cell false
    set_boundary_optimization $array_cell false
    set_fix_hold [get_clocks pe_axi_clk]
    puts "PE_AXI_SYNTH_PHASE=compile_control"
    flush stdout
    redirect -tee -file [file join $output_dir compile.log] {
        set compiled [compile]
    }
    if {!$compiled} {
        error "AXI control synthesis failed; inspect $output_dir/compile.log"
    }
    if {[compare_collections [current_design] $system_design] != 0 ||
        ![get_attribute $system_design is_mapped] ||
        [sizeof_collection [get_cells -hierarchical \
            -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {
        error "The AXI system was not fully mapped"
    }
    if {[sizeof_collection [get_cells u_pe_array]] != 1 ||
        [get_attribute [get_cells u_pe_array] ref_name] ne $array_reference ||
        [compare_collections [get_designs -hierarchical -exact $array_reference] $array_design] != 0} {
        error "The preserved array instance was replaced"
    }
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    if {[sizeof_collection $sram_cells] != 256 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "SRAM instances were not preserved"
    }
    puts "PE_AXI_SYNTH_PHASE=fingerprint_after"
    flush stdout
    set after_hash [pe_axi_array_fingerprint $array_design [file join $output_dir array_after.txt]]
    if {$before_hash ne $after_hash} {
        error "The reused array's cells or connections changed during synthesis"
    }
    puts "PE_AXI_ARRAY_PRESERVED sha256=$after_hash"
    puts "PE_AXI_SYNTH_PHASE=reports"
    flush stdout
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
    redirect -file [file join $output_dir ports.rpt] {
        report_port -verbose -nosplit -significant_digits 6
    }
    set setup_paths [get_timing_paths -delay_type max -max_paths 1]
    set hold_paths [get_timing_paths -delay_type min -max_paths 1]
    if {[sizeof_collection $setup_paths] == 0 || [sizeof_collection $hold_paths] == 0} {
        error "Missing timing paths in the mapped AXI system"
    }
    set setup_slack 1e30
    set hold_slack 1e30
    foreach_in_collection path $setup_paths {
        set setup_slack [expr {min($setup_slack, [get_attribute $path slack])}]
    }
    foreach_in_collection path $hold_paths {
        set hold_slack [expr {min($hold_slack, [get_attribute $path slack])}]
    }
    puts "PE_AXI_SYNTH_PHASE=save"
    flush stdout
    if {![write -format verilog -hierarchy -output [file join $output_dir pe_axi_mapped.v]] ||
        ![write_sdc [file join $output_dir pe_axi_constraints.sdc]] ||
        ![write -format ddc -hierarchy -output [file join $output_dir pe_axi_mapped.ddc]]} {
        error "Cannot save the mapped AXI system"
    }
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {
        error "An input changed during synthesis"
    }
    set stream [open [file join $output_dir synthesis_summary.rpt] w]
    puts $stream "top=[get_object_name $system_design] array_reference=$array_reference"
    puts $stream "mapped=true unmapped_leaf_cells=0 sram_instances=256"
    puts $stream "array_fingerprint_before=$before_hash array_fingerprint_after=$after_hash"
    puts $stream "period_ns=10 setup_slack_ns=$setup_slack hold_slack_ns=$hold_slack"
    puts $stream "array_dont_touch=true set_fix_hold=true"
    puts $stream "scope=AXI control mapping around preserved PE array; no CPU, gate simulation or physical parasitics"
    puts $stream "Review violations.rpt; DONE alone does not establish timing acceptance."
    close $stream
    puts "PE_AXI_SETUP_SLACK_NS=$setup_slack"
    puts "PE_AXI_HOLD_SLACK_NS=$hold_slack"
    puts "DC_PE_AXI_SYNTHESIS_DONE mapped=true sram_instances=256"
}
compile_pe_axi_control [file dirname [file dirname [file normalize [info script]]]]
