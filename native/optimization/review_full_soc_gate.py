"""Accept only the scoped complete-SoC 22-command SDF regression.

This never accepts full model inference, organizer accuracy, final ADP or
formal equivalence. Every required stage must finish on the same executable.
"""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
import sys

from gate_connectivity_audit import ROOT, BASE, SCRIPTS, need, sha, _sdf_path
from gate_runtime_diagnostics import checked_runtime_text
sys.path.insert(0, str(SCRIPTS))
from run_pe_axi_sdf import compiled_runtime_files

parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v5')
parser.add_argument('--probe-revision', default='v1')
args = parser.parse_args()
need(all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in [args.revision, args.probe_revision]), 'invalid revision')
case = ROOT / 'work/full-soc-gate/R8_C16_L4/shared' / args.revision
output = case / 'summary.json'
need(not output.exists(), 'preserve prior review')
probe = case / ('timing-probe-ucli-' + args.probe_revision)


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def verify(hashes):
    for path, value in hashes.items():
        need(sha(local(path)) == value, 'changed input ' + path)


prep = json.loads((case / 'preparation.json').read_text())
compiled = json.loads((case / 'compile-result.json').read_text())
annotation = json.loads((case / 'annotation-review.json').read_text())
normal = json.loads((case / 'normal-result.json').read_text())
corrupt = json.loads((case / 'corrupt-result.json').read_text())
timing = json.loads((probe / 'summary.json').read_text())
need(annotation['status'] == 'ALL_PHYSICAL_SDF_ANNOTATION_AND_SOURCE_DIAGNOSTICS_PASS_RUNTIME_PENDING', 'annotation review incomplete')
verify(prep['source_sha256'])
verify(prep['prepared_sha256'])
verify(compiled['source_sha256'])
verify(annotation['source_sha256'])
verify(timing['source_sha256'])
need(compiled_runtime_files(case) == compiled['runtime_files'], 'runtime changed')
for stage, record in [('normal', normal), ('corrupt', corrupt)]:
    need(record['status'] == 'GATE_STAGE_DIAGNOSTIC_CHECKS_PASS_ANNOTATION_ACCEPTANCE_OPEN', 'unfinished stage ' + stage)
    need(record['stage'] == stage and record['runtime_files_unchanged'], 'stage/runtime mismatch')
    need(bool(record.get('executor_source_sha256')) and record.get('runtime_wall_limit_s') == 86400,
         'runtime executor was not frozen with the required wall cap')
    verify(record['executor_source_sha256'])
    need(sha(case / (stage + '.log')) == record['log_sha256'], 'changed stage log')
    need(all(sha(case / name) == value for name, value in record['annotation_reports_sha256'].items()), 'stage annotation changed')
    need(record['command']['returncode'] == 0, 'stage process failed')
    text = (case / (stage + '.log')).read_text(errors='replace')
    checked_runtime_text(case, stage, text, local)
positive = (case / 'normal.log').read_text()
negative = (case / 'corrupt.log').read_text()
need(positive.count('COMPLETE_SOC_PASS commands=22 all8_engines=1') == 1 and
     positive.count('COMPLETE_SOC_CPU_RETURN_PASS') == 1, 'normal completion absent')
need('COMPLETE_SOC_NEGATIVE_PASS CPU_detected_corrupted_byte=7' in negative and
     'COMPLETE_SOC_CPU_RETURN_FAIL code=bad00120' in negative and
     'COMPLETE_SOC_CPU_RETURN_PASS' not in negative, 'functional corruption not detected')
trace = [list(map(int, match.groups())) for match in re.finditer(
    r'COMPLETE_SOC_ENGINE_DONE command=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', positive)]
need(trace == prep['expected_RTL_engine_trace'] and len(trace) == 22, 'gate engine trace differs from RTL')
need((case / 'normal-report.txt').read_bytes() == (local(prep['gold_RTL_case']) / 'report.txt').read_bytes(), 'gate CPU/call cycles differ from RTL')
need(timing['status'] == 'SAME_COMPILED_FULL_SOC_SRAM_TIMING_NEGATIVE_CONTROL_PASS' and
     timing['same_compiled_runtime_verified'] and timing['CPU_in_reset'] and
     timing['actual_data_after_clock_fs'] == 100 and timing['timing_violations'] == timing['SRAM_notifier_events'] == 1,
     'actual timing negative control incomplete')
need(sha(probe / 'run.log') == timing['log_sha256'], 'timing probe log changed')

