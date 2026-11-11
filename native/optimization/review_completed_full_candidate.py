"""Read-only review of a completed real-RV32 candidate, preserving each revision.

The simulator implementation, full-model execution, common PPA conditions and
official-reference acceptance are recorded separately. No partial-cycle estimate.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--geometry', required=True, choices=['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4'])
parser.add_argument('--simulator', required=True, choices=['vcs', 'verilator'])
parser.add_argument('--revision', required=True)
parser.add_argument('--verilator-revision', default='v1')
args = parser.parse_args()
assert all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in [args.revision, args.verilator_revision])
image = 'ILSVRC2012_val_00000274.JPEG'
fixture = ROOT / 'work/full-rv32' / args.geometry / image
case = fixture if args.simulator == 'vcs' else ROOT / 'work/full-rv32-verilator' / args.geometry / image / args.verilator_revision
target = ROOT / 'work/completed-full-candidate' / args.geometry / args.revision
assert not target.exists(), 'Preserve previous review'
files = set()


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    files.add(path)
    return json.loads(path.read_text())


def verify(hashes):
    for name, value in hashes.items():
        assert sha(local(name)) == value, name


def completed(directory):
    summary = read(directory / 'summary.json')
    prep = read(directory / 'preparation.json')
    assert summary['status'] == 'FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN'
    assert summary['image'] == image and summary['full_RV32_executed']
    assert summary['all8_engines_participation_asserted'] and summary['logits_byte_exact']
    assert summary['source_sha256'] == prep['source_sha256']
    verify(prep['source_sha256'])
    report_path = directory / 'report.txt'
    report = report_path.read_text()
    assert report == summary['report']
    lines = re.findall(r'^RESULT (.+)$', report, re.M)
    assert len(lines) == 1
    result = {key: int(value, 16 if key in ['code', 'max_error_bits'] else 10)
              for key, value in re.findall(r'(\w+)=([0-9a-f]+)', lines[0])}
    assert result['code'] == 0x600dcafe and result['requested'] == result['completed'] == 4640
    assert result['node_events'] == 1312 and result['error_node'] == 0
    assert result['logit_mismatched_words'] == 0 and result['max_error_bits'] == 0
    assert result['top1'] == result['expected_top1'] == 607
    counters = re.findall(r'^ENGINE (\d+) commands=(\d+) cycles=(\d+) written_bytes=(\d+)$', report, re.M)
    assert len(counters) == 8
    engines = {int(op): dict(zip(['commands', 'cycles', 'written_bytes'], map(int, values)))
               for op, *values in counters}
    assert set(engines) == set(range(1, 9))
    assert all(min(engine.values()) > 0 for engine in engines.values())
    assert sum(engine['commands'] for engine in engines.values()) == 4640
    assert engines[3]['commands'] == 1877
    nodes = [(int(node), int(cycles)) for node, cycles in re.findall(r'^NODE (\d+) cycles=(\d+)$', report, re.M)]
    assert len(nodes) == len(set(node for node, _ in nodes)) == 1312 and nodes[-1][0] == 1395
    log_path = directory / 'normal.log'
    log = log_path.read_text(errors='replace')
    assert 'FULL_MODEL_RV32_PASS' in log and 'FULL_MODEL_CPU_RETURN_PASS' in log
    assert not re.search(r'(?i)Fatal:|Error-\[|FULL_MODEL_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
    events = [tuple(map(int, match)) for match in re.findall(
        r'^FULL_MODEL_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)$', log, re.M)]
    assert [(node, cycles) for node, cycles, _ in events] == nodes
    assert all(events[index][2] < events[index + 1][2] for index in range(len(events) - 1))
    cycles = result['inference_cycles']
    assert cycles > sum(engine['cycles'] for engine in engines.values()) > 0
    assert result['CPU_return_cycle'] > cycles > sum(cycles for _, cycles in nodes)
    logits_path = directory / 'logits.bin'
    logits = logits_path.read_bytes()
    reference = ROOT / 'work/fusion-proof' / (image + '.baseline')
    assert len(logits) == 4000 and logits == reference.read_bytes()
    assert all(math.isfinite(value) for value in struct.unpack('<1000f', logits))
    assert sha(logits_path) == summary['logits_sha256']
    files.update([report_path, log_path, logits_path, reference])
    return summary, prep, result, engines, events


summary, prep, result, engines, events = completed(case)
vcs_prep = read(fixture / 'preparation.json')
verify(vcs_prep['source_sha256'])
r, c, l = map(int, re.fullmatch(r'R(\d+)_C(\d+)_L(\d+)', args.geometry).groups())
assert summary['geometry'] == args.geometry
assert vcs_prep['geometry'] == {'rows': r, 'columns': c, 'lanes': l}
executable = case / ('simv' if args.simulator == 'vcs' else 'obj_dir/full_simv')
assert sha(executable) == summary['simulator_sha256']
assert all(command['returncode'] == 0 for command in summary['commands'])
if args.simulator == 'verilator':
    compile_proof = read(case / 'compile-summary.json')
    assert compile_proof['simulator_sha256'] == summary['simulator_sha256']
    assert compile_proof['preparation_sha256'] == sha(case / 'preparation.json')
    assert prep['RAM_image_sha256'] == sha(fixture / 'data.hex')
    assert prep['parameters'] == vcs_prep['parameters']
    assert prep['clock_period_ns'] == 10 and prep['RAM_response_delay_ns'] == 1
    live = read(case / 'live-process.json')
    assert live['status'] == 'TERMINAL' and live['returncode'] == 0

conditions = read(ROOT / 'work/full-soc-conditions-v1/summary.json')
assert conditions['status'] == 'THREE_COMPLETE_SOC_COMMON_BOUNDARY_LIBRARY_SDC_AND_STA_AUDIT_PASS'
verify(conditions['source_sha256'])
timing_revision = 'v8' if args.geometry == 'R8_C16_L4' else 'v1'
timing_path = ROOT / 'work/full-soc-timing' / args.geometry / 'shared' / timing_revision / 'summary.json'
timing = read(timing_path)
assert timing['status'] == 'FULL_ALL8_SOC_MAPPED_TIMING_PASS'
verify(timing['inputs_sha256'])
verify({str(timing_path.parent / name): value for name, value in timing['artifact_sha256'].items()})
control_case = ROOT / 'work/full-rv32/R8_C16_L4' / image
control_summary, control_prep, control_result, _, _ = completed(control_case)
assert sha(control_case / 'simv') == control_summary['simulator_sha256']
control_timing_path = ROOT / 'work/full-soc-timing/R8_C16_L4/shared/v8/summary.json'
control_timing = read(control_timing_path)
assert control_timing['status'] == 'FULL_ALL8_SOC_MAPPED_TIMING_PASS'
verify(control_timing['inputs_sha256'])
verify({str(control_timing_path.parent / name): value for name, value in control_timing['artifact_sha256'].items()})
for size in [30861636, 602112, 4000]:
    sections = [[item['sha256'] for item in fixture_prep['sections'] if item['bytes'] == size]
                for fixture_prep in [vcs_prep, control_prep]]
    assert len(sections[0]) == 1 and sections[0] == sections[1]
graph_path = ROOT / 'work/fused-plan/graph.bin'
graph = graph_path.read_bytes()
header = struct.unpack_from('<16I', graph)
assert header[:2] == (0x4e495753, 1) and header[3:5] == (1396, 77)
jobs = [struct.unpack_from('<12i', graph, header[9] + index * 48)[:4] for index in range(77)]
assert all(min(job) > 0 for job in jobs)
logical_macs = sum(b * m * n * k for b, m, n, k in jobs)
assert logical_macs == 4490566656
files.add(graph_path)
native_macs = r * c * l
dependency_path = ROOT / 'work/compile-dependency-closure-v1/summary.json'
dependency = read(dependency_path)
assert dependency['status'] == 'ACTUAL_VCS_DEPENDENCY_CURRENT_BYTE_IDENTITY_AND_RTL_SNAPSHOT_AUDIT_PASS'
verify(dependency['source_sha256'])
assert len(dependency['supplemental_dependencies']) == 5
assert any(local(record['case']) == fixture for record in dependency['cases'])
alternate = (fixture if args.simulator == 'verilator' else
             ROOT / 'work/full-rv32-verilator' / args.geometry / image / args.verilator_revision)
crosscheck = {'status': 'VCS_FULL_RESULT_PENDING' if args.simulator == 'verilator' else
              'FULL_VCS_COMPLETE_INDEPENDENT_FULL_VERILATOR_RUN_NOT_AVAILABLE',
              'complete_cycle_equality_accepted': False}
if (alternate / 'summary.json').exists():
    alternate_summary, _, alternate_result, alternate_engines, alternate_events = completed(alternate)
    alternate_executable = alternate / ('simv' if args.simulator == 'verilator' else 'obj_dir/full_simv')
    assert sha(alternate_executable) == alternate_summary['simulator_sha256']
    if args.simulator == 'vcs':
        alternate_compile = read(alternate / 'compile-summary.json')
        assert alternate_compile['preparation_sha256'] == sha(alternate / 'preparation.json')
        assert alternate_compile['simulator_sha256'] == alternate_summary['simulator_sha256']
    assert result == alternate_result and engines == alternate_engines and events == alternate_events
    assert (case / 'report.txt').read_bytes() == (alternate / 'report.txt').read_bytes()
    assert (case / 'logits.bin').read_bytes() == (alternate / 'logits.bin').read_bytes()
    crosscheck = {'status': 'FULL_VCS_VERILATOR_OUTPUT_REPORT_AND_ALL_NODE_CYCLES_IDENTICAL',
                  'complete_cycle_equality_accepted': True, 'node_events': len(events)}
review = {
    'status': 'COMPLETED_REAL_RV32_CANDIDATE_FROZEN_C_BYTE_EXACT_COMMON_PPA_REVIEW_PASS',
    'observed_at': datetime.now(timezone.utc).isoformat(), 'geometry': args.geometry,
    'simulator': args.simulator, 'image': image, 'source_files_checked': len(prep['source_sha256']),
    'supplemental_current_actual_dependency_files_checked': len(dependency['supplemental_dependencies']),
    'supplemental_dependency_review': str(dependency_path),
    'retroactive_compile_time_include_hash_claimed': False,
    'full_model_executed': True, 'inference_cycles': result['inference_cycles'],
    'CPU_return_cycle': result['CPU_return_cycle'], 'clock_period_ns': 10,
    'inference_seconds': result['inference_cycles'] * 1e-8,
    'all8_engine_counters': engines, 'logical_MACs': logical_macs, 'native_9bit_MAC_units': native_macs,
    'useful_MAC_utilization_during_GEMM_commands': logical_macs / (native_macs * engines[3]['cycles']),
    'useful_MAC_utilization_over_full_inference': logical_macs / (native_macs * result['inference_cycles']),
    'logits': {'words': 1000, 'all_finite': True, 'byte_exact_to_frozen_C': True,
               'top1': 607, 'maximum_absolute_error': 0, 'sha256': sha(case / 'logits.bin')},
    'complete_SoC_area': timing['area'], 'setup_slack_ns': timing['setup_slack_ns'],
    'hold_slack_ns': timing['hold_slack_ns'],
    'area_times_full_inference_ns': timing['area'] * result['inference_cycles'] * 10,
    'comparison_to_completed_R8_C16_control': {
        'control_cycles': control_result['inference_cycles'], 'control_area': control_timing['area'],
        'latency_ratio': result['inference_cycles'] / control_result['inference_cycles'],
        'area_ratio': timing['area'] / control_timing['area'],
        'ADP_ratio': timing['area'] * result['inference_cycles'] /
                     (control_timing['area'] * control_result['inference_cycles'])},
    'VCS_full_model_crosscheck': crosscheck,
    'official_accuracy_accepted': False, 'final_array_selection_accepted': False,
    'original_baseline_before_after_ADP_accepted': False,
    'scope': 'One complete real-RV32 image with frozen C byte equality and measured full-SoC PPA under independently reviewed common conditions. Cross-simulator agreement remains pending until both full runs complete. This control comparison is not an original16x16 before-after result,20-image acceptance,official accuracy,gate proof or final selection.'}
target.mkdir(parents=True)
(target / 'review_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'review_driver.py')
review['source_sha256'] = {str(path): sha(path) for path in sorted(files)}
(target / 'summary.json').write_text(json.dumps(review, indent=2) + '\n')
print(json.dumps({key: review[key] for key in ['status', 'inference_cycles', 'complete_SoC_area',
    'comparison_to_completed_R8_C16_control', 'VCS_full_model_crosscheck']}), flush=True)
