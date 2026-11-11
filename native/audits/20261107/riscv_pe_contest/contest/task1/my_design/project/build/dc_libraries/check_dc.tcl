# Confirm that Design Compiler can load the converted libraries.
set library_dir [file dirname [file normalize [info script]]]
read_db [file join $library_dir standard.db]
read_db [file join $library_dir sram.db]
list_libs
set standard_cells [sizeof_collection [get_lib_cells NangateOpenCellLibrary/*]]
set sram_cells [sizeof_collection [get_lib_cells SRAM_6T_CORE_16x8_MC_TB/*]]
if {$standard_cells != 135 || $sram_cells != 1} {
    error "Unexpected library cell counts: standard=$standard_cells sram=$sram_cells"
}
puts "DC_LIBRARY_CHECK_PASS standard_cells=$standard_cells sram_cells=$sram_cells"
exit
