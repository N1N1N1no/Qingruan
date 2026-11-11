# Stream progress from the external simulator while keeping this DC session.
proc run_array_sdf_simulation {project_dir} {
    set runner [file join $project_dir synth run_pe_array_sdf.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} simulation_error]} {
        error "Array SDF simulation failed; inspect the RESULT directory above: $simulation_error"
    }
}
run_array_sdf_simulation [file dirname [file dirname [file normalize [info script]]]]
