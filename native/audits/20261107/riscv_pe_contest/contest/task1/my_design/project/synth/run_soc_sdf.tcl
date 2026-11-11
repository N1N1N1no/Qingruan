# Run the CPU program with whole-SoC synthesis SDF in an independent process.
proc run_soc_sdf {project_dir} {
    set runner [file join $project_dir synth run_soc_sdf.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} sdf_error]} {
        error "Whole-SoC SDF simulation failed; inspect the RESULT directory above: $sdf_error"
    }
}
run_soc_sdf [file dirname [file dirname [file normalize [info script]]]]
