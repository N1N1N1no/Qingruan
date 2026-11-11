SHELL := /bin/bash
.DEFAULT_GOAL := help
RV_ROOT ?= $(abspath ../vendor/Cores-VeeR-EL2)
RISCV_PREFIX ?= riscv-none-elf-
PYTHON ?= python3
# Freeze existing snapshots by default; initialize explicitly with PREPARE_ARGS=.
PREPARE_ARGS ?= --skip-config
VCS ?= vcs
IVERILOG ?= iverilog
VVP ?= vvp
VCS_ARGS ?=
RUN_ARGS ?= +WAVE
ISA ?= rv32imc_zicsr

.PHONY: help prepare sw rtl run ram-test pe-test wave
help:
	@echo 'make prepare : reuse frozen config; PREPARE_ARGS= explicitly regenerates it'
	@echo 'make sw      : compile the new PE smoke firmware'
	@echo 'make rtl     : VCS compile soc_top + lightweight testbench'
	@echo 'make run     : run firmware, check all 64 output words'
	@echo 'make pe-test : CPU-independent AXI/PE/RAM test using VCS'
	@echo 'make ram-test: weight memory test using VCS'
	@echo 'make wave    : open build/soc.vpd in DVE'

prepare:
	$(PYTHON) scripts/prepare.py --rv-root "$(RV_ROOT)" $(PREPARE_ARGS)

sw: build/program.hex
build/program.hex: sw/start.S sw/link.ld sw/pe_smoke.c scripts/bin2hex.py
	mkdir -p build
	$(RISCV_PREFIX)gcc -march=$(ISA) -mabi=ilp32 -O2 -ffreestanding -fno-builtin -fno-pic -msmall-data-limit=0 -nostdlib -nostartfiles -Wl,--no-relax -Wl,-T,sw/link.ld -Wl,-Map,build/pe_smoke.map sw/start.S sw/pe_smoke.c -o build/pe_smoke.elf
	$(RISCV_PREFIX)objcopy -O binary build/pe_smoke.elf build/program.bin
	$(RISCV_PREFIX)objdump -d build/pe_smoke.elf > build/pe_smoke.dis
	$(PYTHON) scripts/bin2hex.py build/program.bin build/program.hex

rtl: prepare
	$(VCS) -full64 -sverilog -timescale=1ns/1ps +define+VCS -debug_access+all -f build/rtl.f tb/axi_ram_model.sv tb/tb_soc.sv -top tb_soc -Mdir=build/csrc -o build/simv -l build/compile.log $(VCS_ARGS)

run: sw rtl
	./build/simv +MEM_HEX=build/program.hex $(RUN_ARGS) -l build/run.log

ram-test:
	mkdir -p build
	$(VCS) -full64 -sverilog -timescale=1ns/1ps rtl/rfsp16x8.v tests/tb_weight_ram.sv -top tb_weight_ram -Mdir=build/ram_csrc -o build/ram_simv -l build/ram_compile.log $(VCS_ARGS)
	./build/ram_simv -l build/ram_test.log

pe-test: ram-test
	mkdir -p build
	$(VCS) -full64 -sverilog -timescale=1ns/1ps -f tests/pe.f -top tb_pe_axi -Mdir=build/pe_csrc -o build/pe_simv -l build/pe_compile.log $(VCS_ARGS)
	./build/pe_simv +NO_HEX -l build/pe_test.log

wave:
	dve -full64 -vpd build/soc.vpd &
