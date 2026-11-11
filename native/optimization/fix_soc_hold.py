"""Insert data-path buffers for measured short paths, without relaxing clocks.

Work from a separate copy of the completed SoC DDC. Do not change the original
mapping or library. Recheck setup, hold, SRAM count and functional cell types.
Gate/SDF functional verification is a later, explicit acceptance step.
"""
import hashlib,json,re,resource,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
source=ROOT/'work/soc-synthesis/R8_C32_L4';case=ROOT/'work/soc-hold-fix/R8_C32_L4'
case.mkdir(parents=True,exist_ok=True)
assert not (case/'summary.json').exists()
PROJECT=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
std=PROJECT/'build/dc_libraries/standard.db';sram=PROJECT/'build/dc_libraries/sram.db'
inputs=[source/'mapped.ddc',source/'constraints.sdc',std,sram]
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
script=r'''set_app_var target_library [list {STD}]
set_app_var link_library [list * {STD} {SRAM}]
read_db {STD}
read_db {SRAM}
read_ddc {SOURCE}
current_design soc_accel_top
if {[get_object_name [current_design]] ne "soc_accel_top" || ![link]} {error "wrong or unlinked SoC"}
if {[sizeof_collection [get_clocks core_clk]] != 1 || [get_attribute [get_clocks core_clk] period] != 10} {error "clock changed"}
set buffer [get_lib_cells NangateOpenCellLibrary/BUF_X1]
if {[sizeof_collection $buffer] != 1} {error "missing noninverting buffer"}
set_dont_touch [get_designs -hierarchical *] false
set hierarchical [get_cells -hierarchical -filter "is_hierarchical == true"]
if {[sizeof_collection $hierarchical]} {set_dont_touch $hierarchical false}
set stream [open {EDITS} w]
puts $stream "pass\tendpoint\told_slack_ns\tinserted_cell"
set inserted_total 0
for {set pass 0} {$pass < 8} {incr pass} {
 current_design soc_accel_top
 current_instance
 set paths [get_timing_paths -delay_type min -max_paths 10000 -nworst 1]
 if {[sizeof_collection $paths] == 0} {error "no min timing paths"}
 set endpoints [dict create]
 set pins ""
 foreach_in_collection path $paths {
  set slack [get_attribute $path slack]
  if {$slack < 0.0} {
   set endpoint [get_object_name [get_attribute $path endpoint]]
   if {![dict exists $endpoints $endpoint]} {set pins [add_to_collection $pins [get_attribute $path endpoint]]}
   dict set endpoints $endpoint $slack
  }
 }
 if {[dict size $endpoints] == 0} {break}
 puts "HOLD_FIX_PASS=$pass endpoints=[dict size $endpoints]"
 set last_scope "."
 foreach_in_collection pin $pins {
  set endpoint [get_object_name $pin]
  if {[sizeof_collection $pin] != 1 || ![regexp {/D$} $endpoint]} {error "refuse non-data endpoint $endpoint"}
  set cell [get_cells -of_objects $pin]
  if {[sizeof_collection $cell] != 1 || ![get_attribute $cell is_sequential]} {error "endpoint is not a register data pin"}
  set cell_name [get_object_name $cell]
  set scope [file dirname $cell_name]
  if {$scope ne $last_scope} {
   current_instance
   if {$scope ne "."} {current_instance $scope}
   set last_scope $scope
  }
  set name [format HOLD_FIX_%05d $inserted_total]
  redirect -variable insertion_log {set added [insert_buffer -new_cell_names $name -new_net_names ${name}_net $pin $buffer]}
  if {[sizeof_collection $added] != 1} {error "buffer insertion failed at $endpoint: $insertion_log"}
  puts $stream "$pass\t$endpoint\t[dict get $endpoints $endpoint]\t[get_object_name $added]"
  incr inserted_total
 }
 flush $stream
}
close $stream
current_design soc_accel_top
current_instance
if {[get_object_name [current_design]] ne "soc_accel_top"} {error "wrong final top"}
if {[sizeof_collection [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != 256} {error "SRAM count changed"}
if {[sizeof_collection [get_cells -hierarchical -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped leaves"}
redirect -file {AREA} {report_area}
redirect -file {SETUP} {report_timing -delay_type max -max_paths 20 -significant_digits 9}
redirect -file {HOLD} {report_timing -delay_type min -max_paths 20 -significant_digits 9}
redirect -file {CHECK} {check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}}
write -format ddc -hierarchy -output {DDC}
write -format verilog -hierarchy -output {VERILOG}
write_sdc {SDC}
puts "SOC_HOLD_FIX_MAPPED inserted=$inserted_total"
exit
'''
substitutions={'STD':std,'SRAM':sram,'SOURCE':source/'mapped.ddc','EDITS':case/'edits.tsv','AREA':case/'area.rpt','SETUP':case/'setup.rpt','HOLD':case/'hold.rpt','CHECK':case/'timing_check.rpt','DDC':case/'mapped.ddc','VERILOG':case/'mapped.v','SDC':case/'constraints.sdc'}
for key,value in substitutions.items():script=script.replace('{'+key+'}','{'+str(value)+'}')
(case/'run.tcl').write_text(script)
def limits():resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
start=time.monotonic()
with (case/'run.log').open('w') as log:r=subprocess.run(['dc_shell','-f',str(case/'run.tcl')],cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=1800,preexec_fn=limits)
assert r.returncode==0 and 'SOC_HOLD_FIX_MAPPED' in (case/'run.log').read_text(errors='replace')
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
def slack(name):return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',(case/name).read_text())[1])
setup,hold=slack('setup.rpt'),slack('hold.rpt')
summary={'status':'MAPPED_DC_TIMING_PASS' if min(setup,hold)>=0 else 'MAPPED_DC_TIMING_FAIL','setup_slack_ns':setup,'hold_slack_ns':hold,
 'area':float(re.search(r'Total cell area:\s+([\d.]+)',(case/'area.rpt').read_text())[1]),'inserted_data_buffers':len((case/'edits.tsv').read_text().splitlines())-1,
 'clock_period_ns':10,'SRAMs':256,'inputs_sha256':hashes,'elapsed_s':time.monotonic()-start,
 'scope':'complete prototype with noninverting data-path hold buffers; clocks/constraints unchanged; gate/SDF and signoff STA pending'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
