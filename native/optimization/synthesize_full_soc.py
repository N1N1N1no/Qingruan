"""Map CPU/router/MMIO/arbiter plus an already mapped, current all8 backend.

Reuse the proven CPU conversion and backend mappings without modifying either.
Complete-inference correctness, organizer accuracy and final ADP are separate
acceptance gates; a synthesis PASS never establishes any of those gates.
"""
import argparse
import hashlib
import importlib.util
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
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
PROJECT = TASK / 'my_design/project'
parser = argparse.ArgumentParser()
parser.add_argument('--geometry', choices=['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4'], default='R8_C16_L4')
parser.add_argument('--mode', choices=['shared', 'private'], default='shared')
parser.add_argument('--stage', choices=['prepare', 'map', 'all'], default='all')
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
rows, cols, lanes = map(int, re.fullmatch(r'R(\d+)_C(\d+)_L(\d+)', args.geometry).groups())
case = ROOT / 'work/full-soc-synthesis' / args.geometry / args.mode / args.revision
backend = ROOT / 'work/backend-synthesis' / args.geometry / args.mode
live_fixture = ROOT / 'work/full-rv32/R8_C16_L4/ILSVRC2012_val_00000274.JPEG'
original = TASK / 'work/soc_read.dbjzuval/prepared'
std, sram = [PROJECT / 'build/dc_libraries' / name for name in ['standard.db', 'sram.db']]
commands = []


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify(hashes):
    for name, wanted in hashes.items():
        assert sha(local(name)) == wanted, name


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))


