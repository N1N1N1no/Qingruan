# Private subprocess entry: source export_pe_sdf.tcl directly for interactive use.
# DC returns to its prompt after -f; explicitly end this batch-only process.
set sh_continue_on_error false
set pe_export_script [file join [file dirname [file normalize [info script]]] export_pe_sdf.tcl]
if {[catch {source $pe_export_script} pe_export_error]} {
    puts stderr "PE_SDF_EXPORT_FAILED: $pe_export_error"
    if {[info exists ::errorInfo]} {
        puts stderr $::errorInfo
    }
    exit 1
}
puts "PE_SDF_EXPORT_BATCH_DONE"
exit 0
