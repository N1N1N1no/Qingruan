import json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parents[1]
PROJECT=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
work=ROOT/'work/cell-synthesis';work.mkdir(parents=True,exist_ok=True)
results=[]
for name,lanes in [('legacy',4),('affine4',4),('affine8',8),('affine16',16)]:
    case=work/name;case.mkdir(exist_ok=True)
    sources=[PROJECT/'rtl/rfsp16x8.v']
    module='affine_pe_cell'
    if name=='legacy':
        sources+=[PROJECT/'rtl/Exact_8bit.v',PROJECT/'rtl/adder.v',PROJECT/'rtl/pe_1sram_4mul.v'];module='pe_1sram_4mul'
    else:sources+=[ROOT/'rtl/affine_pe_cell.v']
    script='''set_app_var target_library [list {%s}]
set_app_var link_library [list * {%s} {%s}]
read_db {%s}
read_db {%s}
define_design_lib WORK -path {%s}
if {![analyze -library WORK -format verilog -define {SYNTHESIS PE_REFERENCE_MACRO} [list %s]]} {error "analysis failed"}
if {![elaborate %s -library WORK %s]} {error "elaboration failed"}
set root [get_object_name [get_designs %s*]]
current_design [lindex $root 0]
if {![link]} {error "link failed"}
create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
set_output_delay 1 -clock clk [all_outputs]
set_max_transition 0.5 [current_design]
if {![compile]} {error "compile failed"}
if {[sizeof_collection [get_cells -hier -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != 1} {error "SRAM missing"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped leaves"}
redirect -file {%s} {report_area}
redirect -file {%s} {report_timing -max_paths 3}
redirect -file {%s} {report_constraint -all_violators}
write -format verilog -hierarchy -output {%s}
write -format ddc -hierarchy -output {%s}
puts CELL_CANDIDATE_PASS
exit
'''%((PROJECT/'build/dc_libraries/standard.db',)*2+(PROJECT/'build/dc_libraries/sram.db',PROJECT/'build/dc_libraries/standard.db',PROJECT/'build/dc_libraries/sram.db',case/'work',
        ' '.join('{'+str(p)+'}' for p in sources),module,'' if name=='legacy' else '-parameters LANES='+str(lanes),module,
        case/'area.rpt',case/'timing.rpt',case/'violations.rpt',case/'mapped.v',case/'mapped.ddc'))
    (case/'run.tcl').write_text(script)
    print('MEASURE_CELL='+name,flush=True);start=time.monotonic()
    with (case/'run.log').open('w') as log:
        p=subprocess.run(['dc_shell','-f',str(case/'run.tcl')],cwd=case,stdout=log,stderr=subprocess.STDOUT,timeout=900)
    text=(case/'run.log').read_text(errors='replace')
    if p.returncode or 'CELL_CANDIDATE_PASS' not in text:raise RuntimeError('Cell synthesis failed: '+str(case))
    results.append({'name':name,'lanes':lanes,'status':'PASS','elapsed_s':time.monotonic()-start,'scope':'one PE cell; full SoC area/timing not measured'})
    (work/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
print('CELL_SWEEP_PASS',flush=True)
