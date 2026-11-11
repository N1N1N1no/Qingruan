# EXPERIMENTAL: full-SoC import did not complete on this VM (system OOM).
# Use read_pe.tcl for the validated tutorial step.
# Read the SoC using the frozen AXI CPU configuration; do not run compile.
# Reuse verified generic-synthesis output to avoid old DC SV limitations.
# A procedure prevents DC's source command from continuing after a failed step.
proc read_soc_design {project_dir} {
set output_dir [file join $project_dir build dc_read_design]
set library_dir [file join $project_dir build dc_libraries]
file mkdir $output_dir

exec python3 [file join $project_dir scripts prepare_dc_rtl.py]

set_app_var target_library [list [file join $library_dir standard.db]]
set_app_var link_library [list * [file join $library_dir standard.db] \
    [file join $library_dir sram.db]]
read_db [file join $library_dir standard.db]
read_db [file join $library_dir sram.db]
if {[sizeof_collection [get_lib_cells -quiet NangateOpenCellLibrary/*]] != 135 ||
    [sizeof_collection [get_lib_cells -quiet SRAM_6T_CORE_16x8_MC_TB/*]] != 1} {
    error "Expected standard and SRAM libraries have not loaded"
}
if {![read_verilog -rtl [file join $output_dir soc_dc.v]]} {
    error "Cannot read the generic SoC; inspect the DC log"
}
current_design soc_top
if {![link]} {
    error "SoC link failed; inspect unresolved references"
}
set sram_cells [get_cells -hierarchical \
    -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
set sram_count [sizeof_collection $sram_cells]
if {$sram_count != 256} {
    error "Expected 256 SRAM instances, got $sram_count"
}
if {[sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
    error "SRAM instances are not bound to their library cell"
}
if {![write -format ddc -hierarchy -output [file join $output_dir soc_elaborated.ddc]]} {
    error "Cannot save the elaborated SoC"
}
puts "DC_READ_DESIGN_PASS top=soc_top sram_instances=$sram_count"
}
read_soc_design [file dirname [file dirname [file normalize [info script]]]]
