"""Require complete SDF annotation plus source proof of every diagnostic."""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re
import sys

from gate_connectivity_audit import ROOT, BASE, SCRIPTS, need, sha
sys.path.insert(0, str(SCRIPTS))
from run_pe_axi_sdf import compiled_runtime_files
from validate_soc_sdf_warnings import _blocks

parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v4')
args = parser.parse_args()
need(re.fullmatch(r'[A-Za-z0-9_-]+', args.revision) is not None, 'invalid revision')
case = ROOT / 'work/full-soc-gate/R8_C16_L4/shared' / args.revision
output = case / 'annotation-review.json'
need(not output.exists(), 'preserve previous review')


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def verify(hashes):
    for path, expected in hashes.items():
        need(sha(local(path)) == expected, 'changed source: ' + path)


preparation = json.loads((case / 'preparation.json').read_text())
compiled = json.loads((case / 'compile-result.json').read_text())
need(preparation['status'] == 'FULL_SOC_GATE_FIXTURES_AND_SDF_READY_EXECUTION_PENDING', 'unprepared gate')
need(compiled['status'] == 'GATE_SDF_COMPILED_ANNOTATION_REVIEW_AND_EXECUTION_PENDING', 'uncompiled gate')
verify(preparation['source_sha256'])
verify(preparation['prepared_sha256'])
verify(compiled['source_sha256'])
need(compiled_runtime_files(case) == compiled['runtime_files'], 'compiled runtime changed')

wires = json.loads((case / 'wire-connectivity-review.json').read_text())
ports = json.loads((case / 'unused-output-review.json').read_text())
verify(wires['source_sha256'])
verify(ports['source_sha256'])
need(wires['status'] == 'WARNED_INTERCONNECT_SOURCE_CONNECTIVITY_PROVED_FULL_GATE_ACCEPTANCE_OPEN', 'wire review incomplete')
need(ports['status'] == 'ALL_TFIPC_MISSING_PORTS_PROVED_UNUSED_OUTPUTS' and ports['no_missing_input_or_inout'], 'unused-output review incomplete')
need(len(wires['locations']) == sum(wires['warning_counts'].values()), 'wire location count')
need(all(entry['pure_bit_wiring_equivalence_proved'] for entry in wires['locations']), 'unproven wire entry')
need(len(ports['locations']) == ports['warning_count'], 'port location count')
guard_path = ROOT / 'work/gate-connectivity-guards-v1/summary.json'
guards = json.loads(guard_path.read_text())
need(guards['status'] == 'BIT_WIRING_AUDITOR_GUARDS_PASS' and all(entry['passed'] for entry in guards['checks']), 'source-auditor guards failed')
need(guards['driver_sha256'] == sha(ROOT / 'gate_connectivity_audit.py'), 'source-auditor guard version differs')

compilation = (case / 'compile.log').read_text(errors='replace')
annotation = (case / 'sdf_annotation.log').read_text(errors='replace')
need(not re.search(r'(?im)^\s*(?:Error|Fatal)(?:-|:)', compilation + '\n' + annotation), 'compiler/annotation error')
codes = Counter(re.findall(r'(?m)^\s*(?:Warning|Lint)-\[([^]]+)\]', compilation))
expected = Counter({'LINX_KRNL': 1, 'TFIPC': ports['warning_count'], **wires['warning_counts']})
need(codes == expected, 'unknown/missing compiler diagnostics: ' + repr(codes))
need(len(re.findall(r'(?m)^\s*(?:Warning|Lint)(?:-|:)', compilation)) == sum(codes.values()), 'unnumbered compiler diagnostic')
kernel = _blocks(compilation, 'LINX_KRNL')
need(len(kernel) == 1 and re.fullmatch(
    r"Warning-\[LINX_KRNL\] Unsupported Linux kernel\n  Linux kernel '[^'\n]+' is not supported\.\n  Supported versions are 2\.4\* or 2\.6\*\.", kernel[0]) is not None,
    'unreviewed kernel-support diagnostic')
annotation_codes = Counter(re.findall(r'(?m)^\s*(?:Warning|Lint)-\[([^]]+)\]', annotation))
need(annotation_codes == Counter(wires['warning_counts']), 'unknown annotation diagnostic')
need(len(re.findall(r'(?m)^\s*(?:Warning|Lint)(?:-|:)', annotation)) == sum(annotation_codes.values()), 'unnumbered annotation diagnostic')

spec = importlib.util.spec_from_file_location('full_soc_prepared_sdf_checks', case / 'inputs/prepare_soc_sdf.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
annotation_counts = checker.validate_soc_annotation(case, preparation['sdf_adaptation'],
                                                    reviewed_warning_count=sum(annotation_codes.values()))
verify(compiled['source_sha256'])
need(compiled_runtime_files(case) == compiled['runtime_files'], 'runtime changed during review')
inputs = [Path(__file__), ROOT / 'gate_connectivity_audit.py', case / 'preparation.json',
          case / 'compile-result.json', case / 'wire-connectivity-review.json',
          case / 'unused-output-review.json', guard_path, case / 'sdfAnnotateInfo',
          case / 'compile.log', case / 'sdf_annotation.log']
result = {'status': 'ALL_PHYSICAL_SDF_ANNOTATION_AND_SOURCE_DIAGNOSTICS_PASS_RUNTIME_PENDING',
          'clock_period_ns': 10, 'SDF_selection': 'MAXIMUM', 'source_sha256': {str(p): sha(p) for p in inputs},
          'diagnostic_codes': dict(codes), 'annotation': annotation_counts,
          'all_IOPATH_INTERCONNECT_and_timing_limits_applied': True,
          'missing_input_or_inout_count': 0,
          'all_warned_interconnects_proved_by_exact_mapped_bit_wiring': True,
          'negative_IOPATH_clamping_diagnostics': 0,
          'compiled_runtime_files_unchanged': True,
          'gate_functional_simulation_accepted': False, 'timing_negative_control_accepted': False,
          'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Complete physical instance/path/limit annotation and every source-proven compiler notice only. Normal/corrupt/timing-probe runs on this same executable still require acceptance.'}
output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'diagnostic_codes',
                 'all_IOPATH_INTERCONNECT_and_timing_limits_applied']}), flush=True)
