# Tutorial clock target for one PE, not a complete competition SDC.
# The loaded standard cell library defines time in nanoseconds.
create_clock -name pe_clk -period 10 [get_ports clk]
