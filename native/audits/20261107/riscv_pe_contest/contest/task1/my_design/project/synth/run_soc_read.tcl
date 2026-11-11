# Read/link the SoC in an isolated process and keep this DC session intact.
proc run_soc_structure_read {project_dir} {
    set runner [file join $project_dir synth run_soc_read.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} read_error]} {
        error "SoC read/link failed; inspect the RESULT directory above: $read_error"
    }
}
run_soc_structure_read [file dirname [file dirname [file normalize [info script]]]]
