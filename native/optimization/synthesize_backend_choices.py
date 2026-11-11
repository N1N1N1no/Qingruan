"""Same-boundary phase1 mapping of complete accelerator backend alternatives.

The already mapped array is preserved; new DMA/control/FP modules are all
inside the measured backend. CPU/MMIO/router and full inference are phase2.
"""
import argparse,hashlib,json,os,re,resource,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
PROJECT=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
parser=argparse.ArgumentParser();parser.add_argument('--rows',type=int,default=8);parser.add_argument('--cols',type=int,default=16);parser.add_argument('--lanes',type=int,default=4);parser.add_argument('--resume',action='store_true');parser.add_argument('--mode',choices=['both','private','shared'],default='both');args=parser.parse_args()
identifier='R%d_C%d_L%d'%(args.rows,args.cols,args.lanes)
array=ROOT/'work/array-synthesis'/identifier
record=next(x for x in json.loads((ROOT/'work/array-synthesis/summary.json').read_text())if x['id']==identifier);assert record['status']=='PASS'
std=PROJECT/'build/dc_libraries/standard.db';sram=PROJECT/'build/dc_libraries/sram.db';hf=ROOT/'vendor/HardFloat-1/source'
files=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
 'axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','gelu_lut_stream.sv','axi_gelu_engine.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv',
 'fp32_exp.sv','fp32_softmax_row.sv','axi_softmax_engine.sv','fp32_layernorm.sv','axi_layernorm_engine.sv','fp32_vector.sv','axi_vector_engine.sv','axi_reorder_engine.sv']]
