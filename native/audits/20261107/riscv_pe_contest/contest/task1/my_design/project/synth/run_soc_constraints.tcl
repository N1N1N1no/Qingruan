# Apply whole-SoC assumptions in an isolated DC; preserve this DC session.
proc run_soc_constraints {project_dir} {
    set runner [file join $project_dir synth run_soc_constraints.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} constraints_error]} {
        error "Whole-SoC constraints failed; inspect the RESULT directory above: $constraints_error"
    }
}
run_soc_constraints [file dirname [file dirname [file normalize [info script]]]]
