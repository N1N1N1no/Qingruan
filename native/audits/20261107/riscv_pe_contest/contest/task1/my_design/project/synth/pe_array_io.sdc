# Initial budgets at the 16x16 array boundary; verify against its wrapper later.
# Time unit: ns. Capacitance unit: fF. The clock is ideal before CTS.
create_clock -name array_clk -period 10 -waveform {0 5} [get_ports clk]
set_clock_uncertainty 0.1 [get_clocks array_clk]
set_clock_transition 0.005 [get_clocks array_clk]

# Apply external delay only at array ports. PE-to-PE paths remain internal.
set array_data_inputs [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_input_delay -clock array_clk -max 1.0 $array_data_inputs
set_input_delay -clock array_clk -min 0.0 $array_data_inputs
set_input_transition 0.02 $array_data_inputs

# Model reset release at the falling clock edge; retain recovery/removal checks.
set_input_delay -clock array_clk -clock_fall -max 0.0 [get_ports rst_n]
set_input_delay -clock array_clk -clock_fall -min 0.0 [get_ports rst_n]
set_input_transition 0.02 [get_ports rst_n]

set_output_delay -clock array_clk -max 1.0 [all_outputs]
set_output_delay -clock array_clk -min 0.0 [all_outputs]
set_load -pin_load 5.0 [all_outputs]
unset array_data_inputs
