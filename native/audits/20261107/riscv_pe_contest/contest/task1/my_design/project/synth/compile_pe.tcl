# Tutorial synthesis of one PE. Only the tutorial clock is constrained.
proc compile_single_pe {project_dir} {
    set output_dir [file join $project_dir build dc_pe]
    if {[get_object_name [current_design]] ne "pe_1sram_4mul"} {
        error "Load pe_1sram_4mul with read_pe.tcl first"
    }
    if {[sizeof_collection [get_clocks -quiet pe_clk]] != 1} {
        error "Create the tutorial pe_clk clock before compiling"
    }
    if {![compile]} {
        error "PE synthesis failed"
    }
    if {![get_attribute [current_design] is_mapped]} {
        error "The PE has not been mapped"
    }
    set unmapped [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]
    if {[sizeof_collection $unmapped] != 0} {
        error "Unmapped logic remains in the PE"
    }
    set sram_cells [get_cells -hierarchical \
        -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    if {[sizeof_collection $sram_cells] != 1 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected one SRAM instance bound to its library"
    }
    redirect -file [file join $output_dir area.rpt] {report_area}
    redirect -file [file join $output_dir timing.rpt] {report_timing -max_paths 1}
    if {![write -format verilog -hierarchy -output [file join $output_dir pe_mapped.v]]} {
        error "Cannot write the mapped PE Verilog"
    }
    if {![write -format ddc -hierarchy -output [file join $output_dir pe_mapped.ddc]]} {
        error "Cannot save the mapped PE"
    }
    puts "DC_COMPILE_PE_PASS mapped=true unmapped_leaf_cells=0 sram_instances=1"
}
compile_single_pe [file dirname [file dirname [file normalize [info script]]]]
