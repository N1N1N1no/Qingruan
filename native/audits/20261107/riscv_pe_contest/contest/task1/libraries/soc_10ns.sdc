# Confirmed competition period; current engineering duty cycle: 50%.
create_clock -name core_clk -period 10.0 -waveform {0.0 5.0} [get_ports clk]

# Initial analysis assumptions, not confirmed external interface specs.
set_clock_uncertainty 0.1 [get_clocks core_clk]
# Ideal pre-CTS clock with assumed 5 ps transition, within SRAM read grid.
set_clock_transition 0.005 [get_clocks core_clk]

set data_inputs {}
foreach port [all_inputs] {
    set name [get_full_name $port]
    if {$name ne "clk" && $name ne "rst_n"} {
        lappend data_inputs $port
    }
}
set_input_delay -clock core_clk -max 1.0 $data_inputs
set_input_delay -clock core_clk -min 0.0 $data_inputs
set_input_transition 0.02 $data_inputs

# Reference TB releases reset on the falling edge.
set_input_delay -clock core_clk -clock_fall -max 0.0 [get_ports rst_n]
set_input_delay -clock core_clk -clock_fall -min 0.0 [get_ports rst_n]
set_input_transition 0.02 [get_ports rst_n]

set_output_delay -clock core_clk -max 1.0 [all_outputs]
set_output_delay -clock core_clk -min 0.0 [all_outputs]
# OpenSTA reports capacitance in fF with standard.lib read first.
set_load 5.0 [all_outputs]
# No blanket false paths; no SPEF or physical clock tree.
