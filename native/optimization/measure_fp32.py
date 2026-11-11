import argparse,hashlib,json,re,resource,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
parser=argparse.ArgumentParser();parser.add_argument('module',nargs='?',choices=['fp32_alu','quant_lookup8','fp32_softmax_row','axi_softmax_engine','axi_layernorm_engine','fp32_vector_unit','axi_vector_engine','axi_reorder_engine'],default='fp32_alu');parser.add_argument('--case-name');args=parser.parse_args();module=args.module
name=args.case_name or {'fp32_alu':'fp32-synthesis','quant_lookup8':'quant-synthesis','fp32_softmax_row':'softmax-row-synthesis','axi_softmax_engine':'softmax-engine-synthesis','axi_layernorm_engine':'layernorm-engine-synthesis','fp32_vector_unit':'vector-unit-synthesis','axi_vector_engine':'vector-engine-synthesis','axi_reorder_engine':'reorder-engine-synthesis'}[module];assert re.fullmatch(r'[a-zA-Z0-9_-]+',name)
case=ROOT/'work'/name;case.mkdir(parents=True,exist_ok=True)
assert not(case/'summary.json').exists(),'preserve existing evidence: choose a fresh --case-name'
lib=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project/build/dc_libraries/standard.db'
hf=ROOT/'vendor/HardFloat-1/source'
files=json.loads((ROOT/'hardfloat-files.json').read_text())
if module=='quant_lookup8':files=[str(ROOT/'rtl/quant_lookup8.v')]
if module in ['fp32_softmax_row','axi_softmax_engine']:files += [str(ROOT/'rtl/fp32_exp.sv'),str(ROOT/'rtl/fp32_softmax_row.sv')]
if module=='axi_softmax_engine':files += [str(ROOT/'rtl'/p)for p in ['axi_dma_reader.sv','axi_dma_writer.sv','axi_softmax_engine.sv']]
if module=='axi_layernorm_engine':files += [str(ROOT/'rtl'/p)for p in ['fp32_layernorm.sv','axi_dma_reader.sv','axi_dma_writer.sv','axi_layernorm_engine.sv']]
if module=='fp32_vector_unit':files += [str(ROOT/'rtl'/p)for p in ['fp32_vector.sv','fp32_vector_unit.sv']]
if module=='axi_vector_engine':files += [str(ROOT/'rtl'/p)for p in ['fp32_vector.sv','axi_dma_reader.sv','axi_dma_writer.sv','axi_vector_engine.sv']]
if module=='axi_reorder_engine':files=[str(ROOT/'rtl'/p)for p in ['axi_dma_reader.sv','axi_dma_writer.sv','axi_reorder_engine.sv']]
hashes={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files+[str(lib)]}
script='''set_app_var search_path [concat [list {%s} {%s}] $search_path]
set_app_var target_library [list {%s}]
set_app_var link_library [list * {%s}]
read_db {%s}
define_design_lib WORK -path {%s}
if {![analyze -library WORK -format sverilog [list %s]]} {error "FP analysis failed"}
if {![elaborate fp32_alu -library WORK]} {error "FP elaboration failed"}
current_design fp32_alu
if {![link]} {error "FP link failed"}
create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_output_delay 1 -clock clk [all_outputs]
set_max_transition 0.5 [current_design]
if {![compile]} {error "FP compile failed"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped FP leaves"}
redirect -file {%s} {report_area}
redirect -file {%s} {report_timing -max_paths 3 -significant_digits 9}
redirect -file {%s} {report_constraint -all_violators}
write -format verilog -hierarchy -output {%s}
write -format ddc -hierarchy -output {%s}
puts FP32_SYNTHESIS_PASS
exit
'''%(hf/'RISCV',hf,lib,lib,lib,case/'work',' '.join('{'+p+'}' for p in files),case/'area.rpt',case/'timing.rpt',case/'violations.rpt',case/'mapped.v',case/'mapped.ddc')
if module!='fp32_alu':script=script.replace('elaborate fp32_alu','elaborate '+module).replace('current_design fp32_alu','current_design '+module)
script=script.replace('if {![link]}', 'if {[get_object_name [current_design]] ne "'+module+'"} {error "wrong unit top"}\nif {![link]}')
# DC's interactive Tcl continues after a bare error. Catch the entire flow so
# a failed analyze/elaborate cannot fall through to a misleading PASS marker.
script='if {[catch {\n'+script.replace('puts FP32_SYNTHESIS_PASS\nexit\n','puts FP32_SYNTHESIS_PASS\n')+'\n} unit_error]} {puts stderr "UNIT_SYNTHESIS_FAILED: $unit_error"; exit 1}\nexit 0\n'
(case/'run.tcl').write_text(script)
def limit_memory():
 cap=(8 if module in ['fp32_softmax_row','axi_softmax_engine','axi_layernorm_engine','axi_vector_engine'] else 2)*1024**3
 resource.setrlimit(resource.RLIMIT_AS,(cap,cap))
with (case/'run.log').open('w') as f:r=subprocess.run(['dc_shell','-f',str(case/'run.tcl')],cwd=case,stdout=f,stderr=subprocess.STDOUT,timeout=1200,preexec_fn=limit_memory)
log=(case/'run.log').read_text(errors='replace')
assert not r.returncode and 'FP32_SYNTHESIS_PASS' in log and not re.search(r'^Error:',log,re.M),str(case/'run.log')
assert all((case/n).is_file()and(case/n).stat().st_size>0 for n in['mapped.ddc','mapped.v','area.rpt','timing.rpt'])
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
setup=float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',(case/'timing.rpt').read_text())[1])
(case/'summary.json').write_text(json.dumps({'status':'PASS'if setup>=0 else'MAPPED_UNIT_SETUP_FAIL','module':module,'clock_period_ns':10,'input_output_delay_ns':1,
 'area':float(re.search(r'Total cell area:\s+([\d.]+)',(case/'area.rpt').read_text())[1]),
 'setup_slack_ns':setup,
 'source_sha256':hashes,'scope':'standalone mapped unit, not complete SoC timing or area; hold/signoff/SoC integration pending'},indent=2)+'\n')
print('FP32_SYNTHESIS_PASS',flush=True)
