# Apply and save AXI boundary assumptions. No compile or STA acceptance here.
proc constrain_pe_axi_system {project_dir {output_dir ""}} {
    if {[get_object_name [current_design]] ne "sram_multiplier_system_ID_WIDTH3"} {
        error "Read the AXI-controlled array first using synth/read_pe_axi.tcl"
    }
    set system_design [current_design]
    if {$output_dir eq ""} {
        set output_dir [file join $project_dir build dc_pe_axi_io]
    }
    file mkdir $output_dir
    foreach {pattern expected} {s_axi_awid* 3 s_axi_arid* 3 s_axi_bid* 3 s_axi_rid* 3 s_axi_wdata* 64 s_axi_rdata* 64} {
        if {[sizeof_collection [get_ports $pattern]] != $expected} {
            error "Unexpected AXI port width: $pattern"
        }
    }
    set axi_inputs [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
    if {[sizeof_collection [all_inputs]] != 176 ||
        [sizeof_collection $axi_inputs] != 174 ||
        [sizeof_collection [all_outputs]] != 80 ||
        [sizeof_collection [get_cells u_pe_array]] != 1} {
        error "Expected the 3-bit-ID AXI interface around a 16x16 array"
    }
    set array_reference [get_attribute [get_cells u_pe_array] ref_name]
    set array_design [get_designs -hierarchical -exact $array_reference]
    if {[sizeof_collection $array_design] != 1 ||
        ![get_attribute $array_design is_mapped]} {
        error "Expected one reused mapped array"
    }
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    if {[sizeof_collection $sram_cells] != 256 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected 256 SRAM instances bound to the real library"
    }
    set leaf_count [sizeof_collection [get_cells -hierarchical -filter "is_hierarchical == false"]]
    set unmapped_count [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    if {[get_attribute $system_design is_mapped] || $unmapped_count == 0} {
        error "This step expects the imported AXI control logic before compilation"
    }
    set existing_clocks [get_clocks -quiet *]
    if {[sizeof_collection $existing_clocks] > 1 ||
        ([sizeof_collection $existing_clocks] == 1 &&
         [get_object_name $existing_clocks] ne "pe_axi_clk")} {
        error "Unexpected old clocks; reload using synth/read_pe_axi.tcl"
    }
    redirect -variable unit_report {report_units}
    if {![regexp {Time_unit\s+:\s+1\.0e-09} $unit_report] ||
        ![regexp {Capacitive_load_unit\s+:\s+1\.0e-15} $unit_report]} {
        error "AXI tutorial expects ns/fF units"
    }
    set sdc_file [file join $project_dir synth pe_axi_io.sdc]
    set sdc_hash [exec sha256sum -- $sdc_file]
    puts "PE_AXI_CONSTRAINTS_PHASE=apply period_ns=10"
    flush stdout
    source $sdc_file
    if {[sizeof_collection [get_clocks -quiet *]] != 1 ||
        [sizeof_collection [get_clocks -quiet pe_axi_clk]] != 1 ||
        [get_attribute [get_clocks pe_axi_clk] period] != 10.0} {
        error "Expected exactly one 10 ns AXI system clock"
    }
    puts "PE_AXI_CONSTRAINTS_PHASE=report_ports"
    flush stdout
    redirect -file [file join $output_dir units.rpt] {report_units}
    redirect -file [file join $output_dir clocks.rpt] {
        report_clock -attributes -nosplit
        report_clock -skew -nosplit
    }
    redirect -file [file join $output_dir ports.rpt] {
        report_port -verbose -nosplit -significant_digits 6
    }
    if {[compare_collections [current_design] $system_design] != 0 ||
        [sizeof_collection [get_cells -hierarchical -filter "is_hierarchical == false"]] != $leaf_count ||
        [sizeof_collection [get_cells -hierarchical \
            -filter "is_hierarchical == false && is_unmapped == true"]] != $unmapped_count} {
        error "Unexpected design context or structure change while applying constraints"
    }
    puts "PE_AXI_CONSTRAINTS_PHASE=save"
    flush stdout
    if {![write_sdc [file join $output_dir pe_axi_constraints.sdc]] ||
        ![write -format ddc -hierarchy -output [file join $output_dir pe_axi_constrained.ddc]]} {
        error "Cannot save the AXI constraints and design"
    }
    if {[exec sha256sum -- $sdc_file] ne $sdc_hash} {
        error "The AXI SDC source changed during this step"
    }
    set stream [open [file join $output_dir assumptions.rpt] w]
    puts $stream "top=[get_object_name $system_design] array_reference=$array_reference"
    puts $stream "period_ns=10 uncertainty_ns=0.1 clock_transition_ns=0.005"
    puts $stream "axi_input_bits=174 input_max_ns=1 input_min_ns=0 input_transition_ns=0.02"
    puts $stream "reset_release=falling_edge reset_max_ns=0 reset_min_ns=0"
    puts $stream "output_bits=80 output_max_ns=1 output_min_ns=0 output_pin_load_ff=5"
    puts $stream "system_mapped=false unmapped_leaf_cells=$unmapped_count sram_instances=256"
    puts $stream "source_sdc_sha256=$sdc_hash"
    puts $stream "scope=AXI boundary assumptions only; no compile, STA acceptance, gate simulation or CPU"
    close $stream
    puts "DC_PE_AXI_CONSTRAINTS_APPLIED period_ns=10 axi_input_bits=174 output_bits=80"
}
constrain_pe_axi_system [file dirname [file dirname [file normalize [info script]]]]
