"""Qualify documented VCS +rad in isolated short cases before any adoption.

Original full runs, RTL, firmware and references are untouched. Complete3tensor
CPU/PE tests, their negative control and20Mcycle prefixes are not full inference.
"""
import argparse
import ast
from datetime import datetime, timezone
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

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / ('work/original-vcs-radiant-' + args.revision)
assert not case.exists(), 'Preserve prior attempt'
template = ROOT / 'work/original-baseline-rtl/vcs/v2'
micro = BASE / 'native/swin-tiny/work/rtl.3692ulcq'
records = []


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(hashes):
    for name, value in hashes.items():
        assert sha(local(name)) == value, name


def save(name, value):
    (case / name).write_text(json.dumps(value, indent=2) + '\n')


def checked(path):
    data = json.loads(path.read_text())
    verify(data.get('source_sha256', {}))
    return data


prep = checked(template / 'preparation.json')
compiled = checked(template / 'compile-summary.json')
assert compiled['preparation_sha256'] == sha(template / 'preparation.json')
assert compiled['simulator_sha256'] == sha(template / 'simv')
micro_proof = checked(micro / 'summary.json')
assert micro_proof['status'] == 'PASS' and micro_proof['CPU_results'] == 75
assert micro_proof['negative_control_detected'] and micro_proof['expected_MMIO_writes'] == 3705
assert sha(micro / 'simv') == micro_proof['simulator_sha256']
sys.path.insert(0, str(TASK / 'my_design/project/scripts'))
from run_pe_axi_sdf import compiled_runtime_files
original_runtime = compiled_runtime_files(template)
case.mkdir()
(case / 'micro').mkdir()
old_micro_compile = next(item['argv'] for item in micro_proof['commands'] if '-sverilog' in item['argv'])
old_full_compile = next(item['argv'] for item in compiled['commands'] if item['phase'] == 'compile')
assert '-debug_access' in old_micro_compile and '-debug_access' not in old_full_compile
original_tb = local(next(value for value in old_micro_compile if value.endswith('/tb_soc.sv')))
source_runner = ROOT / 'run_original_baseline_vcs.py'
wave_assignments = [node for node in ast.walk(ast.parse(source_runner.read_text()))
                    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'optional_wave' for target in node.targets)]
assert len(wave_assignments) == 1
wave = ast.literal_eval(wave_assignments[0].value)
tb = original_tb.read_text()
assert tb.count(wave) == 1
new_tb = case / 'micro/tb_soc.sv'
new_tb.write_text(tb.replace(wave, ''))
hashes = dict(prep['source_sha256'])
hashes.update(micro_proof['source_sha256'])
extra = [Path(__file__), source_runner, template / 'preparation.json', template / 'compile-summary.json',
         micro / 'summary.json', original_tb, new_tb, micro / 'good.hex', micro / 'bad.hex',
         micro / 'good_trace.tsv', micro / 'bad_trace.tsv',
         ROOT / 'work/vcs-optimization-help-v1/help-full64.txt',
         template / 'prefix-20000000-report.txt', template / 'prefix-20000000-command.json']
hashes.update({str(path): sha(path) for path in extra})
save('preparation.json', {'status': 'ISOLATED_DOCUMENTED_RADIANT_CHECK_PREPARED',
    'source_sha256': hashes, 'original_runtime': original_runtime,
    'only_micro_TB_change': 'Remove the exact unused WAVE branch, identical to the qualified original no-debug baseline adapter.',
    'WAVE_requested': False, 'new_HDL_or_firmware_behavior': False,
    'full_model_accepted': False, 'official_accuracy_accepted': False})


