import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
"""Induce one real SRAM hold failure after compensating its actual SDF MIPD.

Use the exact compiled normal simulator, netlist, SDF, checks and model. Only
the negative-control stimulus changes. The original unadjusted HDL probe is
retained as a failed stimulus diagnostic, never treated as acceptance.
"""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from gate_connectivity_audit import ROOT, BASE, SCRIPTS, need, sha, _sdf_path
sys.path.insert(0, str(SCRIPTS))
from run_pe_axi_sdf import compiled_runtime_files

parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v5')
parser.add_argument('--probe-revision', default='v1')
args = parser.parse_args()
need(all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in [args.revision, args.probe_revision]), 'invalid revision')
case = ROOT / 'work/vecpipe-full-soc-gate' / args.revision
probe = case / ('timing-probe-ucli-' + args.probe_revision)
need(not probe.exists(), 'preserve previous probe')
compiled = json.loads((case / 'compile-result.json').read_text())
annotation = json.loads((case / 'annotation-review.json').read_text())
preparation = json.loads((case / 'preparation.json').read_text())
need(annotation['status'] == 'ALL_PHYSICAL_SDF_ANNOTATION_AND_SOURCE_DIAGNOSTICS_PASS_RUNTIME_PENDING', 'annotation not accepted')


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def verify(hashes):
    for path, expected in hashes.items():
        need(sha(local(path)) == expected, 'changed input ' + path)


verify(compiled['source_sha256'])
verify(annotation['source_sha256'])
runtime = compiled_runtime_files(case)
need(runtime == compiled['runtime_files'], 'compiled runtime changed')
need('-debug_access+all' in compiled['command']['argv'], 'force debug capability absent')
target = ['u_backend', 'u_array', 'R[0].C[0].u_weight', 'u_macro']
macro = r'tb_soc.dut.u_backend.u_array.\R[0].C[0].u_weight .u_macro'
interconnect = None
celltype = instance = None
setup = hold = None
with (case / 'soc.sdf').open() as stream:
    for number, line in enumerate(stream, 1):
        if '(CELLTYPE ' in line:
            celltype = re.search(r'\(CELLTYPE "([^\"]+)"\)', line)[1]
        if '(INSTANCE' in line:
            instance = re.search(r'\(INSTANCE\s*(.*?)\)', line)[1]
        if '(INTERCONNECT ' in line and '/wd_in[0] ' in line:
            entry = re.fullmatch(r'\s*\(INTERCONNECT (\S+) (\S+) \(([^()]*)\)\)\s*', line)
            if entry and _sdf_path(entry[2]) == target + ['wd_in[0]']:
                need(interconnect is None, 'multiple input interconnects')
                values = list(map(Decimal, entry[3].split(':')))
                need(len(values) == 3 and min(values) >= 0, 'invalid input MIPD')
                interconnect = {'sdf_line': number, 'entry': line.strip(), 'MAXIMUM_delay_ns': str(values[-1])}
        if celltype == 'SRAM_6T_CORE_16x8_MC_TB' and instance and _sdf_path(instance) == target:
            check = re.fullmatch(r'\s*\((SETUP|HOLD) wd_in\[0\] \(posedge clk\) \(([^()]*)\)\)\s*', line)
            if check:
                values = list(map(Decimal, check[2].split(':')))
                need(len(values) == 3, 'invalid SRAM check')
                if check[1] == 'SETUP':
                    setup = values[-1]
                else:
                    hold = values[-1]
            if setup is not None and hold is not None:
                break
need(interconnect is not None and setup is not None and hold is not None, 'missing actual input/check SDF')


