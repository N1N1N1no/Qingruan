# Show the external simulator's progress while preserving the current DC design.
proc run_pe_axi_sdf_simulation {project_dir} {
    set runner [file join $project_dir synth run_pe_axi_sdf.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} simulation_error]} {
        error "AXI SDF simulation failed; inspect the RESULT directory above: $simulation_error"
    }
}
run_pe_axi_sdf_simulation [file dirname [file dirname [file normalize [info script]]]]
