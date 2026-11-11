# Single-PE tutorial assumptions, not an agreed array/SoC interface contract.
# Required units: ns and fF (checked by constrain_pe.tcl before loading).
create_clock -name pe_clk -period 10 -waveform {0 5} [get_ports clk]
set_clock_uncertainty 0.1 [get_clocks pe_clk]
set_clock_transition 0.005 [get_clocks pe_clk]

# External source delivers data between 0 and 1 ns after the rising edge.
set pe_data_inputs [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_input_delay -clock pe_clk -max 1.0 $pe_data_inputs
set_input_delay -clock pe_clk -min 0.0 $pe_data_inputs
set_input_transition 0.02 $pe_data_inputs

# Tutorial assumption: external asynchronous reset is released at a falling
# clock edge. Keep recovery/removal checks; do not hide reset with false paths.
set_input_delay -clock pe_clk -clock_fall -max 0.0 [get_ports rst_n]
set_input_delay -clock pe_clk -clock_fall -min 0.0 [get_ports rst_n]
set_input_transition 0.02 [get_ports rst_n]

# Reserve 1 ns for downstream logic/setup outside this PE; earliest hold budget
# is 0 ns. Each output drives an assumed 5 fF external pin load.
set_output_delay -clock pe_clk -max 1.0 [all_outputs]
set_output_delay -clock pe_clk -min 0.0 [all_outputs]
set_load -pin_load 5.0 [all_outputs]
unset pe_data_inputs
