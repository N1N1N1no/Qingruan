enable_write_lib_mode
set out /workspace/native/cache/libraries
file mkdir $out
if {[catch {
    if {![read_lib /home/zhz/contest/task1/libraries/standard.lib]} {error "standard Liberty import failed"}
    write_lib NangateOpenCellLibrary -format db -output $out/standard.db
    if {![read_lib /workspace/native/cache/libraries/sram-lc-compat.lib]} {error "SRAM Liberty import failed"}
    write_lib SRAM_6T_CORE_16x8_MC_TB -format db -output $out/sram.db
    foreach f [list $out/standard.db $out/sram.db] {
        if {![file exists $f] || [file size $f] < 1000} {error "compiled library missing: $f"}
    }
    puts "NATIVE_LIBRARIES_COMPILED_PASS"
} err]} {
    puts stderr "NATIVE_LIBRARIES_COMPILE_FAIL: $err"
    exit 1
}
exit 0
