#!/usr/bin/env bash
set -euo pipefail
cd /workspace/native/validation/toolchain-flow
printf 'Validating native kernel: '; uname -r
# Tiny probes exercise real synthesis, constrained timing and annotated simulation.
# Full SoC implementation remains the contestant's design task.
rm -f dc.log pt.log sram-pt.log gate-run.log sram-run.log sdf-annotation.log sram-sdf-annotation.log yosys-abc.log opensta-flow.log
/usr/bin/gcc -c /home/zhz/contest/task1/base/compat/pthread_yield_compat.c -o pthread_yield_compat.o
dc_shell -no_gui -f dc.tcl > dc.log 2>&1
pt_shell -f pt.tcl > pt.log 2>&1
vcs -full64 -sverilog -debug_pp -timescale=1ns/1ps -top tb_native_env_probe tb.v mapped.v "$TASK1_STD_MODEL" pthread_yield_compat.o -LDFLAGS "-Wl,--no-as-needed" -o gate_simv -l gate-compile.log > gate-build-output.log 2>&1
./gate_simv +sdfverbose -l gate-simulation.log > gate-run.log 2>&1
pt_shell -f sram-pt.tcl > sram-pt.log 2>&1
vcs -full64 -sverilog -debug_pp -timescale=1ns/1ps -top tb_native_sram tb_sram.v sram_probe.v "$TASK1_SRAM_MODEL_DC" pthread_yield_compat.o -LDFLAGS "-Wl,--no-as-needed" -o sram_simv -l sram-compile.log > sram-build-output.log 2>&1
./sram_simv +sdfverbose -l sram-simulation.log > sram-run.log 2>&1
"$TASK1_SV2V" native_open_probe.sv > open-converted.v
"$TASK1_YOSYS" -p "read_liberty -lib $TASK1_STD_LIB; read_verilog open-converted.v; hierarchy -check -top native_open_probe; synth -top native_open_probe; dfflibmap -liberty $TASK1_STD_LIB; abc -exe $TASK1_ABC -liberty $TASK1_STD_LIB; clean; check -assert; stat -liberty $TASK1_STD_LIB; write_verilog -noattr open-mapped.v" > yosys-abc.log 2>&1
"$TASK1_STA" -exit opensta.tcl > opensta-flow.log 2>&1
python3 /workspace/native/scripts/check-toolchain-results.py
