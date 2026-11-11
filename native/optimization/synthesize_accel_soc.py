"""Map the complete current CPU/control/MAC/GELU/Q SoC at baseline I/O limits.

Use the original proven sv2v/Yosys CPU preparation flow and the corresponding
mapped candidate array. New controllers and the quantizer remain part of this
whole-SoC compile. This is a measured prototype, not the final ADP decision.
"""
import argparse,hashlib,importlib.util,json,os,re,resource,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';PROJECT=TASK/'my_design/project'
parser=argparse.ArgumentParser();parser.add_argument('--rows',type=int,default=8);parser.add_argument('--cols',type=int,default=32);parser.add_argument('--lanes',type=int,default=4);parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
identifier='R%d_C%d_L%d'%(args.rows,args.cols,args.lanes)
case=ROOT/'work/soc-synthesis'/identifier;case.mkdir(parents=True,exist_ok=True)
assert not (case/'summary.json').exists(),'existing proof: choose a fresh case/revision'
inputs=case/'inputs';inputs.mkdir(exist_ok=True)
array=ROOT/'work/array-synthesis'/identifier
array_proof=next(r for r in json.loads((ROOT/'work/array-synthesis/summary.json').read_text())if r['id']==identifier)
assert array_proof['status']=='PASS' and 'AFFINE_ARRAY_SYNTHESIS_PASS' in (array/'synthesis.log').read_text()
commands=[]
def limits():resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
def run(argv,label,timeout=7200,output=None):
 print('SOC_SYNTHESIS_PHASE='+label,flush=True);started=time.monotonic()
 with (output or case/(label+'.log')).open('w') as log:
  p=subprocess.Popen(list(map(str,argv)),cwd=case,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=limits)
  try:
   while True:
    try:code=p.wait(timeout=30);break
    except subprocess.TimeoutExpired:
     print('SOC_SYNTHESIS_RUNNING '+label,flush=True)
     if time.monotonic()-started>timeout:raise TimeoutError(label)
  finally:
   if p.poll()is None:
    try:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=10)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=10)
 commands.append({'label':label,'argv':list(map(str,argv)),'returncode':code,'elapsed_s':time.monotonic()-started})
 if code:raise RuntimeError(str(case/(label+'.log')))

run([sys.executable,ROOT/'prepare_accel_soc.py'],'fixture',300)
source_top=ROOT/'work/accel-soc/soc_accel_top.sv'
(inputs/'soc_accel_top.sv').write_bytes(source_top.read_bytes())
rtl_names=['axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','axi_dma_arbiter.sv','axi_accel_mmio.sv','gelu_lut_stream.sv','axi_gelu_engine.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','axi_accel_backend.sv']
for name in rtl_names:(inputs/name).write_bytes((ROOT/'rtl'/name).read_bytes())
backend=(inputs/'axi_accel_backend.sv').read_text()
stub=re.search(r'module axi_accel_backend[\s\S]*?\n\);',backend)[0]
(inputs/'backend_stub.sv').write_text('(* blackbox *)\n'+stub+'\nendmodule\n')
# Freeze parameters in the separately elaborated backend, and bind exactly the
# mapped array selected above. No old 16x16 accelerator is retained.
backend=backend.replace('parameter ROWS=8,COLS=32,LANES=4,ID_WIDTH=3','parameter ROWS=%d,COLS=%d,LANES=%d,ID_WIDTH=3'%(args.rows,args.cols,args.lanes))
ref='affine_array_ROWS%d_COLS%d_LANES%d'%(args.rows,args.cols,args.lanes)
array_instance='affine_array #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES)) u_array('
assert backend.count(array_instance)==1
backend=backend.replace(array_instance,ref+' u_array(')
(inputs/'backend_mapped_array.sv').write_text(backend)
original=TASK/'work/soc_read.dbjzuval/prepared'
conversion=json.loads((original/'conversion.json').read_text());command=conversion['commands'][0]['command']
kept=[]
for item in command:
 if item.startswith('/') and '/project/rtl/' in item:continue
 if item.endswith('/project/synth/sram_blackbox.v') or item=='-DPE_REFERENCE_MACRO':continue
 kept.append(item)
