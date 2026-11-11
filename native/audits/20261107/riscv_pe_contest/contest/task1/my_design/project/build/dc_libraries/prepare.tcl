# Convert the supplied Liberty files without changing the source libraries.
set output_dir [file dirname [file normalize [info script]]]
if {![read_lib [file normalize [file join $output_dir .. .. .. .. libraries standard.lib]]]} {
    error "Cannot compile the standard cell library"
}
if {![write_lib NangateOpenCellLibrary -format db -output [file join $output_dir standard.db]]} {
    error "Cannot write standard.db"
}
if {![read_lib [file join $output_dir sram_dc_compatible.lib]]} {
    error "Cannot compile the SRAM library"
}
if {![write_lib SRAM_6T_CORE_16x8_MC_TB -format db -output [file join $output_dir sram.db]]} {
    error "Cannot write sram.db"
}
puts "LIBRARY_CONVERSION_FINISHED"
exit
