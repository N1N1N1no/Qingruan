# Whole-SoC boundary; DC units must first be verified as ns/fF.
# Period follows the supplied 10 ns engineering example, rather than a
# separately confirmed final contest frequency. External AXI arrival,
# budget and load values also remain the reference engineering assumptions.
create_clock -name core_clk -period 10.0 -waveform {0.0 5.0} [get_ports clk]
set_clock_uncertainty 0.1 [get_clocks core_clk]
set_clock_transition 0.005 [get_clocks core_clk]

# Instruction and data-memory AXI responses, including ready/valid and IDs.
set soc_data_inputs [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_input_delay -clock core_clk -max 1.0 $soc_data_inputs
set_input_delay -clock core_clk -min 0.0 $soc_data_inputs
set_input_transition 0.02 $soc_data_inputs

# The reference testbench releases active-low reset at the falling edge.
# Retain asynchronous recovery/removal checks.
set_input_delay -clock core_clk -clock_fall -max 0.0 [get_ports rst_n]
set_input_delay -clock core_clk -clock_fall -min 0.0 [get_ports rst_n]
set_input_transition 0.02 [get_ports rst_n]

# External instruction/data memories receive the outgoing AXI channels.
set_output_delay -clock core_clk -max 1.0 [all_outputs]
set_output_delay -clock core_clk -min 0.0 [all_outputs]
set_load -pin_load 5.0 [all_outputs]
unset soc_data_inputs

# CPU/router/PE signals inside soc_top receive no repeated I/O budgets.
# No blanket false paths, disabled checks, propagated clocks or SPEF here.