kept += [PROJECT/'rtl/axi_pe_router.v',inputs/'axi_accel_mmio.sv',inputs/'axi_dma_arbiter.sv',inputs/'backend_stub.sv',inputs/'soc_accel_top.sv']
run(kept,'sv2v',300,case/'soc_sv2v.raw.v')
spec=importlib.util.spec_from_file_location('literal_folds',PROJECT/'scripts/fold_verilog_literal_slices.py');fold=importlib.util.module_from_spec(spec);spec.loader.exec_module(fold)
folded,edits=fold.fold_literal_slices((case/'soc_sv2v.raw.v').read_text());assert fold.fold_literal_slices(folded)==(folded,[])
(case/'soc_sv2v.v').write_text(folded)
(case/'literal_folds.json').write_text(json.dumps(edits,indent=2)+'\n')
script='''read_verilog "%s"
hierarchy -check -top soc_accel_top
blackbox *axi_accel_backend
proc
flatten
opt
memory
opt
techmap
opt
hierarchy -check -top soc_accel_top
check -assert
write_verilog -noattr "%s"
'''%(case/'soc_sv2v.v',case/'soc_control_dc.raw.v')
(case/'prepare.ys').write_text(script)
run(['/home/zhz/miniforge3/envs/rtl-yosys/bin/yosys','-s',case/'prepare.ys'],'yosys',600)
control=(case/'soc_control_dc.raw.v').read_text()
pattern=r'(?m)^[ \t]*(?:\\\S*axi_accel_backend\S*|axi_accel_backend)(?:[ \t]*#\([\s\S]*?\))?[ \t]+u_backend[ \t]*\('
matches=list(re.finditer(pattern,control));assert len(matches)==1
match=matches[0]
parameters=re.findall(r'\.(\w+)\(([^)]*)\)',match[0])
assert parameters in [[],[('ID_WIDTH',"32'sd3")]],parameters
control=control[:match.start()]+'  axi_accel_backend u_backend ('+control[match.end():]
(case/'soc_control_dc.v').write_text(control)
frozen=[*inputs.iterdir(),case/'soc_control_dc.v',PROJECT/'synth/soc_io.sdc',array/'mapped.ddc',PROJECT/'build/dc_libraries/standard.db',PROJECT/'build/dc_libraries/sram.db',original/'conversion.json',original/'frozen_hashes.json']
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen if p.is_file()}
preparation={'status':'PREPARED_NOT_MAPPED','id':identifier,'rows':args.rows,'columns':args.cols,'lanes':args.lanes,'top':'soc_accel_top','inputs_sha256':hashes,'commands':commands}
(case/'preparation.json').write_text(json.dumps(preparation,indent=2)+'\n')
if args.prepare_only:raise SystemExit(0)
std=PROJECT/'build/dc_libraries/standard.db';sram=PROJECT/'build/dc_libraries/sram.db'
analyze=[inputs/n for n in rtl_names if n not in ['axi_accel_backend.sv','axi_dma_arbiter.sv','axi_accel_mmio.sv']]+[inputs/'backend_mapped_array.sv']
tcl='''set_app_var target_library [list {%s}]
set_app_var link_library [list * {%s} {%s}]
read_db {%s}
read_db {%s}
set imported [read_ddc {%s}]
foreach name $imported {
 current_design $name
 reset_design
 set_local_link_library [list * {%s} {%s}]
 set_dont_touch [current_design] true
}
define_design_lib WORK -path {%s}
if {![analyze -format sverilog -library WORK -define {SYNTHESIS PE_REFERENCE_MACRO} [list %s]]} {error "backend analysis failed"}
if {![elaborate axi_accel_backend -library WORK]} {error "backend elaboration failed"}
if {![link]} {error "backend link failed"}
set control_designs [read_verilog -rtl {%s}]
if {[lsearch -exact $control_designs soc_accel_top] < 0} {error "whole SoC top was not imported"}
current_design soc_accel_top
if {[get_object_name [current_design]] ne "soc_accel_top"} {error "wrong current design"}
if {![link]} {error "SoC link failed"}
if {[sizeof_collection [get_cells -quiet u_backend]] != 1} {error "missing exact backend instance"}
if {[sizeof_collection [get_cells -filter "is_hierarchical == false"]] < 10000} {error "CPU/control logic missing from SoC"}
if {[sizeof_collection [get_ports -quiet {ifu_axi_araddr[31] mem_axi_rdata[63]}]] != 2} {error "wrong SoC boundary"}
source {%s}
set_fix_hold [get_clocks core_clk]
if {![compile]} {error "SoC compile failed"}
if {[get_object_name [current_design]] ne "soc_accel_top"} {error "compile changed current top"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped SoC leaves"}
if {[sizeof_collection [get_cells -hier -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != %d} {error "SoC SRAM count"}
redirect -file {%s} {report_area}
redirect -file {%s} {report_timing -delay_type max -max_paths 10 -significant_digits 6}
redirect -file {%s} {report_timing -delay_type min -max_paths 10 -significant_digits 6}
redirect -file {%s} {report_constraint -all_violators -significant_digits 6}
redirect -file {%s} {check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}}
redirect -file {%s} {check_design}
redirect -file {%s} {report_units}
write -format ddc -hierarchy -output {%s}
write -format verilog -hierarchy -output {%s}
write_sdc {%s}
puts ACCEL_SOC_SYNTHESIS_MAPPED
exit
'''%(std,std,sram,std,sram,array/'mapped.ddc',std,sram,case/'work',' '.join('{'+str(p)+'}' for p in analyze),case/'soc_control_dc.v',PROJECT/'synth/soc_io.sdc',args.rows*args.cols,
 case/'area.rpt',case/'setup.rpt',case/'hold.rpt',case/'violations.rpt',case/'timing_check.rpt',case/'check_design.rpt',case/'units.rpt',case/'mapped.ddc',case/'mapped.v',case/'constraints.sdc')
(case/'run.tcl').write_text(tcl);run(['dc_shell','-f',case/'run.tcl'],'synthesis')
log=(case/'synthesis.log').read_text(errors='replace');assert 'ACCEL_SOC_SYNTHESIS_MAPPED' in log
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want,p
def slack(path):return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',path.read_text())[1])
setup,hold=slack(case/'setup.rpt'),slack(case/'hold.rpt')
summary={**preparation,'status':'MAPPED_DC_TIMING_PASS' if min(setup,hold)>=0 else 'MAPPED_DC_TIMING_FAIL',
 'area':float(re.search(r'Total cell area:\s+([\d.]+)',(case/'area.rpt').read_text())[1]),'setup_slack_ns':setup,'hold_slack_ns':hold,'clock_period_ns':10,
 'SRAMs':args.rows*args.cols,'scope':'complete current CPU/router/DMA/MAC/GELU/quantizer SoC; FP/vector extensions and full-RV32 inference/ADP selection pending',
 'commands':commands,'formal_equivalence':False,'SDF':False,'signoff_STA':False}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(summary['status'],flush=True)
