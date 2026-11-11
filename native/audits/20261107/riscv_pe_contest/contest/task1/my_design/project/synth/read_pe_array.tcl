# Read the real 16x16 array while reusing the mapped single-PE hierarchy.
# This checks structure only. Array constraints and compilation come later.
proc read_mapped_pe_array {project_dir} {
    set output_dir [file join $project_dir build dc_pe_array]
    set library_dir [file join $project_dir build dc_libraries]
    set pe_ddc [file join $project_dir build dc_pe_io pe_mapped.ddc]
    set array_rtl [file join $project_dir rtl pe_array.v]
    file mkdir [file join $output_dir work]
    set input_hashes [exec sha256sum -- $pe_ddc $array_rtl \
        [file join $library_dir standard.db] [file join $library_dir sram.db]]
    set_app_var target_library [list [file join $library_dir standard.db]]
    set_app_var link_library [list * [file join $library_dir standard.db] \
        [file join $library_dir sram.db]]
    if {[sizeof_collection [get_libs -quiet NangateOpenCellLibrary]] == 0} {
        read_db [file join $library_dir standard.db]
    }
    if {[sizeof_collection [get_libs -quiet SRAM_6T_CORE_16x8_MC_TB]] == 0} {
        read_db [file join $library_dir sram.db]
    }
    # Reloading an already open DDC produces one DDB-24 message per design.
    # Retain those messages in the log and require the expected import result.
    set loaded_designs {}
    redirect -file [file join $output_dir pe_load.log] {
        set loaded_designs [read_ddc $pe_ddc]
    }
    if {[lsearch -exact $loaded_designs pe_1sram_4mul] < 0} {
        error "Cannot read the mapped PE; inspect $output_dir/pe_load.log"
    }
    current_design pe_1sram_4mul
    if {![get_attribute [current_design] is_mapped]} {
        error "Expected the saved, mapped single PE"
    }
    # The PE's tutorial interface budgets are not the array's constraints.
    # Clearing these annotations does not change the mapped cells or wires.
    reset_design
    if {![get_attribute [current_design] is_mapped]} {
        error "Mapped PE state was not preserved"
    }
    define_design_lib PE_ARRAY_WORK -path [file join $output_dir work]
    if {![analyze -library PE_ARRAY_WORK -format verilog $array_rtl]} {
        error "Cannot analyze pe_array.v"
    }
    if {![elaborate pe_array -library PE_ARRAY_WORK]} {
        error "Cannot elaborate the PE array"
    }
    current_design pe_array
    if {![link]} {
        error "Cannot link the array to the mapped PE and libraries"
    }
    reset_design
    foreach {pattern expected} {sram_ce* 16 sram_we* 16 weight_in* 128 data_in* 512 psum_in* 2048 psum_out* 2048} {
        if {[sizeof_collection [get_ports $pattern]] != $expected} {
            error "Expected the default 16x16 array interface: $pattern"
        }
    }
    set pe_cells [get_cells -hierarchical -filter "ref_name == pe_1sram_4mul"]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    set pe_count [sizeof_collection $pe_cells]
    set sram_count [sizeof_collection $sram_cells]
    if {$pe_count != 256 || $sram_count != 256} {
        error "Expected 256 PEs and 256 SRAMs; got $pe_count PEs and $sram_count SRAMs"
    }
    if {[sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "SRAM instances must resolve to one real library cell"
    }
    set unmapped_count [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    if {$unmapped_count != 0} {
        error "Unexpected unmapped leaf cells: $unmapped_count"
    }
    if {[sizeof_collection [get_clocks -quiet *]] != 0} {
        error "Old single-PE clocks remain; array constraints have not been set"
    }
    if {![write -format ddc -hierarchy -output [file join $output_dir pe_array_structure.ddc]]} {
        error "Cannot save the array structure"
    }
    set stream [open [file join $output_dir inputs.sha256] w]
    puts $stream $input_hashes
    close $stream
    set stream [open [file join $output_dir structure.rpt] w]
    puts $stream "top=pe_array rows=16 columns=16"
    puts $stream "pe_instances=$pe_count sram_instances=$sram_count"
    puts $stream "unmapped_leaf_cells=$unmapped_count array_constraints_applied=false"
    puts $stream "scope=structure import with a reused mapped PE; no array compile or timing validation"
    close $stream
    puts "DC_READ_PE_ARRAY_PASS top=pe_array pe_instances=$pe_count sram_instances=$sram_count"
}
read_mapped_pe_array [file dirname [file dirname [file normalize [info script]]]]
