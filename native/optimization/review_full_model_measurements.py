"""Independent, read-only review of complete-model measurements.

Snapshot output describes incomplete runs without promoting partial cycles to
whole-model measurements. Organizer accuracy is deliberately a separate gate.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--snapshot', action='store_true')
parser.add_argument('--revision', required=True)
parser.add_argument('--image', default='ILSVRC2012_val_00000274.JPEG')
parser.add_argument('--baseline-backend', choices=['auto', 'verilator', 'vcs'], default='auto')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG', args.image)
target = ROOT / ('work/full-model-measurements-' + args.revision)
assert not target.exists(), 'Preserve prior review'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def verify(hashes):
    for name, value in hashes.items():
        assert sha(local(name)) == value, name


graph_path = ROOT / 'work/fused-plan/graph.bin'
graph = graph_path.read_bytes()
header = struct.unpack_from('<16I', graph)
assert header[0:2] == (0x4e495753, 1) and header[3:5] == (1396, 77) and header[14] == 1
jobs = [struct.unpack_from('<12i', graph, header[9] + n * 48)[:4] for n in range(header[4])]
assert all(min(job) > 0 for job in jobs)
logical_macs = sum(b * m * n * k for b, m, n, k in jobs)
assert sum(b for b, _, _, _ in jobs) == 1877
expected_path = ROOT / 'work/fusion-proof' / (args.image + '.baseline')
expected_bytes = expected_path.read_bytes()
expected = struct.unpack('<1000f', expected_bytes)
assert all(math.isfinite(v) for v in expected)
expected_top = max(range(1000), key=lambda i: expected[i])
internal_bound = 2e-6 * max(1.0, max(abs(v) for v in expected))
conditions = ROOT / 'work/full-soc-conditions-v1/summary.json'
condition_proof = json.loads(conditions.read_text())
assert condition_proof['status'] == 'THREE_COMPLETE_SOC_COMMON_BOUNDARY_LIBRARY_SDC_AND_STA_AUDIT_PASS'
verify(condition_proof['source_sha256'])
files = [Path(__file__), graph_path, expected_path, conditions]
rows, pending = [], []
common_data_sections = None


def data_sections(sections):
    result = []
    for size in [30861636, 602112, 4000]:
        matches = [section['sha256'] for section in sections if section['bytes'] == size]
        assert len(matches) == 1
        result.append((size, matches[0]))
    return result

# Link original area to the exact mapped design used by the accepted baseline
# gate test, rather than copying an area value from an exploratory table.
original_gate = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/work/soc_sdf.u63n3r7w'
gate_proof = json.loads((original_gate / 'summary.json').read_text())
assert gate_proof['status'] == 'PASS' and gate_proof['output_traces_match']
export_case = local(gate_proof['source_soc_sdf_export_case'])
export = json.loads((export_case / 'summary.json').read_text())
compile_case = local(export['source_soc_compile_case'])
original_synth = json.loads((compile_case / 'summary.json').read_text())
for directory in [export_case, compile_case]:
    key = '/workspace/' + str(directory.relative_to(BASE)) + '/summary.json'
    assert sha(directory / 'summary.json') == gate_proof['source_dependencies_sha256'][key]
    files.append(directory / 'summary.json')
assert export['status'] == 'PASS' and export['netlist_preservation']['exact_tokens_per_module']
assert original_synth['status'] == 'PASS' and original_synth['setup_and_hold_slack_nonnegative']
for name in ['area.rpt', 'soc_mapped.v']:
    assert sha(compile_case / 'dc' / name) == original_synth['output_sha256']['dc/' + name]
assert sha(export_case / 'dc/soc_mapped.v') == export['output_sha256']['dc/soc_mapped.v']
assert (export_case / 'dc/soc_mapped.v').read_bytes() == (original_gate / 'inputs/soc_mapped.v').read_bytes()
for name, value in condition_proof['libraries_sha256'].items():
    assert sha(compile_case / 'inputs' / name) == value, 'Original and candidate libraries differ'
area_path = compile_case / 'dc/area.rpt'
original_area = float(re.search(r'Total cell area:\s+([\d.]+)', area_path.read_text())[1])
assert original_area > 0
files.extend([original_gate / 'summary.json', area_path])


def logits_review(path):
    data = path.read_bytes()
    assert len(data) == 4000
    values = struct.unpack('<1000f', data)
    assert all(math.isfinite(v) for v in values)
    top = max(range(1000), key=lambda i: values[i])
    error = max(abs(v - ref) for v, ref in zip(values, expected))
    assert top == expected_top and error <= internal_bound
    files.append(path)
    return {'bytes': len(data), 'sha256': sha(path), 'byte_exact_to_frozen_C': data == expected_bytes,
            'top1': top, 'maximum_absolute_error': error,
            'existing_internal_C_bound': internal_bound, 'organizer_tolerance': None}


for geometry, timing_revision in [('R8_C16_L4', 'v8'), ('R8_C32_L4', 'v1'), ('R16_C8_L4', 'v1')]:
    case = ROOT / 'work/full-rv32' / geometry / args.image
    prep = json.loads((case / 'preparation.json').read_text())
    verify(prep['source_sha256'])
    assert any(section['sha256'] == sha(graph_path) for section in prep['sections'])
    assert prep['sections'][-1]['sha256'] == sha(expected_path)
    dataset = data_sections(prep['sections'])
    if common_data_sections is None:
        common_data_sections = dataset
    assert dataset == common_data_sections, 'Candidate constants/input/reference differ'
    files.append(case / 'preparation.json')
    timing_path = ROOT / 'work/full-soc-timing' / geometry / 'shared' / timing_revision / 'summary.json'
    timing = json.loads(timing_path.read_text())
    assert timing['status'] == 'FULL_ALL8_SOC_MAPPED_TIMING_PASS'
    verify(timing['inputs_sha256'])
    verify({str(timing_path.parent / name): value for name, value in timing['artifact_sha256'].items()})
    files.append(timing_path)
    r, c, l = map(int, re.fullmatch(r'R(\d+)_C(\d+)_L(\d+)', geometry).groups())
    peak = r * c * l
    tiles = sum(b * ((m + l - 1) // l) * ((n + c - 1) // c) * ((k + r - 1) // r)
                for b, m, n, k in jobs)
    item = {'geometry': geometry, 'logical_MACs': logical_macs,
            'native_9bit_MAC_units': peak, 'graph_derived_tiles_not_observed_counter': tiles,
            'shape_padding_efficiency': logical_macs / (tiles * peak),
            'area': timing['area'], 'setup_slack_ns': timing['setup_slack_ns'],
            'hold_slack_ns': timing['hold_slack_ns'], 'complete_model_measurement': False}
    summary_path = case / 'summary.json'
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        assert summary['status'] == 'FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN'
        assert summary['full_RV32_executed'] and summary['all8_engines_participation_asserted']
        assert summary['source_sha256'] == prep['source_sha256']
        assert sha(case / 'simv') == summary['simulator_sha256']
        report = (case / 'report.txt').read_text()
        assert report == summary['report']
        result = re.search(r'^RESULT (.+)$', report, re.M)
        assert result
        measured = {key: int(value, 16 if key in ['code', 'max_error_bits'] else 10)
                    for key, value in re.findall(r'(\w+)=([0-9a-f]+)', result[1])}
        assert measured['code'] == 0x600dcafe and measured['requested'] == measured['completed'] == 4640
        assert measured['node_events'] == 1312 and measured['error_node'] == 0
        engines = {int(op): {'commands': int(cmd), 'cycles': int(cycles), 'written_bytes': int(written)}
                   for op, cmd, cycles, written in re.findall(r'^ENGINE (\d+) commands=(\d+) cycles=(\d+) written_bytes=(\d+)$', report, re.M)}
        assert set(engines) == set(range(1, 9)) and all(e['commands'] and e['cycles'] for e in engines.values())
        assert engines[3]['commands'] == 1877 and sum(e['commands'] for e in engines.values()) == 4640
        nodes = [(int(index), int(cycles)) for index, cycles in re.findall(r'^NODE (\d+) cycles=(\d+)$', report, re.M)]
        assert len(nodes) == len(set(index for index, _ in nodes)) == 1312 and nodes[-1][0] == 1395
        total = measured['inference_cycles']
        assert total > sum(e['cycles'] for e in engines.values()) > 0
        assert measured['CPU_return_cycle'] > total and sum(v for _, v in nodes) < total
        item.update(complete_model_measurement=True, inference_cycles=total, inference_ns=total * 10,
                    CPU_return_cycle=measured['CPU_return_cycle'], engine_counters=engines,
                    useful_MAC_utilization_during_GEMM_commands=logical_macs / (peak * engines[3]['cycles']),
                    useful_MAC_utilization_over_full_inference=logical_macs / (peak * total),
                    area_times_full_inference_ns=timing['area'] * total * 10,
                    logits=logits_review(case / 'logits.bin'),
                    most_expensive_node_regions=sorted(nodes, key=lambda pair: pair[1], reverse=True)[:12])
        assert 0 < item['useful_MAC_utilization_over_full_inference'] <= item['useful_MAC_utilization_during_GEMM_commands'] <= 1
        files.extend([summary_path, case / 'report.txt'])
    else:
        pending.append(geometry + ': full-model summary absent')
        # A partial heartbeat is explicitly not an end-to-end measurement.
        log = (case / 'normal.log').read_text(errors='replace')
        progress = re.findall(r'FULL_MODEL_PROGRESS CPU_cycle=(\d+) commands=(\d+) completed=(\d+) last_node=(-?\d+)', log)
        item['last_observed_partial_heartbeat'] = dict(zip(['CPU_cycle', 'commands', 'completed', 'last_node'], map(int, progress[-1]))) if progress else None
    rows.append(item)

legacy_case = ROOT / 'work/original-baseline-rtl/verilator/v1'
vcs_case = ROOT / 'work/original-baseline-full-vcs/v1'
use_vcs = args.baseline_backend == 'vcs' or (args.baseline_backend == 'auto' and
          ((vcs_case / 'summary.json').exists() or not (legacy_case / 'full-summary.json').exists()))
baseline_case = vcs_case if use_vcs else legacy_case
baseline_prep = json.loads((baseline_case / 'preparation.json').read_text())
verify(baseline_prep['source_sha256'])
if use_vcs:
    sys.path.insert(0, str(BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project/scripts'))
    from run_pe_axi_sdf import compiled_runtime_files
    assert baseline_prep['status'] == 'ORIGINAL_FULL_VCS_ARCHIVED_INPUTS_RUNTIME_FROZEN_EXECUTION_PENDING'
    assert baseline_prep['image'] == args.image and baseline_prep['actual_original_RTL_used']
    assert baseline_prep['clock_period_ns'] == 10 and baseline_prep['RAM_response_delay_ns'] == 1
    baseline_executable = local(baseline_prep['compiled_executable'])
    assert sha(baseline_executable) == baseline_prep['simulator_sha256']
    assert compiled_runtime_files(local(baseline_prep['template'])) == baseline_prep['runtime_files']
    recorded_archives = {str(local(name)): value for name, value in baseline_prep['source_sha256'].items()}
    for item in baseline_prep['original_source_archive_map'].values():
        archived = baseline_case / item['archive']
        assert archived.resolve().is_relative_to((baseline_case / 'input-snapshot').resolve())
        assert recorded_archives[str(archived)] == item['sha256']
    archived_fw = baseline_case / 'input-snapshot/native/optimization/work/original-baseline-full' / args.image / 'preparation.json'
    firmware_prep = json.loads(archived_fw.read_text())
    baseline_summary = baseline_case / 'summary.json'
    baseline_report = baseline_case / 'report.txt'
    baseline_logits = baseline_case / 'logits.bin'
    files.extend([archived_fw, BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project/scripts/run_pe_axi_sdf.py'])
else:
    baseline_compile = json.loads((baseline_case / 'compile-summary.json').read_text())
    assert sha(baseline_case / 'preparation.json') == baseline_compile['preparation_sha256']
    baseline_executable = baseline_case / 'obj_dir/original_full_simv'
    assert sha(baseline_executable) == baseline_compile['simulator_sha256']
    firmware_prep = json.loads((local(baseline_prep['firmware']) / 'preparation.json').read_text())
    baseline_summary = baseline_case / 'full-summary.json'
    baseline_report = baseline_case / 'full-report.txt'
    baseline_logits = baseline_case / 'full-logits.bin'
    files.append(baseline_case / 'compile-summary.json')
assert firmware_prep['image'] == args.image and data_sections(firmware_prep['sections']) == common_data_sections
files.append(baseline_case / 'preparation.json')
baseline = {'complete_model_measurement': False, 'full_inference_cycles': None,
            'area': original_area, 'area_provenance': str(area_path),
            'selected_evidence_backend': 'original_raw_VCS_archived' if use_vcs else 'qualified_original_Verilator',
            'case': str(baseline_case), 'constants_input_and_reference_equal_to_candidates': True,
            'same_libraries_and_original_gate_netlist_link_verified': True}
if baseline_summary.exists():
    original = json.loads(baseline_summary.read_text())
    assert original['status'] == 'ORIGINAL_CPU_PE_FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN'
    assert original['full_model_accepted'] and original['image'] == args.image
    verify(original['source_sha256'])
    assert sha(baseline_executable) == original['simulator_sha256']
    if use_vcs:
        assert original['source_archive_verified'] and original['runtime_files_unchanged']
        assert original['source_sha256'] == baseline_prep['source_sha256']
        assert original['runtime_files'] == baseline_prep['runtime_files']
    report = baseline_report.read_text()
    assert report == original['report'] and 'code=600dcafe' in report
    measured = re.search(r'^RESULT (.+)$', report, re.M)
    assert measured
    counters = {key: int(value, 16 if key in ['code', 'max_error_bits'] else 10)
                for key, value in re.findall(r'(\w+)=([0-9a-f]+)', measured[1])}
    assert counters['node_events'] == 1396 and counters['error_node'] == 0
    assert counters['PE_AW'] == counters['PE_W'] == counters['PE_B'] == 181690705
    assert counters['PE_AR'] == counters['PE_R'] == 1148252160
    node_ids = [int(index) for index in re.findall(r'^NODE (\d+) cycles=\d+$', report, re.M)]
    assert node_ids == list(range(1396))
    cycles = re.search(r'^RESULT .*inference_cycles=(\d+)', report, re.M)
    assert cycles and int(cycles[1]) > 0
    baseline.update(complete_model_measurement=True, full_inference_cycles=int(cycles[1]),
                    logits=logits_review(baseline_logits))
    baseline['area_times_full_inference_ns'] = original_area * int(cycles[1]) * 10
    for item in rows:
        if item['complete_model_measurement']:
            item['engineering_speedup_over_original'] = int(cycles[1]) / item['inference_cycles']
            item['engineering_ADP_ratio_to_original'] = item['area_times_full_inference_ns'] / baseline['area_times_full_inference_ns']
    files.extend([baseline_summary, baseline_report])
else:
    pending.append('original baseline: full-model summary absent')
if not args.snapshot:
    assert not pending, '\n'.join(pending)
target.mkdir()
result = {'status': 'PARTIAL_FULL_MODEL_EVIDENCE_SNAPSHOT_NOT_PERFORMANCE_ACCEPTANCE' if pending else 'COMPLETE_SINGLE_IMAGE_ENGINEERING_MEASUREMENTS_OFFICIAL_ACCURACY_OPEN',
          'observed_at': datetime.now(timezone.utc).isoformat(), 'image': args.image,
          'candidates': rows, 'original_baseline': baseline, 'pending': pending,
          'official_accuracy_accepted': False, 'final_ADP_selection_accepted': False,
          'definitions': {'shape_padding_efficiency': 'Graph useful MACs / padded tile MAC slots; a shape property, not measured utilization.',
             'useful_MAC_utilization_during_GEMM_commands': 'Graph useful MACs / (physical native9bit MAC units * actual completed GEMM-command RTL cycles), includes DMA/control/wait.',
             'useful_MAC_utilization_over_full_inference': 'Graph useful MACs / (physical native9bit MAC units * actual full RV32 inference cycles), includes nonMAC/software/movement/output.',
             'area_times_full_inference_ns': 'Engineering area-delay product only. Organizer accuracy and required final gate/delivery checks remain separate gates.'},
          'source_sha256': {str(path): sha(path) for path in files},
          'scope': 'Read-only review. All source guards checked even in snapshot mode. No partial-cycle extrapolation, simulator wall time used as hardware latency, inferred official tolerance, or final best-candidate claim.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'pending': pending}), flush=True)