# Require the observed warned INTERCONNECT delays to match actual MAXIMUM
# triplets at the simulator's 1fs precision; a positive arbitrary delay fails.
wire_review = json.loads((case / 'wire-connectivity-review.json').read_text())
verify(wire_review['source_sha256'])
observed = re.findall(r'FULL_SOC_SDF_WARNED_DELAY_OBSERVED SWC_events=(\d+) SWC_first_ns=([\d.]+) IWSBA_events=(\d+) IWSBA_first_ns=([\d.]+)', positive)
need(len(observed) == 1, 'warned pin-delay observations absent')


def femtoseconds(value):
    return int((Decimal(value) * 1000000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


pin_observations = {}
for code, count, measured in [('SDFCOM_SWC', observed[0][0], observed[0][1]),
                              ('SDFCOM_IWSBA', observed[0][2], observed[0][3])]:
    representative = wire_review['representatives'][code]
    selected = [representative[key][-1] for key in ['rise_triplet_ns', 'fall_triplet_ns']]
    need(int(count) > 0 and femtoseconds(measured) in list(map(femtoseconds, selected)), 'measured pin delay differs from SDF: ' + code)
    pin_observations[code] = {'events': int(count), 'first_measured_ns': measured,
                              'SDF_MAXIMUM_rise_fall_ns': selected, 'entry': representative['sdf_entry']}
macro_observed = re.findall(r'FULL_SOC_SDF_DELAY_OBSERVED events=(\d+) first_sram_ns=([\d.]+)', positive)
need(len(macro_observed) == 1 and int(macro_observed[0][0]) > 0, 'actual SRAM clock-to-output delay absent')
macro_delays = []
inside = False
with (case / 'soc.sdf').open() as stream:
    for line in stream:
        if '(INSTANCE' in line:
            instance = re.search(r'\(INSTANCE\s*(.*?)\)', line)[1]
            inside = bool(instance) and _sdf_path(instance) == ['u_backend', 'u_array', 'R[0].C[0].u_weight', 'u_macro']
        if inside and '(IOPATH (posedge clk) rd_out[' in line:
            triples = re.findall(r'\(([-+0-9.eE]+:[-+0-9.eE]+:[-+0-9.eE]+)\)', line)
            macro_delays.extend(triple.split(':')[-1] for triple in triples)
        if inside and '(TIMINGCHECK' in line:
            break
need(femtoseconds(macro_observed[0][1]) in list(map(femtoseconds, macro_delays)), 'SRAM measured delay differs from SDF')
verify(compiled['source_sha256'])
need(compiled_runtime_files(case) == compiled['runtime_files'], 'runtime changed during final review')
paths = [Path(__file__), case / 'preparation.json', case / 'compile-result.json',
         ROOT / 'gate_runtime_diagnostics.py',
         case / 'annotation-review.json', case / 'normal-result.json', case / 'corrupt-result.json',
         case / 'normal.log', case / 'corrupt.log', case / 'normal-report.txt',
         case / 'wire-connectivity-review.json', case / 'unused-output-review.json',
         probe / 'summary.json', probe / 'run.log']
paths.extend(local(path) for path in normal['executor_source_sha256'])
result = {'status': 'FULL_SOC_ALL8_GATE_SDF_22_COMMAND_ENGINEERING_CHECK_PASS',
          'geometry': 'R8_C16_L4', 'mode': 'shared', 'clock_period_ns': 10,
          'SDF_selection': 'MAXIMUM', 'commands': 22, 'RTL_gate_engine_and_CPU_call_cycles_equal': True,
          'complete_SDF_annotation': annotation['annotation'], 'normal_timing_violations': 0,
          'functional_corruption_detected_by_actual_CPU': True, 'timing_negative_control': str(probe / 'summary.json'),
          'actual_warned_interconnect_delay_observations': pin_observations,
          'SRAM_clock_to_output_first_delay_ns': macro_observed[0][1],
          'source_sha256': {str(path): sha(path) for path in paths},
          'full_model_inference_accepted': False, 'official_accuracy_accepted': False,
          'final_ADP_accepted': False, 'formal_equivalence_claimed': False,
          'scope': 'Complete mapped CPU/MMIO/router/all8 SoC running the fixed 22-command engineering firmware: all bytes, measured CPU call cycles and engine cycles match RTL, complete synthesis MAXIMUM SDF, nonzero pin delays, CPU corruption detection and one genuine scoped SRAM hold negative control. This is not full-image Swin inference, its full latency, multicorner/physical signoff, organizer accuracy or final ADP.'}
output.write_text(json.dumps(result, indent=2) + '\n')
print(result['status'], flush=True)
