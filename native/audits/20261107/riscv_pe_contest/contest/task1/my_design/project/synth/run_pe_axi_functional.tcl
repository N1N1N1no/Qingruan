# Run the external simulator and stream its progress into the existing DC shell.
proc run_pe_axi_functional_simulation {project_dir} {
    set runner [file join $project_dir synth run_pe_axi_functional.sh]
    set stream [open [list | bash $runner 2>@1] r]
    fconfigure $stream -buffering line
    while {[gets $stream line] >= 0} {
        puts $line
        flush stdout
    }
    if {[catch {close $stream} simulation_error]} {
        error "AXI functional simulation failed; inspect the RESULT directory above: $simulation_error"
    }
}
run_pe_axi_functional_simulation [file dirname [file dirname [file normalize [info script]]]]
