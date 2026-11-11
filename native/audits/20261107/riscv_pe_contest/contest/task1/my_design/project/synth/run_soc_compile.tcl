# Map whole-SoC CPU/router in an isolated DC; preserve this DC session.
proc run_soc_compile {project_dir} {
    set runner [file join $project_dir synth run_soc_compile.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} compile_error]} {
        error "Whole-SoC mapping failed; inspect the RESULT directory above: $compile_error"
    }
}
run_soc_compile [file dirname [file dirname [file normalize [info script]]]]
