# Initial AXI boundary assumptions; units ns/fF, ideal clock before CTS.
# Verify these budgets against the CPU/router when integrating the SoC.
create_clock -name pe_axi_clk -period 10 -waveform {0 5} [get_ports clk]
set_clock_uncertainty 0.1 [get_clocks pe_axi_clk]
set_clock_transition 0.005 [get_clocks pe_axi_clk]

# All incoming AXI channels, including valid/ready, ID and byte strobes.
set pe_axi_inputs [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_input_delay -clock pe_axi_clk -max 1.0 $pe_axi_inputs
set_input_delay -clock pe_axi_clk -min 0.0 $pe_axi_inputs
set_input_transition 0.02 $pe_axi_inputs

# Keep recovery/removal checks; model external reset release at falling edge.
set_input_delay -clock pe_axi_clk -clock_fall -max 0.0 [get_ports rst_n]
set_input_delay -clock pe_axi_clk -clock_fall -min 0.0 [get_ports rst_n]
set_input_transition 0.02 [get_ports rst_n]

set_output_delay -clock pe_axi_clk -max 1.0 [all_outputs]
set_output_delay -clock pe_axi_clk -min 0.0 [all_outputs]
set_load -pin_load 5.0 [all_outputs]
unset pe_axi_inputs
