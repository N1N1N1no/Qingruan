"""Close the measured complete all8 SoC setup/hold gaps at unchanged constraints.

Work on a new DDC copy. Permit mapped controller/FP cell optimization, preserve
the array arithmetic mapping during setup, and buffer remaining short D paths.
No RTL, clock relaxation, reference, or running simulation input changes.
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
parser.add_argument('--geometry', choices=['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4'], default='R8_C16_L4')
parser.add_argument('--mode', choices=['shared', 'private'], default='shared')
parser.add_argument('--source-revision', default='v1')
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
for value in [args.source_revision, args.revision]:
    assert re.fullmatch(r'[A-Za-z0-9_-]+', value)
rows, columns, lanes = map(int, re.fullmatch(r'R(\d+)_C(\d+)_L(\d+)', args.geometry).groups())
source = ROOT / 'work/full-soc-synthesis' / args.geometry / args.mode / args.source_revision
case = ROOT / 'work/full-soc-timing' / args.geometry / args.mode / args.revision
assert not case.exists(), 'Preserve prior attempt; use a new revision'
proof = json.loads((source / 'summary.json').read_text())
assert proof['status'] in ['FULL_ALL8_SOC_MAPPED_TIMING_PASS', 'FULL_ALL8_SOC_MAPPED_TIMING_FAIL']
assert proof['clock_period_ns'] == 10 and proof['SRAMs'] == rows * columns


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(hashes):
    for name, wanted in hashes.items():
        assert sha(local(name)) == wanted, name


verify(proof['inputs_sha256'])
for name, wanted in proof['artifact_sha256'].items():
    assert sha(source / name) == wanted, name
std, sram = [PROJECT / 'build/dc_libraries' / name for name in ['standard.db', 'sram.db']]
original_sdc = PROJECT / 'synth/soc_io.sdc'
inputs = [Path(__file__), source / 'summary.json', source / 'mapped.ddc',
          source / 'mapped.v', source / 'constraints.sdc', std, sram, original_sdc]
hashes = {str(path): sha(path) for path in inputs}
case.mkdir(parents=True)
tcl = r'''set_app_var target_library [list {STD}]
set_app_var link_library [list * {STD} {SRAM}]
read_db {STD}
read_db {SRAM}
set imported [read_ddc {SOURCE}]
foreach name $imported {
 current_design $name
 set_dont_touch [current_design] false
}
current_design soc_accel_top
if {[get_object_name [current_design]] ne "soc_accel_top" || ![link]} {error "wrong or unlinked SoC"}
reset_design
source {ORIGINAL_SDC}
set hierarchy [get_cells -hierarchical -filter "is_hierarchical == true"]
if {[sizeof_collection $hierarchy]} {set_dont_touch $hierarchy false}
set array [get_cells -quiet u_backend/u_array]
if {[sizeof_collection $array] != 1} {error "wrong number of physical arrays"}
set_dont_touch $array true
if {[sizeof_collection [all_clocks]] != 1 || [get_attribute [get_clocks core_clk] period] != 10} {error "clock constraint changed"}
set_fix_hold [get_clocks core_clk]
if {![compile -incremental_mapping]} {error "incremental SoC mapping failed"}
current_design soc_accel_top
redirect -file {INTERIM_SETUP} {report_timing -delay_type max -max_paths 10 -significant_digits 9}
redirect -file {INTERIM_HOLD} {report_timing -delay_type min -max_paths 10 -significant_digits 9}
write -format ddc -hierarchy -output {INTERIM_DDC}
puts FULL_SOC_INCREMENTAL_MAPPING_COMPLETE

# Buffer only genuine register D endpoints of measured negative min paths.
# Insertion does not alter arithmetic, state transitions, or the clock tree.
# If an array D path needs a buffer, permit this local insertion after setup;
# no further array arithmetic optimization is performed.
set_dont_touch $array false
set buffer [get_lib_cells NangateOpenCellLibrary/BUF_X1]
if {[sizeof_collection $buffer] != 1} {error "missing noninverting buffer"}
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
 puts "FULL_SOC_HOLD_FIX_PASS=$pass endpoints=[dict size $endpoints]"
 set last_scope "."
 foreach_in_collection pin $pins {
  set endpoint [get_object_name $pin]
  if {[sizeof_collection $pin] != 1 || ![regexp {/D$} $endpoint]} {error "refuse non-D endpoint $endpoint"}
  set cell [get_cells -of_objects $pin]
  if {[sizeof_collection $cell] != 1 || ![get_attribute $cell is_sequential]} {error "endpoint not sequential D"}
  set scope [file dirname [get_object_name $cell]]
  if {$scope ne $last_scope} {
   current_instance
   if {$scope ne "."} {current_instance $scope}
   set last_scope $scope
  }
  set name [format FULL_SOC_HOLD_%05d $inserted_total]
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
if {[sizeof_collection [all_clocks]] != 1 || [get_attribute [get_clocks core_clk] period] != 10} {error "final clock changed"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped leaves"}
if {[sizeof_collection [get_cells -hier -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != SRAM_COUNT} {error "SRAM count changed"}
redirect -file {AREA} {report_area}
redirect -file {SETUP} {report_timing -delay_type max -max_paths 20 -significant_digits 9}
redirect -file {HOLD} {report_timing -delay_type min -max_paths 20 -significant_digits 9}
redirect -file {VIOLATIONS} {report_constraint -all_violators -significant_digits 9}
redirect -file {TIMING_CHECK} {check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}}
redirect -file {DESIGN_CHECK} {check_design}
redirect -file {UNITS} {report_units}
redirect -file {HIERARCHY} {report_hierarchy}
write -format ddc -hierarchy -output {DDC}
write -format verilog -hierarchy -output {VERILOG}
write_sdc {SDC}
write_sdf -version 2.1 -significant_digits 9 {SDF}
puts "FULL_SOC_TIMING_CLOSURE_MAPPED inserted=$inserted_total"
'''
values = dict(STD=std, SRAM=sram, SOURCE=source / 'mapped.ddc', ORIGINAL_SDC=original_sdc,
              INTERIM_SETUP=case / 'incremental_setup.rpt', INTERIM_HOLD=case / 'incremental_hold.rpt',
              INTERIM_DDC=case / 'incremental.ddc', EDITS=case / 'hold-edits.tsv',
              AREA=case / 'area.rpt', SETUP=case / 'setup.rpt', HOLD=case / 'hold.rpt',
              VIOLATIONS=case / 'violations.rpt', TIMING_CHECK=case / 'timing_check.rpt',
              DESIGN_CHECK=case / 'check_design.rpt', UNITS=case / 'units.rpt',
              HIERARCHY=case / 'hierarchy.rpt', DDC=case / 'mapped.ddc',
              VERILOG=case / 'mapped.v', SDC=case / 'constraints.sdc', SDF=case / 'mapped.sdf')
for key, value in values.items():
    tcl = tcl.replace('{' + key + '}', '{' + str(value) + '}')
tcl = tcl.replace('SRAM_COUNT', str(rows * columns))
tcl = 'if {[catch {\n' + tcl + '\n} closure_error]} {puts stderr "FULL_SOC_TIMING_CLOSURE_FAILED: $closure_error"; exit 1}\nexit 0\n'
(case / 'run.tcl').write_text(tcl)
hashes[str(case / 'run.tcl')] = sha(case / 'run.tcl')
preparation = {'status': 'FULL_SOC_TIMING_CLOSURE_PREPARED_EXECUTION_PENDING',
               'geometry': args.geometry, 'mode': args.mode, 'source_case': str(source),
               'inputs_sha256': hashes, 'original_source_inputs_sha256': proof['inputs_sha256'],
               'original_setup_slack_ns': proof['setup_slack_ns'],
               'original_hold_slack_ns': proof['hold_slack_ns'],
               'clock_period_ns': 10, 'clock_or_constraints_relaxed': False,
               'scope': 'Mapped-cell timing optimization and noninverting D-path buffers in complete all8 SoC; original RTL/library/common SDC unchanged. No function, accuracy, gate/SDF or ADP acceptance from preparation.'}
(case / 'preparation.json').write_text(json.dumps(preparation, indent=2) + '\n')


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))


argv = ['dc_shell', '-f', str(case / 'run.tcl')]
started = time.monotonic()
with (case / 'run.log').open('w') as log:
    process = subprocess.Popen(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, preexec_fn=limits)
    (case / 'live-process.json').write_text(json.dumps({
        'status': 'RUNNING', 'pid': process.pid, 'argv': argv}, indent=2) + '\n')
    try:
        while True:
            try:
                code = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                print('FULL_SOC_TIMING_CLOSURE_RUNNING elapsed_s=%d' % (time.monotonic() - started), flush=True)
                if time.monotonic() - started > 7200:
                    raise TimeoutError('timing closure')
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
verify(proof['inputs_sha256'])
if code != 0 or 'FULL_SOC_TIMING_CLOSURE_MAPPED' not in log or re.search(r'^Error:', log, re.M):
    (case / 'failure.json').write_text(json.dumps({**preparation,
        'status': 'FULL_SOC_TIMING_CLOSURE_TOOL_FAILURE', 'returncode': code,
        'elapsed_s': time.monotonic() - started}, indent=2) + '\n')
    raise RuntimeError(str(case / 'run.log'))
assert 'Thank you...' in log
artifacts = ['area.rpt', 'setup.rpt', 'hold.rpt', 'violations.rpt', 'timing_check.rpt',
             'check_design.rpt', 'hierarchy.rpt', 'units.rpt', 'mapped.ddc', 'mapped.v',
             'constraints.sdc', 'mapped.sdf', 'hold-edits.tsv', 'run.tcl',
             'incremental_setup.rpt', 'incremental_hold.rpt', 'incremental.ddc']
for name in artifacts:
    assert (case / name).is_file() and (case / name).stat().st_size > 0, name


def slack(name):
    return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / name).read_text())[1])


setup, hold = slack('setup.rpt'), slack('hold.rpt')
summary = {**preparation,
           'status': 'FULL_ALL8_SOC_MAPPED_TIMING_PASS' if min(setup, hold) >= 0 else 'FULL_ALL8_SOC_MAPPED_TIMING_FAIL',
           'area': float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1]),
           'setup_slack_ns': setup, 'hold_slack_ns': hold, 'SRAMs': rows * columns,
           'inserted_D_path_buffers': len((case / 'hold-edits.tsv').read_text().splitlines()) - 1,
           'elapsed_s': time.monotonic() - started,
           'artifact_sha256': {name: sha(case / name) for name in artifacts},
           'gate_SDF_functional_simulation_passed': False,
           'official_accuracy_accepted': False, 'final_ADP_accepted': False,
           'scope': 'Complete all8 CPU/router/MMIO/arbiter/accelerator SoC after mapped-cell setup optimization and measured noninverting D-path hold buffers. Original common clock, 0.1ns uncertainty, IO constraints and libraries retained. Negative timing is a failure. Function, official accuracy, full-model latency/ADP and gate/SDF simulation remain separate gates.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({key: summary[key] for key in ['status', 'area', 'setup_slack_ns', 'hold_slack_ns', 'inserted_D_path_buffers', 'SRAMs']}), flush=True)
