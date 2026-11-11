# First DC exercise: read one real PE and its arithmetic/SRAM dependencies.
proc read_single_pe {project_dir} {
    set output_dir [file join $project_dir build dc_pe]
    set library_dir [file join $project_dir build dc_libraries]
    file mkdir [file join $output_dir work]
    set_app_var target_library [list [file join $library_dir standard.db]]
    set_app_var link_library [list * [file join $library_dir standard.db] \
        [file join $library_dir sram.db]]
    if {[sizeof_collection [get_libs -quiet NangateOpenCellLibrary]] == 0} {
        read_db [file join $library_dir standard.db]
    }
    if {[sizeof_collection [get_libs -quiet SRAM_6T_CORE_16x8_MC_TB]] == 0} {
        read_db [file join $library_dir sram.db]
    }
    if {[sizeof_collection [get_lib_cells -quiet NangateOpenCellLibrary/*]] != 135 ||
        [sizeof_collection [get_lib_cells -quiet SRAM_6T_CORE_16x8_MC_TB/*]] != 1} {
        error "Expected standard and SRAM libraries have not loaded"
    }
    define_design_lib PE_WORK -path [file join $output_dir work]
    set sources [list [file join $project_dir rtl rfsp16x8.v] \
        [file join $project_dir rtl Exact_8bit.v] \
        [file join $project_dir rtl adder.v] \
        [file join $project_dir rtl pe_1sram_4mul.v]]
    if {![analyze -library PE_WORK -format verilog \
        -define {SYNTHESIS PE_REFERENCE_MACRO} $sources]} {
        error "PE source analysis failed"
    }
    if {![elaborate pe_1sram_4mul -library PE_WORK]} {
        error "Cannot elaborate pe_1sram_4mul"
    }
    current_design pe_1sram_4mul
    if {![link]} {
        error "PE link failed"
    }
    set sram_cells [get_cells -hierarchical \
        -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    if {[sizeof_collection $sram_cells] != 1 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected one SRAM instance bound to the library"
    }
    if {![write -format ddc -hierarchy -output [file join $output_dir pe_elaborated.ddc]]} {
        error "Cannot save the elaborated PE"
    }
    puts "DC_READ_PE_PASS top=pe_1sram_4mul sram_instances=1"
}
read_single_pe [file dirname [file dirname [file normalize [info script]]]]