parent=ROOT/'work/backend-synthesis'/identifier;parent.mkdir(parents=True,exist_ok=True);results=[]
for shared in([False,True]if args.mode=='both'else[args.mode=='shared']):
 mode='shared'if shared else'private';case=parent/mode;case.mkdir(exist_ok=True)
 backend=ROOT/'rtl'/('axi_accel_shared_backend.sv'if shared else'axi_accel_complete_backend.sv')
 originals=files+[backend,array/'mapped.ddc',std,sram]
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in originals}
 if args.resume and(case/'summary.json').exists():
  proof=json.loads((case/'summary.json').read_text());assert proof['source_sha256']==hashes;results.append(proof);print('BACKEND_SYNTHESIS_SKIP '+identifier+' '+mode,flush=True);continue
 assert not(case/'summary.json').exists(),'preserve existing proof; use --resume'
 snapshot=case/'inputs';snapshot.mkdir(exist_ok=True);frozen=[]
 for i,p in enumerate(files):
  dest=snapshot/('%02d_'%i+p.name);dest.write_bytes(p.read_bytes());frozen.append(dest)
 text=backend.read_text().replace('parameter ROWS=8,COLS=32,LANES=4,ID_WIDTH=3','parameter ROWS=%d,COLS=%d,LANES=%d,ID_WIDTH=3'%(args.rows,args.cols,args.lanes))
 needle='affine_array #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES)) u_array(';assert text.count(needle)==1
 text=text.replace(needle,'affine_array_ROWS%d_COLS%d_LANES%d u_array('%(args.rows,args.cols,args.lanes))
 top=snapshot/'backend.sv';top.write_text(text);frozen.append(top)
 script='''set_app_var search_path [concat [list {HF_RISCV} {HF}] $search_path]
set_app_var target_library [list {STD}]
set_app_var link_library [list * {STD} {SRAM}]
read_db {STD}
read_db {SRAM}
set imported [read_ddc {ARRAY}]
foreach name $imported {
 current_design $name
 reset_design
 set_local_link_library [list * {STD} {SRAM}]
 set_dont_touch [current_design] true
}
define_design_lib WORK -path {WORK}
if {![analyze -format sverilog -library WORK -define {SYNTHESIS PE_REFERENCE_MACRO} [list FILES]]} {error "backend analysis failed"}
if {![elaborate axi_accel_backend -library WORK]} {error "backend elaboration failed"}
current_design axi_accel_backend
if {[get_object_name [current_design]] ne "axi_accel_backend" || ![link]} {error "wrong or unlinked backend"}
set fp_count [sizeof_collection [get_cells -hier -filter "is_hierarchical == true && ref_name =~ fp32_alu*"]]
if {$fp_count != FP_COUNT} {error "wrong number of physical FP32 units before mapping"}
create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_output_delay 1 -clock clk [all_outputs]
set_max_transition 0.5 [current_design]
if {![compile]} {error "backend compile failed"}
if {[get_object_name [current_design]] ne "axi_accel_backend"} {error "wrong mapped backend top"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped backend leaves"}
if {[sizeof_collection [get_cells -hier -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != SRAM_COUNT} {error "backend SRAM count changed"}
redirect -file {AREA} {report_area}
redirect -file {TIMING} {report_timing -max_paths 10 -significant_digits 9}
redirect -file {HOLD} {report_timing -delay_type min -max_paths 5 -significant_digits 9}
redirect -file {CHECK} {check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}}
redirect -file {VIOLATIONS} {report_constraint -all_violators -significant_digits 9}
redirect -file {RESOURCES} {report_hierarchy}
write -format ddc -hierarchy -output {DDC}
write -format verilog -hierarchy -output {VERILOG}
write_sdc {SDC}
puts "BACKEND_SYNTHESIS_MAPPED fp_before_mapping=$fp_count"
'''
 values={'HF_RISCV':hf/'RISCV','HF':hf,'STD':std,'SRAM':sram,'ARRAY':array/'mapped.ddc','WORK':case/'work','AREA':case/'area.rpt','TIMING':case/'timing.rpt','HOLD':case/'hold.rpt','CHECK':case/'timing_check.rpt','VIOLATIONS':case/'violations.rpt','RESOURCES':case/'hierarchy.rpt','DDC':case/'mapped.ddc','VERILOG':case/'mapped.v','SDC':case/'constraints.sdc'}
 for k,v in values.items():script=script.replace('{'+k+'}','{'+str(v)+'}')
 script=script.replace('FILES',' '.join('{'+str(p)+'}'for p in frozen)).replace('FP_COUNT',str(1 if shared else 3)).replace('SRAM_COUNT',str(args.rows*args.cols))
 script='if {[catch {\n'+script+'\n} backend_error]} {puts stderr "BACKEND_SYNTHESIS_FAILED: $backend_error"; exit 1}\nexit 0\n'
 (case/'run.tcl').write_text(script)
 (case/'inputs-manifest.json').write_text(json.dumps({'source_sha256':hashes,'frozen_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in frozen}},indent=2)+'\n')
 def limits():resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
 begin=time.monotonic();print('BACKEND_SYNTHESIS_PHASE '+identifier+' '+mode,flush=True)
 with(case/'run.log').open('w')as log:
  process=subprocess.Popen(['dc_shell','-f',str(case/'run.tcl')],cwd=case,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=limits)
  try:
   while True:
    try:code=process.wait(timeout=30);break
    except subprocess.TimeoutExpired:
     print('BACKEND_SYNTHESIS_RUNNING '+identifier+' '+mode,flush=True)
     if time.monotonic()-begin>7200:raise TimeoutError(mode)
  finally:
   if process.poll()is None:
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)
 log=(case/'run.log').read_text(errors='replace');assert code==0 and'BACKEND_SYNTHESIS_MAPPED fp_before_mapping='in log and not re.search(r'^Error:',log,re.M),str(case/'run.log')
 for n in['mapped.ddc','mapped.v','area.rpt','timing.rpt','constraints.sdc']:assert(case/n).is_file()and(case/n).stat().st_size>0
 setup=float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',(case/'timing.rpt').read_text())[1]);hold=float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',(case/'hold.rpt').read_text())[1])
 proof={'status':'MAPPED_UNIT_SETUP_PASS'if setup>=0 else'MAPPED_UNIT_SETUP_FAIL','id':identifier,'shared_FP32':shared,'FP32_instances_before_mapping':1 if shared else 3,
  'SRAMs':args.rows*args.cols,'area':float(re.search(r'Total cell area:\s+([\d.]+)',(case/'area.rpt').read_text())[1]),'setup_slack_ns':setup,'hold_slack_ns':hold,'clock_period_ns':10,'input_output_delay_ns':1,
  'elapsed_s':time.monotonic()-begin,'source_sha256':hashes,'artifact_sha256':{n:hashlib.sha256((case/n).read_bytes()).hexdigest()for n in['mapped.ddc','mapped.v','constraints.sdc']},
  'scope':'complete accelerator backend with one mapped array, all8 engines and selected FP32 sharing, identical module I/O conditions; CPU/MMIO/router/whole-inference/SoC STA and ADP are excluded'}
 (case/'summary.json').write_text(json.dumps(proof,indent=2)+'\n');results.append(proof)
 (parent/'summary.json').write_text(json.dumps({'status':'RUNNING','completed':results},indent=2)+'\n')
(parent/'summary.json').write_text(json.dumps({'status':'COMPLETE_MODULE_MAPPING_NOT_SOC_SELECTION'if args.mode=='both'else'REQUESTED_MODE_MAPPED_COMPARISON_PENDING','requested_mode':args.mode,'completed':results},indent=2)+'\n');print('BACKEND_RESOURCE_COMPARISON_MAPPED '+identifier,flush=True)
