"""Repair identified negative cell IOPATHs by physical output loading.

Insert a noninverting mapped buffer after each affected NOR2_X2 output in a
fresh DDC. Keep the common SDC and original RTL; never clamp SDF values.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v2')
parser.add_argument('--buffer', choices=['BUF_X2', 'BUF_X4'], default='BUF_X2')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
source = ROOT / 'work/full-soc-timing/R8_C16_L4/shared/v1'
gate = ROOT / 'work/full-soc-gate/R8_C16_L4/shared/v3'
case = source.parent / args.revision
assert not case.exists(), 'Preserve previous revision'


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify(hashes):
    for name, wanted in hashes.items():
        assert sha(local(name)) == wanted, name


proof = json.loads((source / 'summary.json').read_text())
assert proof['status'] == 'FULL_ALL8_SOC_MAPPED_TIMING_PASS'
for name, wanted in proof['artifact_sha256'].items():
    assert sha(source / name) == wanted, name
diagnostic = json.loads((gate / 'annotation-diagnostic.json').read_text())
verify(diagnostic['source_sha256'])
gate_preparation = json.loads((gate / 'preparation.json').read_text())
assert gate_preparation['sdf_adaptation']['source_sha256'] == sha(source / 'mapped.sdf')
assert gate_preparation['sdf_adaptation']['netlist_sha256'] == sha(source / 'mapped.v')
aliases = sorted(gate_preparation['verified_zero_cell_alias_modules'])
assert aliases and all(re.fullmatch(r'reverse_width\d+(?:_\d+)?', name) for name in aliases)
classification_path = ROOT / 'work/ddc-leaf-classification-v1/summary.json'
classification = json.loads(classification_path.read_text())
assert classification['status'] == 'READONLY_CLASSIFICATION_DIAGNOSTIC_COMPLETE'
assert classification['source_ddc_sha256'] == sha(source / 'mapped.ddc')
constant_counts = {'**logic_0**': 2965, '**logic_1**': 601}
assert set(classification['differences']) == set(constant_counts)
for name, count in constant_counts.items():
    entry = classification['differences'][name]
    assert entry == {'DC': count, 'mapped_SDF': 0, 'library_cells': '', 'mapped_design': ''}
physical_counts = gate_preparation['sdf_adaptation']['physical_leaf_celltypes']
assert sum(physical_counts.values()) == classification['physical_SDF_total']
arcs = diagnostic['affected_arcs']
assert len(arcs) == 24 and all(arc['celltype'] == 'NOR2_X2' for arc in arcs)
instances = sorted({arc['instance_sdf'] for arc in arcs})
assert len(instances) == 24
assert all(re.fullmatch(r'u_backend/u_softmax/core/U\d+', name) for name in instances)
std, sram = [PROJECT / 'build/dc_libraries' / name for name in ['standard.db', 'sram.db']]
inputs = [Path(__file__), source / 'summary.json', source / 'mapped.ddc',
          source / 'mapped.v', source / 'mapped.sdf', source / 'constraints.sdc',
          gate / 'annotation-diagnostic.json', gate / 'preparation.json',
          classification_path, std, sram]
hashes = {str(path): sha(path) for path in inputs}
case.mkdir(parents=True)
(case / 'targets.json').write_text(json.dumps({'celltype': 'NOR2_X2', 'output_pin': 'ZN',
    'instances': instances, 'inserted_cell': args.buffer}, indent=2) + '\n')
tcl = r'''set_app_var target_library [list {STD}]
set_app_var link_library [list * {STD} {SRAM}]
read_db {STD}
read_db {SRAM}
read_ddc {SOURCE}
current_design soc_accel_top
if {[get_object_name [current_design]] ne "soc_accel_top" || ![link]} {error "wrong or unlinked SoC"}
if {[sizeof_collection [all_clocks]] != 1 || [get_attribute [get_clocks core_clk] period] != 10} {error "wrong clock"}
array set expected [list PHYSICAL_COUNTS CONSTANT_COUNTS]
proc verify_leaf_histogram {expected_array stage} {
 upvar $expected_array expected
 array set actual {}
 foreach ref [get_attribute [get_cells -hier -filter "is_hierarchical == false"] ref_name] {incr actual($ref)}
 if {[lsort [array names actual]] ne [lsort [array names expected]]} {error "$stage unexpected leaf reference types"}
 foreach ref [array names expected] {
  if {$actual($ref) != $expected($ref)} {error "$stage $ref count differs: actual=$actual($ref) expected=$expected($ref)"}
 }
 foreach ref [list {**logic_0**} {**logic_1**}] {
  if {[sizeof_collection [get_lib_cells -quiet */$ref]] != 0 || [sizeof_collection [get_designs -quiet $ref]] != 0} {error "constant unexpectedly binds to a physical library/design"}
 }
 puts "SDF_REPAIR_EXACT_LEAF_HISTOGRAM_PASS stage=$stage physical_expected=SOURCE_LEAVES constant0=$actual(**logic_0**) constant1=$actual(**logic_1**)"
 return $actual(SRAM_6T_CORE_16x8_MC_TB)
}
if {[verify_leaf_histogram expected source] != 128} {error "source SRAM count differs"}
set buffer [get_lib_cells NangateOpenCellLibrary/BUFFER_CELL]
if {[sizeof_collection $buffer] != 1} {error "missing selected noninverting buffer"}
set stream [open {EDITS} w]
puts $stream "source_cell\toutput_pin\tinserted_buffer"
set inserted 0
foreach target [list TARGETS] {
 current_instance
 set cell [get_cells $target]
 if {[sizeof_collection $cell] != 1 || [get_attribute $cell ref_name] ne "NOR2_X2"} {error "wrong target $target"}
 set pin [get_pins ${target}/ZN]
 if {[sizeof_collection $pin] != 1} {error "missing output pin"}
 set scope [file dirname $target]
 current_instance $scope
 set_dont_touch [current_design] false
 set_dont_touch $cell false
 set name [format SDF_LOAD_%05d $inserted]
 redirect -variable insertion_log {set added [insert_buffer -new_cell_names $name -new_net_names ${name}_net $pin $buffer]}
 if {[sizeof_collection $added] != 1} {error "output buffer insertion failed: $insertion_log"}
 puts $stream "$target\tZN\t[get_object_name $added]"
 incr inserted
}
close $stream
current_instance
current_design soc_accel_top
if {$inserted != 24} {error "wrong inserted count"}
incr expected(BUFFER_CELL) 24
if {[verify_leaf_histogram expected repaired] != 128} {error "repaired SRAM count differs"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped leaf"}
redirect -file {AREA} {report_area}
redirect -file {SETUP} {report_timing -delay_type max -max_paths 20 -significant_digits 9}
redirect -file {HOLD} {report_timing -delay_type min -max_paths 20 -significant_digits 9}
redirect -file {VIOLATIONS} {report_constraint -all_violators -significant_digits 9}
redirect -file {TIMING_CHECK} {check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}}
redirect -file {DESIGN_CHECK} {check_design}
redirect -file {HIERARCHY} {report_hierarchy}
redirect -file {UNITS} {report_units}
write -format ddc -hierarchy -output {DDC}
write -format verilog -hierarchy -output {VERILOG}
write_sdc {SDC}
write_sdf -version 2.1 -significant_digits 9 {SDF}
puts "FULL_SOC_SDF_LOAD_REPAIR_MAPPED inserted=$inserted"
'''
paths = dict(STD=std, SRAM=sram, SOURCE=source / 'mapped.ddc', EDITS=case / 'output-buffer-edits.tsv',
             AREA=case / 'area.rpt', SETUP=case / 'setup.rpt', HOLD=case / 'hold.rpt',
             VIOLATIONS=case / 'violations.rpt', TIMING_CHECK=case / 'timing_check.rpt',
             DESIGN_CHECK=case / 'check_design.rpt', HIERARCHY=case / 'hierarchy.rpt',
             UNITS=case / 'units.rpt', DDC=case / 'mapped.ddc', VERILOG=case / 'mapped.v',
             SDC=case / 'constraints.sdc', SDF=case / 'mapped.sdf')
for key, value in paths.items():
    tcl = tcl.replace('{' + key + '}', '{' + str(value) + '}')
tcl = tcl.replace('SOURCE_LEAVES', str(gate_preparation['sdf_adaptation']['leaf_cells']))
tcl = tcl.replace('PHYSICAL_COUNTS', ' '.join('{' + name + '} ' + str(count) for name, count in sorted(physical_counts.items())))
tcl = tcl.replace('CONSTANT_COUNTS', ' '.join('{' + name + '} ' + str(count) for name, count in constant_counts.items()))
tcl = tcl.replace('BUFFER_CELL', args.buffer).replace('TARGETS', ' '.join('{' + name + '}' for name in instances))
tcl = 'if {[catch {\n' + tcl + '\n} repair_error]} {puts stderr "SDF_LOAD_REPAIR_FAILED: $repair_error"; exit 1}\nexit 0\n'
(case / 'run.tcl').write_text(tcl)
hashes[str(case / 'run.tcl')] = sha(case / 'run.tcl')
preparation = {'status': 'SDF_CELL_LOAD_REPAIR_PREPARED_EXECUTION_PENDING',
               'source_case': str(source), 'inputs_sha256': hashes,
               'target_instances': instances, 'buffer': args.buffer,
               'clock_period_ns': 10, 'source_SRAMs': 128,
               'source_physical_leaves': gate_preparation['sdf_adaptation']['leaf_cells'],
               'source_DC_nonphysical_constant_primitives': constant_counts,
               'exact_physical_reference_histogram_checked_before_and_after': True,
               'explicit_zero_cell_alias_definitions': gate_preparation['verified_zero_cell_alias_modules'],
               'scope': 'Mapped output loading only; no RTL/library/clock or reference edits and no SDF clamping. Recheck real STA and selected IOPATH values before gate preparation.'}
(case / 'preparation.json').write_text(json.dumps(preparation, indent=2) + '\n')


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))


started = time.monotonic()
with (case / 'run.log').open('w') as log:
    process = subprocess.Popen(['dc_shell', '-f', str(case / 'run.tcl')], cwd=case,
                               stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, preexec_fn=limits)
    (case / 'live-process.json').write_text(json.dumps({
        'status': 'RUNNING', 'pid': process.pid, 'phase': 'mapped_output_load_repair'}, indent=2) + '\n')
    try:
        while True:
            try:
                code = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                print('SDF_CELL_LOAD_REPAIR_RUNNING elapsed_s=%d' % (time.monotonic() - started), flush=True)
                if time.monotonic() - started > 1800:
                    raise TimeoutError('mapped load repair')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        (case / 'live-process.json').write_text(json.dumps({
            'status': 'TERMINAL', 'returncode': process.returncode}, indent=2) + '\n')
log = (case / 'run.log').read_text(errors='replace')
verify(hashes)
if code != 0 or 'FULL_SOC_SDF_LOAD_REPAIR_MAPPED inserted=24' not in log or re.search(r'^Error:', log, re.M):
    (case / 'failure.json').write_text(json.dumps({**preparation,
        'status': 'SDF_CELL_LOAD_REPAIR_TOOL_FAILURE', 'returncode': code}, indent=2) + '\n')
    raise RuntimeError(str(case / 'run.log'))
assert 'Thank you...' in log


def normalized_sdc(path):
    commands = [' '.join(line.split()) for line in path.read_text().replace('\\\n', ' ').splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
    # DC reorders these four independent transition attributes on DDC reload.
    # Only this contiguous, complete, value-identical quartet is canonicalized;
    # all other commands retain their exact order (including any overrides).
    expected = {f'set_clock_transition -{corner} -{edge} 0.005 [get_clocks core_clk]'
                for corner in ['min', 'max'] for edge in ['rise', 'fall']}
    indices = [i for i, command in enumerate(commands) if command.startswith('set_clock_transition ')]
    assert len(indices) == 4 and indices == list(range(indices[0], indices[0] + 4))
    assert {commands[i] for i in indices} == expected
    commands[indices[0]:indices[-1] + 1] = sorted(expected)
    return commands


same_constraints = normalized_sdc(source / 'constraints.sdc') == normalized_sdc(case / 'constraints.sdc')
print('SDF_CELL_LOAD_REPAIR_SCAN_IOPATH', flush=True)
value = re.compile(r'\(\s*([-+0-9.eE]+(?::[-+0-9.eE]+){0,2})\s*\)')
iopaths = 0
negative, negative_any = [], 0
celltype = instance = None
with (case / 'mapped.sdf').open() as stream:
    for number, line in enumerate(stream, 1):
        if number % 1000000 == 0:
            print('SDF_REPAIR_SCAN_LINES=%d' % number, flush=True)
        if '(CELLTYPE ' in line:
            celltype = re.search(r'\(CELLTYPE "([^\"]+)"\)', line)[1]
        if '(INSTANCE' in line:
            instance = re.search(r'\(INSTANCE\s*(.*?)\)', line)[1]
        if '(IOPATH ' not in line:
            continue
        iopaths += 1
        values = [item.split(':') for item in value.findall(line)]
        assert values and all(len(item) in [1, 3] for item in values), line
        if any(float(part) < 0 for item in values for part in item):
            negative_any += 1
        if any(float(item[-1]) < 0 for item in values):
            negative.append({'line': number, 'celltype': celltype,
                             'instance': instance, 'iopath': line.strip()})
assert iopaths > 5000000
scan = {'status': 'SELECTED_MAXIMUM_IOPATH_NONNEGATIVE' if not negative else 'NEGATIVE_MAXIMUM_IOPATH_REMAINS',
        'IOPATH_entries_scanned': iopaths, 'negative_MAXIMUM_IOPATH_entries': len(negative),
        'negative_IOPATH_entries_any_triplet_field': negative_any,
        'negative_arcs': negative,
        'scope': 'Complete streaming check of regenerated cell IOPATH numeric fields; negative setup/hold/recrem limits are preserved and are not rejected or clamped.'}
(case / 'iopath-scan.json').write_text(json.dumps(scan, indent=2) + '\n')


def slack(name):
    return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / name).read_text())[1])


setup, hold = slack('setup.rpt'), slack('hold.rpt')
accepted = min(setup, hold) >= 0 and same_constraints and not negative
names = ['mapped.ddc', 'mapped.v', 'mapped.sdf', 'constraints.sdc', 'area.rpt', 'setup.rpt',
         'hold.rpt', 'violations.rpt', 'timing_check.rpt', 'check_design.rpt', 'hierarchy.rpt',
         'units.rpt', 'output-buffer-edits.tsv', 'targets.json', 'iopath-scan.json', 'run.tcl']
for name in names:
    assert (case / name).is_file() and (case / name).stat().st_size > 0, name
result = {**preparation,
          'status': 'FULL_ALL8_SOC_MAPPED_TIMING_PASS' if accepted else 'FULL_SOC_MAPPING_OR_SDF_REPAIR_NOT_ACCEPTED',
          'geometry': 'R8_C16_L4', 'mode': 'shared', 'SRAMs': 128,
          'setup_slack_ns': setup, 'hold_slack_ns': hold,
          'area': float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1]),
          'inserted_output_buffers': 24, 'normalized_SDC_identical_to_source': same_constraints,
          'SDC_comparison_rule': 'Exact ordered commands except canonical order of the four contiguous independent min/max rise/fall core_clk transition=0.005 commands. No other command/value/order changes allowed.',
          'selected_MAXIMUM_IOPATH_nonnegative': not negative, 'iopath_scan': scan,
          'artifact_sha256': {name: sha(case / name) for name in names},
          'elapsed_s': time.monotonic() - started,
          'gate_SDF_functional_simulation_passed': False, 'official_accuracy_accepted': False,
          'final_ADP_accepted': False,
          'scope': 'Exactly24 noninverting output-load buffers in complete all8 SoC, remeasured common-constraint STA and complete selected-IOPATH scan. This is not gate/SDF execution, official model accuracy or final ADP acceptance.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'area', 'setup_slack_ns', 'hold_slack_ns',
      'normalized_SDC_identical_to_source', 'selected_MAXIMUM_IOPATH_nonnegative']}), flush=True)
assert accepted, str(case / 'summary.json')
