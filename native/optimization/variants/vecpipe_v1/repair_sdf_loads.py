"""Repair the actual24 NOR2 negative MAXIMUM arcs in the streaming SoC.

Reuse the qualified original noninverting BUF_X4 output-load procedure. The
physical histogram comes from the new complete SDF; only the two unbound
logical constant primitives are counted from the loaded DDC. Require exact
physical reference histograms before/after and remeasure unchanged SDC/STA.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
source = ROOT / 'work/vecpipe-full-soc-timing-v1'
scanfile = ROOT / 'work/vecpipe-full-soc-timing-v1-sdf-audit/scan.json'
case = ROOT / ('work/vecpipe-full-soc-loads-' + args.revision)
assert not case.exists(), 'Preserve prior load repair'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


def verify(hashes):
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


proof = json.loads((source / 'summary.json').read_text())
assert proof['status'] == 'VECPIPE_FULL_ALL8_SOC_CLOSED_TIMING_PASS'
assert min(proof['setup_slack_ns'], proof['hold_slack_ns']) >= 0 and proof['SRAMs'] == 128
verify(proof['source_sha256'])
for name, expected in proof['artifact_sha256'].items():
    assert sha(source / name) == expected, name
scan = json.loads(scanfile.read_text())
assert scan['status'] == 'NEGATIVE_MAXIMUM_IOPATH_REMAINS'
assert scan['source_sha256'] == sha(source / 'mapped.sdf')
assert scan['negative_MAXIMUM_IOPATH_entries'] == 24
qualification = ROOT / 'work/vecpipe-sdf-parser-qualification-v1/summary.json'
parser_proof = json.loads(qualification.read_text())
assert parser_proof['status'] == 'SDF_IOPATH_PARSER_KNOWN_FULL_SOC_POSITIVE_AND24_NEGATIVE_ARCS_QUALIFICATION_PASS'
verify(parser_proof['source_sha256'])
known = json.loads((qualification.parent / 'positive.json').read_text())
assert scan['driver_sha256'] == known['driver_sha256'] == sha(HERE / 'scan_sdf_iopath.py')
targets = scan['negative_output_targets']
assert len(targets) == 24
assert all(item['celltype'] == 'NOR2_X2' and item['output_pin'] == 'ZN'
           and re.fullmatch(r'u_backend/u_softmax/core/U\d+', item['instance']) for item in targets)
physical = dict(scan['SDF_cell_histogram_including_top_and_wiring_scopes'])
assert physical.pop('soc_accel_top') == 1 and physical['SRAM_6T_CORE_16x8_MC_TB'] == 128
assert all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) and count > 0 for name, count in physical.items())
physical_leaves = sum(physical.values())
assert physical_leaves == 701259
template_path = ROOT / 'repair_sdf_cell_loads.py'
tree = ast.parse(template_path.read_text())
templates = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == 'tcl' for target in node.targets)
             and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
             and 'FULL_SOC_SDF_LOAD_REPAIR_MAPPED' in node.value.value]
assert len(templates) == 1
tcl = templates[0]
needle = 'array set expected [list PHYSICAL_COUNTS CONSTANT_COUNTS]'
assert tcl.count(needle) == 1
tcl = tcl.replace(needle, '''array set expected [list PHYSICAL_COUNTS]
# The SDF independently fixes every physical reference/count. Only the same
# two known unbound logical constants are learned from this new DDC.
foreach ref [get_attribute [get_cells -hier -filter "is_hierarchical == false"] ref_name] {
 if {$ref eq "**logic_0**" || $ref eq "**logic_1**"} {incr expected($ref)}
}
foreach ref [list {**logic_0**} {**logic_1**}] {
 if {![info exists expected($ref)] || $expected($ref) <= 0} {error "missing expected logical constant primitive"}
}''')
case.mkdir()
std, sram = [PROJECT / 'build/dc_libraries' / name for name in ['standard.db', 'sram.db']]
values = dict(STD=std, SRAM=sram, SOURCE=source / 'mapped.ddc', EDITS=case / 'output-buffer-edits.tsv',
              AREA=case / 'area.rpt', SETUP=case / 'setup.rpt', HOLD=case / 'hold.rpt',
              VIOLATIONS=case / 'violations.rpt', TIMING_CHECK=case / 'timing_check.rpt',
              DESIGN_CHECK=case / 'check_design.rpt', HIERARCHY=case / 'hierarchy.rpt',
              UNITS=case / 'units.rpt', DDC=case / 'mapped.ddc', VERILOG=case / 'mapped.v',
              SDC=case / 'constraints.sdc', SDF=case / 'mapped.sdf')
for key, value in values.items():
    assert '{' + key + '}' in tcl, key
    tcl = tcl.replace('{' + key + '}', '{' + str(value) + '}')
tcl = tcl.replace('SOURCE_LEAVES', str(physical_leaves))
tcl = tcl.replace('PHYSICAL_COUNTS', ' '.join('{%s} %d' % item for item in sorted(physical.items())))
tcl = tcl.replace('BUFFER_CELL', 'BUF_X4').replace('TARGETS', ' '.join('{' + item['instance'] + '}' for item in targets))
tcl = 'if {[catch {\n' + tcl + '\n} repair_error]} {puts stderr "VECPIPE_SDF_LOAD_REPAIR_FAILED: $repair_error"; exit 1}\nexit 0\n'
(case / 'run.tcl').write_text(tcl)
(case / 'targets.json').write_text(json.dumps(targets, indent=2) + '\n')
paths = [Path(__file__), template_path, source / 'summary.json', source / 'mapped.ddc',
         source / 'mapped.v', source / 'mapped.sdf', source / 'constraints.sdc', scanfile,
         qualification, HERE / 'scan_sdf_iopath.py', std, sram, case / 'run.tcl', case / 'targets.json']
hashes = {str(path): sha(path) for path in paths}
preparation = {'status': 'VECPIPE_SDF_OUTPUT_LOAD_REPAIR_PREPARED_EXECUTION_PENDING',
               'source_case': str(source), 'source_sha256': hashes, 'geometry': 'R8_C16_L4',
               'target_instances': [item['instance'] for item in targets], 'buffer': 'BUF_X4',
               'clock_period_ns': 10, 'source_SRAMs': 128, 'source_physical_leaves': physical_leaves,
               'source_physical_reference_histogram': physical,
               'logical_constants_policy': 'Only **logic_0** and **logic_1** may be omitted from physical SDF; exact counts obtained from loaded source DDC, verified unbound to any library cell/design and retained through repair.',
               'scope': 'Exactly24 noninverting output-load buffers at actual source negative arcs; no compilation/RTL/reference/clock change and no SDF value clamping.'}
(case / 'preparation.json').write_text(json.dumps(preparation, indent=2) + '\n')
records = []


def run(argv, label, limit):
    argv = list(map(str, argv))
    print('VECPIPE_SDF_LOAD_REPAIR_PHASE ' + label, flush=True)
    started = time.monotonic()
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))
    with (case / (label + '.log')).open('w') as log:
        process = subprocess.Popen(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=limits)
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('VECPIPE_SDF_LOAD_REPAIR_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > limit:
                        raise TimeoutError(label)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
    records.append({'label': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    assert code == 0, str(case / (label + '.log'))
    return (case / (label + '.log')).read_text(errors='replace')


log = run(['dc_shell', '-f', case / 'run.tcl'], 'map', 1800)
assert 'FULL_SOC_SDF_LOAD_REPAIR_MAPPED inserted=24' in log and 'Thank you...' in log
assert not re.search(r'^Error:', log, re.M)
counts = list(re.finditer(r'SDF_REPAIR_EXACT_LEAF_HISTOGRAM_PASS stage=(source|repaired) physical_expected=701259 constant0=(\d+) constant1=(\d+)', log))
assert len(counts) == 2 and [item[1] for item in counts] == ['source', 'repaired']
assert counts[0].groups()[1:] == counts[1].groups()[1:]
verify(hashes)
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'normalized_sdc']
assert len(functions) == 1
namespace = {}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(template_path), 'exec'), namespace)
same_sdc = namespace['normalized_sdc'](source / 'constraints.sdc') == namespace['normalized_sdc'](case / 'constraints.sdc')
run([sys.executable, '-B', HERE / 'scan_sdf_iopath.py', '--input', case / 'mapped.sdf',
     '--output', case / 'iopath-scan.json'], 'scan', 600)
after = json.loads((case / 'iopath-scan.json').read_text())


def slack(name):
    return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / name).read_text())[1])


setup, hold = slack('setup.rpt'), slack('hold.rpt')
accepted = min(setup, hold) >= 0 and same_sdc and after['negative_MAXIMUM_IOPATH_entries'] == 0
assert after['SDF_cell_histogram_including_top_and_wiring_scopes']['SRAM_6T_CORE_16x8_MC_TB'] == 128
assert sum(after['SDF_cell_histogram_including_top_and_wiring_scopes'].values()) - 1 == physical_leaves + 24
names = ['mapped.ddc', 'mapped.v', 'mapped.sdf', 'constraints.sdc', 'area.rpt', 'setup.rpt', 'hold.rpt',
         'violations.rpt', 'timing_check.rpt', 'check_design.rpt', 'hierarchy.rpt', 'units.rpt',
         'output-buffer-edits.tsv', 'targets.json', 'iopath-scan.json', 'iopath-scan.driver.py', 'run.tcl', 'map.log', 'scan.log']
result = {**preparation, 'status': 'VECPIPE_FULL_ALL8_SOC_SDF_LOAD_REPAIR_PASS' if accepted else 'VECPIPE_SDF_LOAD_REPAIR_NOT_ACCEPTED',
          'area': float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1]),
          'setup_slack_ns': setup, 'hold_slack_ns': hold, 'SRAMs': 128,
          'source_DC_nonphysical_constant_primitives': {'**logic_0**': int(counts[0][2]), '**logic_1**': int(counts[0][3])},
          'inserted_output_buffers': 24, 'normalized_SDC_identical_to_source': same_sdc,
          'selected_MAXIMUM_IOPATH_nonnegative': after['negative_MAXIMUM_IOPATH_entries'] == 0,
          'iopath_scan': {key: after[key] for key in ['IOPATH_entries_scanned', 'negative_MAXIMUM_IOPATH_entries', 'negative_IOPATH_entries_any_triplet_field']},
          'source_and_repaired_exact_physical_reference_histograms_verified': True,
          'commands': records, 'artifact_sha256': {name: sha(case / name) for name in names},
          'gate_SDF_functional_simulation_passed': False, 'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Full mapped SoC with exactly24 actual NOR2 output-load corrections and unchanged constraints; physical histograms and all selected MAXIMUM cell-delay fields checked. Not annotation or actual gate simulation acceptance.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
assert accepted, str(case / 'summary.json')
print(json.dumps({key: result[key] for key in ['status', 'area', 'setup_slack_ns', 'hold_slack_ns', 'iopath_scan']}), flush=True)
