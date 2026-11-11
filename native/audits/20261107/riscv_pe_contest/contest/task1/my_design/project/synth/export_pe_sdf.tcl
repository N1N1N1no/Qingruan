# Export delays for the already mapped PE; do not change or recompile it.
proc export_pe_delays {project_dir} {
    set output_dir [file join $project_dir build dc_pe_io]
    set library_dir [file join $project_dir build dc_libraries]
    set ddc_file [file join $output_dir pe_mapped.ddc]
    # The standalone regression uses immutable copies of the DDC and libraries.
    if {[info exists ::env(PE_SDF_OUTPUT_DIR)]} {
        set output_dir $::env(PE_SDF_OUTPUT_DIR)
    }
    if {[info exists ::env(PE_SDF_LIBRARY_DIR)]} {
        set library_dir $::env(PE_SDF_LIBRARY_DIR)
    }
    if {[info exists ::env(PE_SDF_DDC)]} {
        set ddc_file $::env(PE_SDF_DDC)
    }
    file mkdir $output_dir
    set_app_var target_library [list [file join $library_dir standard.db]]
    set_app_var link_library [list * [file join $library_dir standard.db] \
        [file join $library_dir sram.db]]
    read_ddc $ddc_file
    current_design pe_1sram_4mul
    if {![get_attribute [current_design] is_mapped] || ![link]} {
        error "Expected the mapped single PE"
    }
    set sdf_file [file join $output_dir pe_mapped.sdf]
    if {![write -format verilog -hierarchy -output [file join $output_dir exported_pe.v]]} {
        error "Cannot export the matching mapped netlist"
    }
    write_sdf -version 2.1 -significant_digits 6 $sdf_file
    if {![file exists $sdf_file] || [file size $sdf_file] == 0} {
        error "No SDF file was written"
    }
    puts "PE_SDF_EXPORTED $sdf_file"
}
export_pe_delays [file dirname [file dirname [file normalize [info script]]]]