def run(argv, label, deadline, output=None):
    print('FULL_SOC_SYNTHESIS_PHASE ' + label, flush=True)
    started = time.monotonic()
    argv = list(map(str, argv))
    with (output or case / (label + '.log')).open('w') as log:
        process = subprocess.Popen(argv, cwd=case, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=limits)
        (case / 'live-process.json').write_text(json.dumps({
            'phase': label, 'status': 'RUNNING', 'pid': process.pid,
            'argv': argv}, indent=2) + '\n')
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('FULL_SOC_SYNTHESIS_RUNNING ' + label +
                          ' elapsed_s=%d' % (time.monotonic() - started), flush=True)
                    if time.monotonic() - started > deadline:
                        raise TimeoutError(label)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
            (case / 'live-process.json').write_text(json.dumps({
                'phase': label, 'status': 'TERMINAL',
                'returncode': process.returncode}, indent=2) + '\n')
    commands.append({'phase': label, 'argv': argv, 'returncode': code,
                     'elapsed_s': time.monotonic() - started})
    (case / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    assert code == 0, str(output or case / (label + '.log'))


if args.stage in ['prepare', 'all']:
    assert not case.exists(), 'Preserve previous attempt; use another revision'
    review = json.loads((ROOT / 'phase1-review.json').read_text())
    assert review['phase1_accepted'] and not review['pending_phase1']
    proof = json.loads((backend / 'summary.json').read_text())
    assert proof['status'] == 'MAPPED_UNIT_SETUP_PASS'
    assert proof['SRAMs'] == rows * cols and proof['clock_period_ns'] == 10
    verify(proof['source_sha256'])
    for name, wanted in proof['artifact_sha256'].items():
        assert sha(backend / name) == wanted, name
    original_hashes = json.loads((original / 'frozen_hashes.json').read_text())
    verify(original_hashes)
    fixture_proof = json.loads((live_fixture / 'preparation.json').read_text())
    fixture_top = live_fixture / 'soc_accel_top.sv'
    assert sha(fixture_top) == next(value for key, value in fixture_proof['source_sha256'].items()
                                    if key.endswith('/soc_accel_top.sv'))
    case.mkdir(parents=True)
    inputs = case / 'inputs'
    inputs.mkdir()
    text = fixture_top.read_text()
    assert text.count(".SUPPORTED_OPS(32'h1fe)") == 1
    needle = 'axi_accel_backend #(.ROWS(8),.COLS(16),.LANES(4),.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend('
    assert text.count(needle) == 1
    text = text.replace(needle, 'axi_accel_backend #(.ROWS(%d),.COLS(%d),.LANES(%d),.ID_WIDTH(`RV_LSU_BUS_TAG)) u_backend(' % (rows, cols, lanes))
    (inputs / 'soc_accel_top.sv').write_text(text)
    source_backend = ROOT / 'rtl' / ('axi_accel_shared_backend.sv' if args.mode == 'shared' else 'axi_accel_complete_backend.sv')
    header = re.search(r'module axi_accel_backend[\s\S]*?\n\);', source_backend.read_text())[0]
    (inputs / 'backend_stub.sv').write_text('(* blackbox *)\n' + header + '\nendmodule\n')
    for name in ['axi_accel_mmio.sv', 'axi_dma_arbiter.sv']:
        (inputs / name).write_bytes((ROOT / 'rtl' / name).read_bytes())
    conversion = json.loads((original / 'conversion.json').read_text())
    cpu_command = []
    for argument in conversion['commands'][0]['command']:
        if argument.startswith('/') and '/project/rtl/' in argument:
            continue
        if argument.endswith('/project/synth/sram_blackbox.v') or argument == '-DPE_REFERENCE_MACRO':
            continue
        cpu_command.append(argument)
    cpu_command.extend([PROJECT / 'rtl/axi_pe_router.v', inputs / 'axi_accel_mmio.sv',
                        inputs / 'axi_dma_arbiter.sv', inputs / 'backend_stub.sv',
                        inputs / 'soc_accel_top.sv'])
    run(cpu_command, 'sv2v', 600, case / 'soc_sv2v.raw.v')
    folder = PROJECT / 'scripts/fold_verilog_literal_slices.py'
    spec = importlib.util.spec_from_file_location('literal_folds', folder)
    fold = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fold)
    folded, edits = fold.fold_literal_slices((case / 'soc_sv2v.raw.v').read_text())
    assert fold.fold_literal_slices(folded) == (folded, [])
    (case / 'soc_sv2v.v').write_text(folded)
    (case / 'literal_folds.json').write_text(json.dumps(edits, indent=2) + '\n')
    script = '''read_verilog "%s"
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
''' % (case / 'soc_sv2v.v', case / 'soc_control_dc.raw.v')
    (case / 'prepare.ys').write_text(script)
    run(['/home/zhz/miniforge3/envs/rtl-yosys/bin/yosys', '-s', case / 'prepare.ys'], 'yosys', 900)
    control = (case / 'soc_control_dc.raw.v').read_text()
    pattern = r'(?m)^[ \t]*(?:\\\S*axi_accel_backend\S*|axi_accel_backend)(?:[ \t]*#\([\s\S]*?\))?[ \t]+u_backend[ \t]*\('
    matches = list(re.finditer(pattern, control))
    assert len(matches) == 1
    match = matches[0]
    parameters = dict(re.findall(r'\.(\w+)\(([^)]*)\)', match[0]))
    expected = dict(ROWS=rows, COLS=cols, LANES=lanes, ID_WIDTH=3)
    for key, value in parameters.items():
        assert key in expected and value.strip() == "32'sd%d" % expected[key], parameters
    for key in ['ROWS', 'COLS', 'LANES']:
        assert key in parameters, parameters
    control = control[:match.start()] + '  axi_accel_backend u_backend (' + control[match.end():]
    (case / 'soc_control_dc.v').write_text(control)
    frozen = [Path(__file__), ROOT / 'phase1-review.json', ROOT / 'phase1-selection.json',
              backend / 'summary.json', backend / 'mapped.ddc', backend / 'constraints.sdc',
              original / 'conversion.json', original / 'frozen_hashes.json',
              folder, fixture_top, PROJECT / 'rtl/axi_pe_router.v',
              PROJECT / 'synth/soc_io.sdc', std, sram, case / 'soc_control_dc.v',
              case / 'prepare.ys', *inputs.iterdir(), *map(local, original_hashes),
              *map(local, proof['source_sha256'])]
    hashes = {str(path): sha(path) for path in dict.fromkeys(frozen)}
    preparation = {'status': 'FULL_ALL8_SOC_PREPARED_MAPPING_PENDING',
                   'geometry': args.geometry, 'mode': args.mode,
                   'rows': rows, 'columns': cols, 'lanes': lanes,
                   'top': 'soc_accel_top', 'inputs_sha256': hashes,
                   'parameters_removed_from_generic_backend_instance': parameters,
                   'CPU_control_preparation_commands': commands,
                   'backend_reuse': 'current all8 mapped DDC imported with local clocks/IO constraints reset; mapped hierarchy retained',
                   'scope': 'CPU, MMIO, router, arbiter, all8 accelerator engines, exactly one array and its SRAM macros; external simulation RAM is outside synthesis, matching baseline SoC boundary'}
    (case / 'preparation.json').write_text(json.dumps(preparation, indent=2) + '\n')
    if args.stage == 'prepare':
        print('FULL_ALL8_SOC_PREPARED_NOT_MAPPED', flush=True)
        raise SystemExit(0)
else:
    assert (case / 'preparation.json').is_file()
    preparation = json.loads((case / 'preparation.json').read_text())
    commands = preparation['CPU_control_preparation_commands']