def run(argv, label, directory, timeout):
    argv = list(map(str, argv))
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / (label + '.log')
    assert not log_path.exists()
    assert '+WAVE' not in argv
    print('RADIANT_CHECK_PHASE ' + label, flush=True)
    started = time.monotonic()
    def limit():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    with log_path.open('w') as stream:
        child = subprocess.Popen(argv, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=limit)
        save('live-process.json', {'status': 'RUNNING', 'phase': label, 'pid': child.pid, 'argv': argv})
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('RADIANT_CHECK_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(label)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
            save('live-process.json', {'status': 'TERMINAL', 'phase': label, 'returncode': child.returncode})
    record = {'phase': label, 'argv': argv, 'returncode': code,
              'elapsed_s': time.monotonic() - started, 'log': str(log_path)}
    records.append(record)
    save('commands.json', records)
    assert code == 0, str(log_path)
    return record, log_path.read_text(errors='replace')


def review_micro_log(kind, log):
    assert not re.search(r'(?i)Error-\[|SWIN_DRIVER_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
    if kind == 'good':
        assert 'Fatal:' not in log and 'SWIN_MONITOR_FAIL' not in log
        assert micro_proof['normal_monitor'] in log
    else:
        reference = (micro / 'bad_simulation.log').read_text()
        pattern = r'Fatal: "([^"]+)", (\d+): (\S+): at time (\d+) (\w+)'
        observed = re.findall(pattern, log)
        expected = re.findall(pattern, reference)
        assert len(observed) == len(expected) == log.count('Fatal:') == 1
        assert observed == expected
        lines = re.findall(r'^SWIN_MONITOR_FAIL arithmetic .+$', log, re.M)
        assert len(lines) == 1 and lines == re.findall(r'^SWIN_MONITOR_FAIL arithmetic .+$', reference, re.M)
        assert log.count('SWIN_MONITOR_FAIL') == 1


prior = ROOT / 'work/original-vcs-radiant-v1'
prior_failure = json.loads((prior / 'failure.json').read_text())
assert prior_failure['error'] == 'AssertionError()'
assert [(item['phase'], item['returncode']) for item in prior_failure['commands']] == [
    ('compat', 0), ('compile-baseline', 0), ('baseline-good', 0), ('baseline-bad', 0)]
verify(prior_failure['source_sha256'])
reused_baseline = []
for kind in ['good', 'bad']:
    log_path = prior / 'micro/baseline' / ('baseline-' + kind + '.log')
    trace_path = prior / 'micro/baseline' / (kind + '.tsv')
    review_micro_log(kind, log_path.read_text())
    assert trace_path.read_bytes() == (micro / (kind + '_trace.tsv')).read_bytes()
    command = next(item for item in prior_failure['commands'] if item['phase'] == 'baseline-' + kind)
    reused_baseline.append({'mode': 'baseline', 'kind': kind, 'trace_sha256': sha(trace_path),
                           'host_wall_s': command['elapsed_s'], 'trace_and_cycles_byte_exact': True,
                           'reused_from_preserved_first_attempt': True})
    hashes[str(log_path)] = sha(log_path)
    hashes[str(trace_path)] = sha(trace_path)
for path in [prior / 'preparation.json', prior / 'failure.json', prior / 'reviewer-diagnostic.json',
             prior / 'driver-source-v1.py']:
    hashes[str(path)] = sha(path)
save('preparation.json', {'status': 'ISOLATED_DOCUMENTED_RADIANT_CHECK_PREPARED_WITH_CORRECTED_NEGATIVE_REVIEW',
    'source_sha256': hashes, 'original_runtime': original_runtime,
    'intentional_negative_scope': 'One original monitor arithmetic fatal at the exact original file,line,time and mismatch body; other diagnostics rejected.',
    'prior_baseline_reused': reused_baseline, 'CPU_negative_detection_claimed': False,
    'full_model_accepted': False, 'official_accuracy_accepted': False})


try:
    common = case / 'common'
    common.mkdir()
    compat = common / 'compat.o'
    run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c', '-o', compat],
        'compat', common, 120)
    micro_results = list(reused_baseline)
    for mode in ['radiant']:
        directory = case / 'micro' / mode
        argv = []
        for value in old_micro_compile:
            if value == '-debug_access':
                continue
            if value.startswith('-Mdir='):
                argv.append('-Mdir=' + str(directory / 'csrc'))
            elif local(value) == original_tb:
                argv.append(str(new_tb))
            elif value == str(micro / 'simv') or local(value) == micro / 'simv':
                argv.append(str(directory / 'simv'))
            elif local(value) == micro / 'compat.o':
                argv.append(str(compat))
            else:
                argv.append(value)
        if mode == 'radiant':
            argv.append('+rad')
        run(argv, 'compile-' + mode, directory, 1800)
        for kind, selector in [('good', '/good.hex'), ('bad', '/bad.hex')]:
            original = next(item['argv'] for item in micro_proof['commands']
                            if any(value.endswith(selector) for value in item['argv']) and item['argv'][0].endswith('/simv'))
            sim_args = [str(directory / 'simv')]
            for value in original[1:]:
                sim_args.append('+TRACE=' + str(directory / (kind + '.tsv')) if value.startswith('+TRACE=') else value)
            record, log = run(sim_args, mode + '-' + kind, directory, 1800)
            review_micro_log(kind, log)
            assert (directory / (kind + '.tsv')).read_bytes() == (micro / (kind + '_trace.tsv')).read_bytes()
            micro_results.append({'mode': mode, 'kind': kind, 'trace_sha256': sha(directory / (kind + '.tsv')),
                                  'host_wall_s': record['elapsed_s'], 'trace_and_cycles_byte_exact': True})
    verify(hashes)
    verify(prior_failure['source_sha256'])
    save('micro-qualification.json', {'status': 'ORIGINAL_CPU_PE_THREE_TENSORS_AND_NEGATIVE_RADIANT_TRACES_EXACT_PASS',
        'CPU_results': 75, 'MMIO_writes': 3705, 'MMIO_reads': 5376, 'cases': micro_results,
        'intentional_negative_detected_by_original_monitor': True, 'CPU_negative_detection_claimed': False,
        'full_model_accepted': False, 'hardware_performance_accepted': False})
    full = case / 'full/radiant'
    full_compile = []
    for value in old_full_compile:
        if value.startswith('-Mdir='):
            full_compile.append('-Mdir=' + str(full / 'csrc'))
        elif local(value) == template / 'simv':
            full_compile.append(str(full / 'simv'))
        elif local(value) == template / 'compat.o':
            full_compile.append(str(compat))
        else:
            full_compile.append(value)
    full_compile.append('+rad')
    run(full_compile, 'compile-full-radiant', full, 1800)
    prefix_command = json.loads((template / 'prefix-20000000-command.json').read_text())['argv']
    reference_report = (template / 'prefix-20000000-report.txt').read_bytes()
    reference_log = (template / 'prefix-20000000.log').read_text()
    node_pattern = r'ORIGINAL_BASELINE_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)'
    reference_events = re.findall(node_pattern, reference_log)
    assert len(reference_events) == 385
    samples = []
    for round_number in [1, 2]:
        order = ['baseline', 'radiant'] if round_number == 1 else ['radiant', 'baseline']
        measured = {}
        for mode in order:
            directory = case / 'prefix' / ('round%d-' % round_number + mode)
            executable = template / 'simv' if mode == 'baseline' else full / 'simv'
            argv = []
            for value in prefix_command:
                if local(value) == template / 'simv':
                    argv.append(str(executable))
                elif value.startswith('+REPORT='):
                    argv.append('+REPORT=' + str(directory / 'report.txt'))
                elif value.startswith('+LOGITS='):
                    argv.append('+LOGITS=' + str(directory / 'logits.bin'))
                else:
                    argv.append(value)
            record, log = run(argv, 'prefix-%d-%s' % (round_number, mode), directory, 3600)
            assert 'ORIGINAL_BASELINE_PREFIX_ONLY CPU_cycle=20000000' in log
            assert not re.search(r'(?i)Fatal:|Error-\[|ORIGINAL_BASELINE_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
            assert re.findall(node_pattern, log) == reference_events
            assert (directory / 'report.txt').read_bytes() == reference_report
            measured[mode] = record['elapsed_s']
        samples.append({'round': round_number, 'baseline_wall_s': measured['baseline'],
                        'radiant_wall_s': measured['radiant'],
                        'observed_host_speedup': measured['baseline'] / measured['radiant']})
        save('prefix-samples.json', samples)
        if samples[-1]['observed_host_speedup'] < 1.15:
            break
    verify(hashes)
    verify(prior_failure['source_sha256'])
    assert compiled_runtime_files(template) == original_runtime
    eligible = len(samples) == 2 and min(sample['observed_host_speedup'] for sample in samples) >= 1.15
    result = {'status': 'RADIANT_ORIGINAL_RTL_QUALIFIED_HOST_SPEEDUP_ELIGIBLE_FOR_SEPARATE_REVIEW' if eligible else
              'RADIANT_ORIGINAL_RTL_QUALIFIED_INSUFFICIENT_HOST_SPEEDUP_NOT_ADOPTED',
              'observed_at': datetime.now(timezone.utc).isoformat(), 'micro': micro_results,
              'prefix_samples': samples, 'prefix_CPU_cycles': 20000000,
              'prefix_node_and_CPU_cycle_events_compared': len(reference_events),
              'source_sha256': hashes, 'commands': records, 'original_runtime_unchanged': True,
              'new_full_inference_started': False, 'full_model_accepted': False,
              'hardware_latency_measured': False, 'official_accuracy_accepted': False,
              'before_after_ADP_accepted': False,
              'scope': 'Existing original CPU/PE RTL and exact firmware preserved. The only simulator flag experiment is documented+rad. Complete3tensor and intentional firmware-negative traces plus bounded20Mcycle reports agree. Host wall comparisons are not hardware performance or complete-model acceptance; original full runs remain active.'}
    save('summary.json', result)
    print(result['status'], flush=True)
except BaseException as error:
    save('failure.json', {'status': 'RADIANT_ISOLATED_CHECK_DIAGNOSTIC_FAILURE',
                          'error': repr(error), 'commands': records, 'source_sha256': hashes,
                          'original_full_runs_modified': False, 'full_model_accepted': False})
    raise