def fs(value):
    return int((Decimal(value) * 1000000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


half_period = 5000000
data_after_clock = 100
delay_fs = fs(interconnect['MAXIMUM_delay_ns'])
offset = half_period - delay_fs + data_after_clock
need(0 < offset < half_period and data_after_clock < fs(hold), 'stimulus outside available hold window')
need(preparation['sdf_adaptation']['sdf_selection'] == 'MAXIMUM', 'wrong SDF selection')
probe.mkdir()
commands = ['run 20ns', 'get {tb_soc.rst_n}', 'force {' + macro + ".wd_in} 8'h00",
            'run -negedge {' + macro + '.clk}', 'run %dfs' % offset,
            'force {' + macro + ".wd_in} 8'h01", 'run -change {' + macro + '.wd_in}',
            'get {' + macro + '.wd_in}', 'get {tb_soc.rst_n}', 'run 1ns',
            'get {' + macro + '.notifier}', 'get {tb_soc.rst_n}', 'quit']
control = probe / 'stimulus.do'
control.write_text('\n'.join(commands) + '\n')
argv = [str(case / 'simv')]
for arg in preparation['original_RTL_normal_argv'][1:]:
    if arg.startswith('+MEM_HEX='):
        argv.append('+MEM_HEX=' + str(case / 'inputs/program.hex'))
    elif arg.startswith('+REPORT='):
        argv.append('+REPORT=' + str(probe / 'unused-report.txt'))
    else:
        argv.append(arg)
argv += ['-ucli', '-do', str(control)]
inputs = [Path(__file__), ROOT / 'gate_connectivity_audit.py', control,
          case / 'compile-result.json', case / 'annotation-review.json']
hashes = {str(path): sha(path) for path in inputs}
annotation_hashes = {name: sha(case / name) for name in ['sdfAnnotateInfo', 'sdf_annotation.log']}
record = {'status': 'MIPD_COMPENSATED_TIMING_PROBE_PREPARED', 'argv': argv,
          'source_sha256': hashes, 'actual_input_interconnect': interconnect,
          'clock_period_ns': 10, 'setup_limit_fs': fs(setup), 'hold_limit_fs': fs(hold),
          'target_data_after_clock_fs': data_after_clock, 'force_after_negedge_fs': offset,
          'scope': 'Only negative-control stimulus compensates existing SDF input MIPD; no timing value, check, model, clock, netlist or compiled runtime changes.'}
(probe / 'preparation.json').write_text(json.dumps(record, indent=2) + '\n')
start = time.monotonic()
with (probe / 'run.log').open('w') as log:
    process = subprocess.run(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=60)
text = (probe / 'run.log').read_text(errors='replace')
verify(compiled['source_sha256'])
verify(annotation['source_sha256'])
verify(hashes)
need(compiled_runtime_files(case) == runtime, 'runtime changed by probe')
need(all(sha(case / name) == value for name, value in annotation_hashes.items()), 'annotation reports changed')
need(process.returncode == 0 and not re.search(r'(?im)(?:Error-\[|Fatal:|^\s*Warning-\[)', text), 'probe tool error')
violations = re.findall(r'(?m)^"([^"\n]+)", (\d+): Timing violation in ([^\n]+)$', text)
notifiers = re.findall(r'(?m)^SRAM_TIMING_VIOLATION instance=(.*?) time_ns=([0-9.]+)$', text)
windows = re.findall(r'\$setuphold\(\s*posedge clk:(\d+),\s*wd_in\[0\]:(\d+),\s*limits:\s*\((\d+),(\d+)\)', text)
need(len(violations) == len(notifiers) == len(windows) == text.count('Timing violation') == 1,
     'must observe exactly one genuine SRAM timing violation')
need(violations[0][0] == str(case / 'models/sram_model.v') and violations[0][2] == macro and notifiers[0][0] == macro,
     'wrong violation model or macro scope')
clock_fs, input_fs, setup_fs, hold_fs = map(int, windows[0])
need(input_fs - clock_fs == data_after_clock and setup_fs == fs(setup) and hold_fs == fs(hold), 'wrong real violation timing window')
need(fs(notifiers[0][1]) == input_fs, 'notifier time differs from input event')
need(re.findall(r'get \{tb_soc.rst_n\}\n([^\n]+)', text) == ["'b0"] * 3, 'CPU not held in reset throughout probe')
need(re.findall(r'get \{' + re.escape(macro) + r'\.wd_in\}\n([^\n]+)', text) == ["'b00000001"], 'actual pin did not become one')
need('COMPLETE_SOC_PASS' not in text and 'COMPLETE_SOC_CPU_RETURN' not in text, 'probe ran normal CPU program')
result = {**record, 'status': 'SAME_COMPILED_FULL_SOC_SRAM_TIMING_NEGATIVE_CONTROL_PASS',
          'actual_clock_fs': clock_fs, 'actual_input_fs': input_fs, 'actual_data_after_clock_fs': input_fs - clock_fs,
          'timing_violations': 1, 'SRAM_notifier_events': 1, 'CPU_in_reset': True,
          'same_compiled_runtime_verified': True, 'annotation_reports_unchanged': True,
          'elapsed_s': time.monotonic() - start, 'returncode': process.returncode,
          'log_sha256': sha(probe / 'run.log'), 'official_accuracy_accepted': False,
          'full_gate_functional_acceptance': False}
(probe / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'actual_data_after_clock_fs',
                 'setup_limit_fs', 'hold_limit_fs', 'timing_violations']}), flush=True)
