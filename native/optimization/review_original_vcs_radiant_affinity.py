"""Independent review of the completed four-prefix, same-core experiment."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
CASE = ROOT / 'work/original-vcs-radiant-affinity-v1'
OUT = ROOT / 'work/original-vcs-radiant-affinity-review-v1'
TEMPLATE = ROOT / 'work/original-baseline-rtl/vcs/v2'
RADIANT = ROOT / 'work/original-vcs-radiant-v2/full/radiant'
assert not OUT.exists(), 'Preserve prior reviews'
used = {}


def local(value):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if value.startswith(prefix):
            return BASE / value[len(prefix):]
    return Path(value)


def canonical(value):
    return value.replace('/workspace/', str(BASE) + '/')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def guard(path):
    path = local(str(path))
    used[str(path)] = sha(path)
    return used[str(path)]


def read(path):
    guard(path)
    return json.loads(path.read_text())


result = read(CASE / 'summary.json')
assert result['status'] == 'SAME_CORE_ORIGINAL_CPU_PE_PREFIX_EXACT_HOST_COMPARISON_COMPLETE'
for name, expected in result['source_sha256'].items():
    assert guard(local(name)) == expected, name
commands = read(CASE / 'commands.json')
assert commands == result['commands'] and all(item['returncode'] == 0 for item in commands)
prefixes = [item for item in commands if item['phase'].startswith('round')]
assert [item['phase'] for item in prefixes] == ['round1-baseline', 'round1-radiant', 'round2-radiant', 'round2-baseline']
selection = read(CASE / 'core-selection.json')
assert selection['selected_CPU'] == result['selected_CPU'] == 22
assert not selection['existing_affinity_changed'] and not selection['exclusive_CPU_or_fixed_frequency_claimed']
assert 22 in selection['allowed_CPUs']
assert next(item for item in selection['topology'] if item['CPU'] == 22)['thread_siblings'] == '22'
command_reference = read(TEMPLATE / 'prefix-20000000-command.json')['argv']
gold_log_path = TEMPLATE / 'prefix-20000000.log'
gold_report_path = TEMPLATE / 'prefix-20000000-report.txt'
guard(gold_log_path)
guard(gold_report_path)
pattern = r'^ORIGINAL_BASELINE_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)$'
events = re.findall(pattern, gold_log_path.read_text(), re.M)
assert len(events) == 385
observations = []
for item in prefixes:
    directory = CASE / item['phase']
    mode = item['phase'].split('-', 1)[1]
    executable = (TEMPLATE if mode == 'baseline' else RADIANT) / 'simv'
    expected_args = []
    for value in command_reference:
        if local(value) == TEMPLATE / 'simv':
            value = str(executable)
        elif value.startswith('+REPORT='):
            value = '+REPORT=' + str(directory / 'report.txt')
        elif value.startswith('+LOGITS='):
            value = '+LOGITS=' + str(directory / 'logits.bin')
        expected_args.append(value)
    assert list(map(canonical, item['argv'])) == list(map(canonical, expected_args))
    assert item['requested_CPU_affinity'] == [22]
    samples = item['observed_main_thread_affinity']
    assert len(samples) >= 10 and all(sample['allowed_CPUs'] == [22] for sample in samples)
    assert samples[0]['elapsed_s'] < 5 and samples[-1]['elapsed_s'] > item['host_wall_s'] - 35
    assert all(samples[i]['elapsed_s'] < samples[i + 1]['elapsed_s'] for i in range(len(samples) - 1))
    log_path = directory / 'run.log'
    guard(log_path)
    log = log_path.read_text()
    assert 'ORIGINAL_BASELINE_PREFIX_ONLY CPU_cycle=20000000' in log
    assert not re.search(r'Fatal:|Error-\[|ORIGINAL_BASELINE_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
    assert re.findall(pattern, log, re.M) == events
    assert guard(directory / 'report.txt') == used[str(local(str(gold_report_path)))]
    cpu_fraction = (item['host_user_CPU_s'] + item['host_system_CPU_s']) / item['host_wall_s']
    observations.append({'phase': item['phase'], 'host_wall_s': item['host_wall_s'],
                         'observed_affinity_samples': len(samples), 'CPU_seconds_over_wall_seconds': cpu_fraction})
runtime_counts = {}
for mode, directory in [('baseline', TEMPLATE), ('radiant', RADIANT)]:
    recorded = result['runtime_files'][mode]
    files = [directory / 'simv']
    for path in (directory / 'simv.daidir').rglob('*'):
        assert not (path.is_symlink() and path.is_dir())
        if path.is_file():
            files.append(path)
        elif path.is_symlink():
            raise AssertionError('Broken runtime link')
    assert {str(path.relative_to(directory)) for path in files} == set(recorded)
    for path in files:
        expected = recorded[str(path.relative_to(directory))]
        assert guard(path) == expected['sha256'] and path.stat().st_size == expected['size_bytes']
        assert (os.readlink(path) if path.is_symlink() else None) == expected['symlink_target']
    runtime_counts[mode] = len(recorded)
samples = read(CASE / 'prefix-samples.json')
assert samples == result['samples'] and len(samples) == 2
for sample in samples:
    number = sample['round']
    times = {mode: next(item['host_wall_s'] for item in prefixes if item['phase'] == 'round%d-%s' % (number, mode))
             for mode in ['baseline', 'radiant']}
    assert sample['baseline_host_wall_s'] == times['baseline'] and sample['radiant_host_wall_s'] == times['radiant']
    assert sample['observed_host_speedup'] == times['baseline'] / times['radiant']
eligible = min(sample['observed_host_speedup'] for sample in samples) >= 1.15
assert eligible == result['eligible_for_separate_adoption_review'] is False
for key in ['existing_processes_modified', 'exclusive_CPU_or_fixed_frequency_claimed', 'new_full_inference_started',
            'full_model_accepted', 'hardware_performance_accepted', 'official_accuracy_accepted']:
    assert result[key] is False
guard(Path(__file__))
OUT.mkdir()
(OUT / 'review_driver.py').write_bytes(Path(__file__).read_bytes())
guard(OUT / 'review_driver.py')
proof = {'status': 'SAME_CORE_ORIGINAL_CPU_PE_PREFIX_CYCLES_AFFINITY_AND_RUNTIME_REVIEW_PASS',
    'observed_at': datetime.now(timezone.utc).isoformat(), 'selected_CPU': 22,
    'prefix_runs': 4, 'CPU_cycles_each': 20000000, 'node_and_absolute_CPU_cycle_events_exact_each': 385,
    'samples': samples, 'affinity_and_CPU_usage_observations': observations,
    'runtime_files_unchanged': runtime_counts, 'eligible_for_separate_adoption_review': False,
    'full_inference_accepted': False, 'hardware_performance_accepted': False,
    'official_accuracy_accepted': False, 'new_full_inference_started': False,
    'source_sha256': used,
    'scope': 'Four bounded original-RTL prefixes with exact outputs/events, same logical CPU and checked runtime files. Observed host gains below the internal15% threshold; no new full run or hardware/official-performance acceptance. CPU exclusivity/frequency were not fixed.'}
(OUT / 'summary.json').write_text(json.dumps(proof, indent=2) + '\n')
print(json.dumps({key: value for key, value in proof.items() if key != 'source_sha256'}, indent=2))
