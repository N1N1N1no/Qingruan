"""Finite, same-core host-simulator comparison; never launches full inference.

Wait for the existing option experiment to terminate, independently review it,
then pin only four new bounded prefix children to one observed idle CPU.
Existing simulator processes, RTL, firmware, clocks and references stay intact.
"""
import argparse
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
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
parser.add_argument('--prior-pid', type=int, required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision) and args.prior_pid > 1
CASE = ROOT / ('work/original-vcs-radiant-affinity-' + args.revision)
PRIOR = ROOT / 'work/original-vcs-radiant-v2'
TEMPLATE = ROOT / 'work/original-baseline-rtl/vcs/v2'
RADIANT = PRIOR / 'full/radiant'
REVIEW = ROOT / 'work/original-vcs-radiant-review-v1'
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
assert not CASE.exists(), 'Preserve prior attempts'
CASE.mkdir()
records = []
hashes = {}


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(name, value):
    temporary = CASE / (name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(CASE / name)


def checked(path):
    data = json.loads(path.read_text())
    hashes[str(path)] = sha(path)
    for name, expected in data.get('source_sha256', {}).items():
        assert sha(local(name)) == expected, name
        hashes[name] = expected
    return data


def verify():
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


def process_identity(pid):
    try:
        fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        os.kill(pid, 0)
        return fields[19]
    except ProcessLookupError:
        return None
    except FileNotFoundError:
        return None


def run(argv, label, timeout, core=None):
    directory = CASE / label
    directory.mkdir()
    started = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    def child_conditions():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
        if core is not None:
            os.sched_setaffinity(0, {core})
    print('AFFINITY_CHECK_PHASE ' + label, flush=True)
    observations = []
    with (directory / 'run.log').open('w') as stream:
        child = subprocess.Popen(argv, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=child_conditions)
        save('live-process.json', {'status': 'RUNNING', 'phase': label, 'pid': child.pid,
                                  'argv': argv, 'requested_CPU_affinity': [core] if core is not None else None})
        try:
            while True:
                if core is not None and child.poll() is None:
                    observed = sorted(os.sched_getaffinity(child.pid))
                    assert observed == [core], observed
                    observations.append({'elapsed_s': time.monotonic() - started, 'allowed_CPUs': observed})
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('AFFINITY_CHECK_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
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
    elapsed = time.monotonic() - started
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    record = {'phase': label, 'argv': argv, 'returncode': code, 'host_wall_s': elapsed,
              'host_user_CPU_s': usage_after.ru_utime - usage_before.ru_utime,
              'host_system_CPU_s': usage_after.ru_stime - usage_before.ru_stime,
              'requested_CPU_affinity': [core] if core is not None else None,
              'observed_main_thread_affinity': observations, 'log': str(directory / 'run.log')}
    records.append(record)
    save('commands.json', records)
    assert code == 0, label
    return directory, record, (directory / 'run.log').read_text(errors='replace')


try:
    helper = TASK / 'my_design/project/scripts/run_pe_axi_sdf.py'
    for path in [Path(__file__), ROOT / 'review_original_vcs_radiant.py', helper]:
        hashes[str(path)] = sha(path)
    preparation = checked(PRIOR / 'preparation.json')
    checked(PRIOR / 'micro-qualification.json')
    identity = process_identity(args.prior_pid)
    if identity is not None:
        command = Path('/proc/%d/cmdline' % args.prior_pid).read_bytes()
        assert b'check_original_vcs_radiant_v2.py' in command
    save('preparation.json', {'status': 'WAIT_FOR_EXISTING_OPTION_EXPERIMENT_THEN_SAME_CORE_PREFIX_ONLY',
         'source_sha256': hashes, 'prior_parent_pid': args.prior_pid, 'prior_process_start_ticks': identity,
         'maximum_new_simulators_at_once': 1, 'new_full_inference_started': False,
         'existing_process_affinity_or_state_modified': False,
         'scope': 'Host simulator qualification only, not full inference or hardware performance.'})
    hashes[str(CASE / 'preparation.json')] = sha(CASE / 'preparation.json')
    wait_started = time.monotonic()
    while identity is not None and process_identity(args.prior_pid) == identity:
        save('live-process.json', {'status': 'WAITING_FOR_VERIFIED_EXISTING_PROCESS',
             'phase': 'prior-option-check', 'pid': args.prior_pid, 'start_ticks': identity})
        print('AFFINITY_CHECK_WAIT_PRIOR pid=%d elapsed_s=%d' %
              (args.prior_pid, time.monotonic() - wait_started), flush=True)
        if time.monotonic() - wait_started > 5400:
            raise TimeoutError('Prior experiment still live; it was left untouched')
        time.sleep(30)
    assert (PRIOR / 'summary.json').exists() and not (PRIOR / 'failure.json').exists()
    prior = checked(PRIOR / 'summary.json')
    assert prior['status'] in [
        'RADIANT_ORIGINAL_RTL_QUALIFIED_HOST_SPEEDUP_ELIGIBLE_FOR_SEPARATE_REVIEW',
        'RADIANT_ORIGINAL_RTL_QUALIFIED_INSUFFICIENT_HOST_SPEEDUP_NOT_ADOPTED']
    assert prior['new_full_inference_started'] is False and prior['prefix_samples'][0]['observed_host_speedup'] >= 1.15
    verify()
    if not (REVIEW / 'summary.json').exists():
        run([sys.executable, '-B', str(ROOT / 'review_original_vcs_radiant.py')], 'prior-independent-review', 180)
    review = checked(REVIEW / 'summary.json')
    assert review['status'] == 'ORIGINAL_VCS_RADIANT_ISOLATED_FLAGS_TRACES_AND_PREFIX_REVIEW_PASS'
    assert not review['new_full_run_adoption_qualified_by_this_review']
    sys.path.insert(0, str(helper.parent))
    from run_pe_axi_sdf import compiled_runtime_files
    runtimes = {mode: compiled_runtime_files(path) for mode, path in [('baseline', TEMPLATE), ('radiant', RADIANT)]}
    assert runtimes['baseline'] == preparation['original_runtime']
    allowed = sorted(os.sched_getaffinity(0))
    topology = []
    for cpu in allowed:
        folder = Path('/sys/devices/system/cpu') / ('cpu%d' % cpu)
        siblings = (folder / 'topology/thread_siblings_list').read_text().strip()
        frequency = folder / 'cpufreq/cpuinfo_max_freq'
        topology.append({'CPU': cpu, 'thread_siblings': siblings,
                         'max_frequency_kHz': int(frequency.read_text()) if frequency.exists() else None})
    candidates = [item['CPU'] for item in topology if item['thread_siblings'] == str(item['CPU'])] or allowed
    def cpu_times():
        values = {}
        for line in Path('/proc/stat').read_text().splitlines():
            if re.match(r'^cpu\d+ ', line):
                parts = line.split(); values[int(parts[0][3:])] = list(map(int, parts[1:9]))
        return values
    first = cpu_times(); time.sleep(0.25); second = cpu_times()
    busy = {}
    for cpu in candidates:
        delta = [after - before for before, after in zip(first[cpu], second[cpu])]
        busy[cpu] = 1 - (delta[3] + delta[4]) / max(sum(delta), 1)
    core = min(candidates, key=lambda cpu: (busy[cpu], -cpu))
    save('core-selection.json', {'selected_CPU': core, 'allowed_CPUs': allowed, 'topology': topology,
        'candidate_busy_fraction_250ms': busy, 'prefer_no_SMT_sibling': True,
        'existing_affinity_changed': False, 'exclusive_CPU_or_fixed_frequency_claimed': False})
    command_file = TEMPLATE / 'prefix-20000000-command.json'
    original_args = json.loads(command_file.read_text())['argv']
    golden_log = TEMPLATE / 'prefix-20000000.log'
    golden_report = TEMPLATE / 'prefix-20000000-report.txt'
    for path in [command_file, golden_log, golden_report]:
        hashes[str(path)] = sha(path)
    event_pattern = r'ORIGINAL_BASELINE_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)'
    expected_events = re.findall(event_pattern, golden_log.read_text())
    assert len(expected_events) == 385
    samples = []
    for number, order in [(1, ['baseline', 'radiant']), (2, ['radiant', 'baseline'])]:
        measured = {}
        for mode in order:
            label = 'round%d-%s' % (number, mode)
            directory = CASE / label
            argv = []
            for value in original_args:
                if local(value) == TEMPLATE / 'simv':
                    value = str((TEMPLATE if mode == 'baseline' else RADIANT) / 'simv')
                elif value.startswith('+REPORT='):
                    value = '+REPORT=' + str(directory / 'report.txt')
                elif value.startswith('+LOGITS='):
                    value = '+LOGITS=' + str(directory / 'logits.bin')
                argv.append(value)
            _, record, log = run(argv, label, 3600, core)
            assert 'ORIGINAL_BASELINE_PREFIX_ONLY CPU_cycle=20000000' in log
            assert not re.search(r'Fatal:|Error-\[|ORIGINAL_BASELINE_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
            assert re.findall(event_pattern, log) == expected_events
            assert (directory / 'report.txt').read_bytes() == golden_report.read_bytes()
            measured[mode] = record['host_wall_s']
        samples.append({'round': number, 'baseline_host_wall_s': measured['baseline'],
                        'radiant_host_wall_s': measured['radiant'],
                        'observed_host_speedup': measured['baseline'] / measured['radiant']})
        save('prefix-samples.json', samples)
    verify()
    for mode, folder in [('baseline', TEMPLATE), ('radiant', RADIANT)]:
        assert compiled_runtime_files(folder) == runtimes[mode]
    eligible = min(sample['observed_host_speedup'] for sample in samples) >= 1.15
    save('summary.json', {'status': 'SAME_CORE_ORIGINAL_CPU_PE_PREFIX_EXACT_HOST_COMPARISON_COMPLETE',
         'observed_at': datetime.now(timezone.utc).isoformat(), 'selected_CPU': core,
         'prefix_CPU_cycles': 20000000, 'node_and_absolute_CPU_events_exact': 385,
         'samples': samples, 'eligible_for_separate_adoption_review': eligible,
         'source_sha256': hashes, 'runtime_files': runtimes, 'commands': records,
         'existing_processes_modified': False, 'exclusive_CPU_or_fixed_frequency_claimed': False,
         'new_full_inference_started': False, 'full_model_accepted': False,
         'hardware_performance_accepted': False, 'official_accuracy_accepted': False,
         'scope': 'Only new bounded prefix children pinned to the same logical CPU, with reverse-order repetition. OS load/frequency remain observed conditions. Host wall is not hardware latency or full inference acceptance.'})
    print('SAME_CORE_ORIGINAL_CPU_PE_PREFIX_EXACT_HOST_COMPARISON_COMPLETE', flush=True)
except BaseException as error:
    save('failure.json', {'status': 'SAME_CORE_PREFIX_QUALIFICATION_DIAGNOSTIC_FAILURE',
         'error': repr(error), 'source_sha256': hashes, 'commands': records,
         'existing_processes_modified': False, 'new_full_inference_started': False})
    raise
