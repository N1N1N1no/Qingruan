"""Wait for one actual full-RV32 run, then independently review its full PPA.

The completed scalar controls exercise the same report/logit parser first.
No missing-result retry, simulation launch, gate acceptance or official score.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
parser.add_argument('--prior-pid', required=True, type=int)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision) and args.prior_pid > 1
TARGET = ROOT / ('work/vecpipe-complete-candidate-review-' + args.revision)
CASE = ROOT / 'work/vecpipe-full-rv32-v1'
IMAGE = 'ILSVRC2012_val_00000274.JPEG'
assert not TARGET.exists(), 'Preserve prior reviews'
TARGET.mkdir()
guarded = {}


def local(value):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if value.startswith(prefix):
            return BASE / value[len(prefix):]
    return Path(value)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def guard(path, expected=None):
    path = local(str(path))
    current = guarded.get(str(path)) or sha(path)
    assert expected is None or current == expected, str(path)
    guarded[str(path)] = current
    return current


def verify(hashes):
    for name, expected in hashes.items():
        guard(local(name), expected)


def read(path):
    guard(path)
    return json.loads(path.read_text())


def proof(path, expected_status):
    data = read(path)
    assert data['status'] == expected_status, str(path)
    verify(data.get('source_sha256', {}))
    verify(data.get('inputs_sha256', {}))
    verify({str(path.parent / name): value for name, value in data.get('artifact_sha256', {}).items()})
    return data


def save(name, value):
    temporary = TARGET / (name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(TARGET / name)


def identity(pid):
    try:
        fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        os.kill(pid, 0)
        return fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def completed(directory, pipeline=False):
    summary = read(directory / 'summary.json')
    prep = read(directory / 'preparation.json')
    expected = ('VECPIPE_REAL_RV32_FULL_FIRST_IMAGE_BYTE_EXACT_PASS_OFFICIAL_ACCURACY_OPEN' if pipeline else
                'FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN')
    assert summary['status'] == expected and summary['image'] == IMAGE and summary['full_RV32_executed']
    if pipeline:
        assert summary['logits_byte_exact_to_completed_scalar_and_frozen_C']
        assert all(summary['source_sha256'][name] == value for name, value in prep['source_sha256'].items())
    else:
        assert summary['all8_engines_participation_asserted'] and summary['logits_byte_exact']
        assert summary['source_sha256'] == prep['source_sha256']
    verify(summary['source_sha256'])
    verify(prep['source_sha256'])
    assert sha(directory / 'simv') == summary['simulator_sha256']
    guard(directory / 'simv')
    assert all(command['returncode'] == 0 for command in summary['commands'])
    report_path = directory / 'report.txt'
    report = report_path.read_text()
    guard(report_path)
    assert report == summary['report']
    lines = re.findall(r'^RESULT (.+)$', report, re.M)
    assert len(lines) == 1
    result = {key: int(value, 16 if key in ['code', 'max_error_bits'] else 10)
              for key, value in re.findall(r'(\w+)=([0-9a-f]+)', lines[0])}
    assert result['code'] == 0x600dcafe and result['requested'] == result['completed'] == 4640
    assert result['node_events'] == 1312 and result['error_node'] == 0
    assert result['logit_mismatched_words'] == result['max_error_bits'] == 0
    assert result['top1'] == result['expected_top1'] == 607
    counters = re.findall(r'^ENGINE (\d+) commands=(\d+) cycles=(\d+) written_bytes=(\d+)$', report, re.M)
    assert len(counters) == 8
    engines = {int(op): dict(zip(['commands', 'cycles', 'written_bytes'], map(int, values))) for op, *values in counters}
    assert set(engines) == set(range(1, 9)) and all(min(item.values()) > 0 for item in engines.values())
    assert sum(item['commands'] for item in engines.values()) == 4640 and engines[3]['commands'] == 1877
    nodes = [tuple(map(int, pair)) for pair in re.findall(r'^NODE (\d+) cycles=(\d+)$', report, re.M)]
    assert len(nodes) == len({node for node, _ in nodes}) == 1312 and nodes[-1][0] == 1395
    log_path = directory / 'normal.log'
    log = log_path.read_text(errors='replace')
    guard(log_path)
    assert log.count('FULL_MODEL_RV32_PASS') == log.count('FULL_MODEL_CPU_RETURN_PASS') == 1
    assert not re.search(r'(?i)Fatal:|Error-\[|FULL_MODEL_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|VECPIPE_FIFO_FAIL', log)
    events = [tuple(map(int, values)) for values in re.findall(
        r'^FULL_MODEL_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)$', log, re.M)]
    assert [(node, cycles) for node, cycles, _ in events] == nodes
    assert all(events[i][2] < events[i + 1][2] for i in range(len(events) - 1))
    assert result['inference_cycles'] > sum(item['cycles'] for item in engines.values()) > 0
    assert result['CPU_return_cycle'] > result['inference_cycles'] > sum(cycles for _, cycles in nodes)
    output = directory / 'logits.bin'
    reference = ROOT / 'work/fusion-proof' / (IMAGE + '.baseline')
    logits = output.read_bytes()
    assert len(logits) == 4000 and logits == reference.read_bytes()
    assert all(math.isfinite(value) for value in struct.unpack('<1000f', logits))
    assert guard(output) == summary['logits_sha256']
    guard(reference)
    return {'summary': summary, 'prep': prep, 'result': result, 'engines': engines, 'events': events}


try:
    guard(Path(__file__))
    conditions = proof(ROOT / 'work/vecpipe-soc-conditions-v2/summary.json',
        'VECPIPE_COMPLETE_SOC_BOUNDARY_LIBRARY_SDC_PHYSICAL_SRAM_AND_STA_AUDIT_PASS')
    timing = proof(ROOT / 'work/vecpipe-full-soc-loads-v1/summary.json',
        'VECPIPE_FULL_ALL8_SOC_SDF_LOAD_REPAIR_PASS')
    binding = proof(ROOT / 'work/vecpipe-synthesis-binding-v1/summary.json',
        'VECPIPE_BENCHMARK_RTL_AND_SYNTHESIS_INPUT_LINEAGE_AUDIT_PASS')
    dependencies = proof(ROOT / 'work/compile-dependency-closure-v1/summary.json',
        'ACTUAL_VCS_DEPENDENCY_CURRENT_BYTE_IDENTITY_AND_RTL_SNAPSHOT_AUDIT_PASS')
    assert len(dependencies['supplemental_dependencies']) == 5
    assert any(local(item['case']) == CASE for item in dependencies['cases'])
    assert local(binding['benchmark_case']) == CASE
    assert conditions['same_libraries_as_all_three_original_candidates']
    assert conditions['same_complete_ordered_SDC_commands_except_four_independent_transitions']
    assert conditions['same_original_baseline_top_port_directions_widths']
    assert conditions['report_constraint_has_no_violators']
    assert conditions['physical_SRAMs'] == timing['SRAMs'] == 128
    assert timing['clock_period_ns'] == conditions['clock_period_ns'] == 10
    assert timing['area'] == conditions['area'] == binding['area'] == 2114649.956611
    assert timing['setup_slack_ns'] >= 0 and timing['hold_slack_ns'] >= 0
    assert timing['normalized_SDC_identical_to_source']
    assert timing['iopath_scan']['negative_IOPATH_entries_any_triplet_field'] == 0
    controls = {}
    for geometry, timing_revision in [('R8_C16_L4', 'v8'), ('R8_C32_L4', 'v1'), ('R16_C8_L4', 'v1')]:
        directory = ROOT / 'work/full-rv32' / geometry / IMAGE
        controls[geometry] = completed(directory)
        controls[geometry]['timing'] = proof(ROOT / 'work/full-soc-timing' / geometry / 'shared' / timing_revision / 'summary.json',
            'FULL_ALL8_SOC_MAPPED_TIMING_PASS')
    baseline = controls['R8_C16_L4']
    preparation = read(CASE / 'preparation.json')
    verify(preparation['source_sha256'])
    assert preparation['clock_period_ns'] == 10 and preparation['RAM_response_delay_ns'] == 1
    assert preparation['SRAM_macros'] == 128 and preparation['geometry'] == 'R8_C16_L4'
    assert preparation['parameters'] == baseline['prep']['parameters']
    assert preparation['sections'] == baseline['prep']['sections']
    assert all(preparation['unchanged_fixture_files_byte_exact'].values())
    for name in ['program.elf', 'program.bin', 'program.hex', 'data.hex', 'tb_soc.sv', 'axi_ram_model.sv']:
        assert guard(CASE / name) == guard(ROOT / 'work/full-rv32/R8_C16_L4' / IMAGE / name)
    graph_path = ROOT / 'work/fused-plan/graph.bin'
    graph = graph_path.read_bytes()
    guard(graph_path)
    header = struct.unpack_from('<16I', graph)
    assert header[:2] == (0x4e495753, 1) and header[3:5] == (1396, 77)
    jobs = [struct.unpack_from('<12i', graph, header[9] + i * 48)[:4] for i in range(77)]
    logical_macs = sum(b * m * n * k for b, m, n, k in jobs)
    assert logical_macs == 4490566656
    prior_identity = identity(args.prior_pid)
    if prior_identity is not None:
        assert b'run_full_model.py' in Path('/proc/%d/cmdline' % args.prior_pid).read_bytes()
    save('preflight.json', {'status': 'THREE_COMPLETE_CONTROLS_AND_FINAL_VECPIPE_PPA_REVIEWED_FULL_RESULT_PENDING',
         'source_sha256': guarded, 'waiting_for_pid': args.prior_pid, 'process_start_ticks': prior_identity,
         'control_parser_exercised_complete_images': 3, 'full_pipeline_result_reviewed': False,
         'new_simulation_started': False, 'official_accuracy_accepted': False})
    guard(TARGET / 'preflight.json')
    started = time.monotonic()
    while prior_identity is not None and identity(args.prior_pid) == prior_identity:
        save('live-process.json', {'status': 'WAITING_FOR_VERIFIED_FULL_MODEL_PROCESS',
             'pid': args.prior_pid, 'start_ticks': prior_identity})
        print('VECPIPE_COMPLETE_REVIEW_WAIT pid=%d elapsed_s=%d' %
              (args.prior_pid, time.monotonic() - started), flush=True)
        if time.monotonic() - started > 86400:
            raise TimeoutError('Full-model process still live; left untouched')
        time.sleep(30)
    assert not (CASE / 'failure.json').exists(), 'Full-model diagnostic failure; preserve it'
    current = completed(CASE, pipeline=True)
    assert current['prep'] == preparation
    assert [event[0] for event in current['events']] == [event[0] for event in baseline['events']]
    for op in range(1, 9):
        assert current['engines'][op]['commands'] == baseline['engines'][op]['commands']
        assert current['engines'][op]['written_bytes'] == baseline['engines'][op]['written_bytes']
        if op != 7:
            assert current['engines'][op]['cycles'] == baseline['engines'][op]['cycles'], op
    result = current['result']
    measured = []
    for geometry, item in controls.items():
        measured.append({'candidate': geometry + '/scalar', 'area': item['timing']['area'],
                         'cycles': item['result']['inference_cycles']})
    measured.append({'candidate': 'R8_C16_L4/vecpipe_v1', 'area': timing['area'], 'cycles': result['inference_cycles']})
    reference_adp = baseline['timing']['area'] * baseline['result']['inference_cycles']
    for item in measured:
        item['inference_seconds_at10ns'] = item['cycles'] * 1e-8
        item['area_times_inference_ns'] = item['area'] * item['cycles'] * 10
        item['ADP_ratio_to_scalar_R8_C16'] = item['area'] * item['cycles'] / reference_adp
    measured.sort(key=lambda item: item['area_times_inference_ns'])
    for name, expected in guarded.items():
        assert sha(Path(name)) == expected, name
    save('summary.json', {'status': 'VECPIPE_COMPLETE_FIRST_IMAGE_BYTE_EXACT_FINAL_PPA_ENGINEERING_REVIEW_PASS',
         'observed_at': datetime.now(timezone.utc).isoformat(), 'image': IMAGE,
         'full_RV32_executed': True, 'node_events': 1312, 'commands': 4640,
         'inference_cycles': result['inference_cycles'], 'CPU_return_cycle': result['CPU_return_cycle'],
         'clock_period_ns': 10, 'RAM_response_delay_ns': 1, 'SRAMs': 128,
         'complete_SoC_area': timing['area'], 'setup_slack_ns': timing['setup_slack_ns'],
         'hold_slack_ns': timing['hold_slack_ns'], 'engine_counters': current['engines'],
         'non_vector_engine_cycles_identical_to_scalar_control': True,
         'logits_words': 1000, 'logits_all_finite_and_byte_exact_to_frozen_C': True,
         'logits_sha256': sha(CASE / 'logits.bin'), 'top1': 607, 'max_absolute_error': 0,
         'logical_MACs': logical_macs, 'native_9bit_MAC_units': 512,
         'useful_MAC_utilization_over_full_inference': logical_macs / (512 * result['inference_cycles']),
         'same_first_image_engineering_ADP_ranking': measured,
         'source_sha256': guarded, 'supplemental_current_include_dependencies_checked': 5,
         'retroactive_compile_time_include_hash_claimed': False, 'formal_equivalence_claimed': False,
         'full_independent_Verilator_result_accepted': False, 'gate_function_accepted_by_this_review': False,
         'dataset20_accepted': False, 'official_accuracy_accepted': False,
         'original_full_before_after_ADP_accepted': False, 'final_selection_accepted': False,
         'scope': 'Complete first-image VCS actual RV32 execution, exact outputs/counters and independently bound final PPA. Engineering comparison of four completed candidates only; final20-image, gate, organizer accuracy, original baseline and delivery gates remain separate.'})
    save('live-process.json', {'status': 'TERMINAL_REVIEW_PASS', 'new_simulation_started': False})
    print('VECPIPE_COMPLETE_FIRST_IMAGE_BYTE_EXACT_FINAL_PPA_ENGINEERING_REVIEW_PASS', flush=True)
except BaseException as error:
    save('failure.json', {'status': 'VECPIPE_COMPLETE_CANDIDATE_REVIEW_DIAGNOSTIC_FAILURE',
         'error': repr(error), 'source_sha256': guarded, 'new_simulation_started': False,
         'official_accuracy_accepted': False, 'final_selection_accepted': False})
    raise