assert not (case / 'summary.json').exists() and not (case / 'synthesis.log').exists(), 'Preserve completed/interrupted attempt'
hashes = preparation['inputs_sha256']
verify(hashes)
tcl = '''set_app_var target_library [list {STD}]
set_app_var link_library [list * {STD} {SRAM}]
read_db {STD}
read_db {SRAM}
set imported [read_ddc {BACKEND}]
foreach name $imported {
 current_design $name
 reset_design
 set_local_link_library [list * {STD} {SRAM}]
 set_dont_touch [current_design] true
}
current_design axi_accel_backend
if {[get_object_name [current_design]] ne "axi_accel_backend" || ![link]} {error "wrong imported backend"}
set control_designs [read_verilog -rtl {CONTROL}]
if {[lsearch -exact $control_designs soc_accel_top] < 0} {error "whole SoC top not imported"}
current_design soc_accel_top
if {![link]} {error "SoC link failed"}
if {[sizeof_collection [get_cells -quiet u_backend]] != 1} {error "missing backend"}
if {[sizeof_collection [get_cells -filter "is_hierarchical == false"]] < 10000} {error "missing CPU/control"}
if {[sizeof_collection [get_ports -quiet {ifu_axi_araddr[31] mem_axi_rdata[63]}]] != 2} {error "wrong SoC boundary"}
source {SDC_SOURCE}
if {[sizeof_collection [all_clocks]] != 1 || [get_attribute [get_clocks core_clk] period] != 10} {error "wrong clock constraints"}
set_fix_hold [get_clocks core_clk]
if {![compile]} {error "SoC compile failed"}
if {[get_object_name [current_design]] ne "soc_accel_top"} {error "wrong mapped top"}
if {[sizeof_collection [get_cells -hier -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {error "unmapped SoC leaves"}
if {[sizeof_collection [get_cells -hier -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]] != SRAM_COUNT} {error "wrong SRAM count"}
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
puts FULL_ALL8_SOC_SYNTHESIS_MAPPED
'''
paths = dict(STD=std, SRAM=sram, BACKEND=backend / 'mapped.ddc', CONTROL=case / 'soc_control_dc.v',
             SDC_SOURCE=PROJECT / 'synth/soc_io.sdc', AREA=case / 'area.rpt', SETUP=case / 'setup.rpt',
             HOLD=case / 'hold.rpt', VIOLATIONS=case / 'violations.rpt', TIMING_CHECK=case / 'timing_check.rpt',
             DESIGN_CHECK=case / 'check_design.rpt', HIERARCHY=case / 'hierarchy.rpt', UNITS=case / 'units.rpt',
             DDC=case / 'mapped.ddc', VERILOG=case / 'mapped.v', SDC=case / 'constraints.sdc', SDF=case / 'mapped.sdf')
for key, value in paths.items():
    tcl = tcl.replace('{' + key + '}', '{' + str(value) + '}')
tcl = tcl.replace('SRAM_COUNT', str(rows * cols))
tcl = 'if {[catch {\n' + tcl + '\n} synthesis_error]} {puts stderr "FULL_SOC_SYNTHESIS_FAILED: $synthesis_error"; exit 1}\nexit 0\n'
(case / 'run.tcl').write_text(tcl)
tcl_hash = sha(case / 'run.tcl')
run(['dc_shell', '-f', case / 'run.tcl'], 'synthesis', 7200)
log = (case / 'synthesis.log').read_text(errors='replace')
assert 'FULL_ALL8_SOC_SYNTHESIS_MAPPED' in log and 'Thank you...' in log
assert not re.search(r'^Error:', log, re.M), str(case / 'synthesis.log')
verify(hashes)
assert sha(case / 'run.tcl') == tcl_hash
artifacts = ['area.rpt', 'setup.rpt', 'hold.rpt', 'violations.rpt', 'timing_check.rpt',
             'check_design.rpt', 'hierarchy.rpt', 'units.rpt', 'mapped.ddc', 'mapped.v',
             'constraints.sdc', 'mapped.sdf', 'run.tcl']
for name in artifacts:
    assert (case / name).is_file() and (case / name).stat().st_size > 0, name


def slack(name):
    return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / name).read_text())[1])


setup, hold = slack('setup.rpt'), slack('hold.rpt')
result = {**preparation,
          'status': 'FULL_ALL8_SOC_MAPPED_TIMING_PASS' if min(setup, hold) >= 0 else 'FULL_ALL8_SOC_MAPPED_TIMING_FAIL',
          'area': float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1]),
          'setup_slack_ns': setup, 'hold_slack_ns': hold, 'clock_period_ns': 10,
          'SRAMs': rows * cols, 'commands': commands,
          'artifact_sha256': {name: sha(case / name) for name in artifacts},
          'official_accuracy_accepted': False, 'full_inference_accepted': False,
          'final_ADP_accepted': False, 'gate_SDF_functional_simulation_passed': False,
          'scope': 'Complete CPU/router/MMIO/arbiter/all8 mapped SoC, common baseline library and SDC; synthesis estimates and exported netlist/SDC/SDF only. Negative timing remains a failure. Full inference, organizer accuracy and gate/SDF function are separate unresolved gates.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'geometry', 'mode', 'area', 'setup_slack_ns', 'hold_slack_ns', 'SRAMs']}), flush=True)
