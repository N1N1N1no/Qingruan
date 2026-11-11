# Compare the CPU smoke program in original RTL and mapped SoC; keep this DC.
proc run_soc_functional {project_dir} {
    set runner [file join $project_dir synth run_soc_functional.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} functional_error]} {
        error "Whole-SoC functional simulation failed; inspect the RESULT directory above: $functional_error"
    }
}
run_soc_functional [file dirname [file dirname [file normalize [info script]]]]
