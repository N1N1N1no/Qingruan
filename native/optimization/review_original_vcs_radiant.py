"""Independently review the completed, isolated simulator-option experiment."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
CASE = ROOT / 'work/original-vcs-radiant-v2'
OUT = ROOT / 'work/original-vcs-radiant-review-v1'
TEMPLATE = ROOT / 'work/original-baseline-rtl/vcs/v2'
MICRO = BASE / 'native/swin-tiny/work/rtl.3692ulcq'
used = {}


def local(value):
    return BASE / value[11:] if value.startswith('/workspace/') else Path(value)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    used[str(path)] = digest(path)
    return path.read_text()


def load(path):
    return json.loads(read(path))


def canonical(value):
    return value.replace('/workspace/', str(BASE) + '/')


def compare_compile(old, new, micro=False):
    assert new.count('+rad') == 1 and '+rad' not in old
    new = [value for value in new if value != '+rad']
    if micro:
        assert old.count('-debug_access') == 1
        old = [value for value in old if value != '-debug_access']
    assert len(old) == len(new)
    changed = []
    for index, (before, after) in enumerate(zip(old, new)):
        if canonical(before) == canonical(after):
            continue
        if before.startswith('-Mdir=') and after.startswith('-Mdir='):
            changed.append('build-directory')
        elif old[index - 1] == '-o' and after.endswith('/simv'):
            changed.append('executable-output')
        elif old[index - 1] == '-LDFLAGS' and before.endswith('/compat.o') and after.endswith('/compat.o'):
            changed.append('compat-object-path')
        elif micro and before.endswith('/tb_soc.sv') and after.endswith('/tb_soc.sv'):
            changed.append('unused-wave-branch-adapter')
        else:
            raise AssertionError((index, before, after))
    assert sorted(changed) == sorted(['build-directory', 'executable-output', 'compat-object-path'] +
                                    (['unused-wave-branch-adapter'] if micro else []))
    return changed


assert not OUT.exists(), 'Preserve prior review'
result = load(CASE / 'summary.json')
topology = load(CASE / 'host-topology-observation.json')
assert topology['benchmark_children_were_pinned_to_same_core'] is False
assert topology['first_round_process_affinity_history_available'] is False
assert result['status'] in {
    'RADIANT_ORIGINAL_RTL_QUALIFIED_HOST_SPEEDUP_ELIGIBLE_FOR_SEPARATE_REVIEW',
    'RADIANT_ORIGINAL_RTL_QUALIFIED_INSUFFICIENT_HOST_SPEEDUP_NOT_ADOPTED'}
for name, expected in result['source_sha256'].items():
    path = local(name)
    assert digest(path) == expected, name
    used[str(path)] = expected
preparation = load(CASE / 'preparation.json')
runtime = preparation['original_runtime']
paths = [TEMPLATE / 'simv'] + [p for p in (TEMPLATE / 'simv.daidir').rglob('*') if p.is_file()]
assert {str(p.relative_to(TEMPLATE)) for p in paths} == set(runtime)
for path in paths:
    expected = runtime[str(path.relative_to(TEMPLATE))]
    assert digest(path) == expected['sha256'] and path.stat().st_size == expected['size_bytes']
    assert (os.readlink(path) if path.is_symlink() else None) == expected['symlink_target']
    used[str(path)] = expected['sha256']
commands = load(CASE / 'commands.json')
assert commands == result['commands'] and all(command['returncode'] == 0 for command in commands)
micro_proof = load(MICRO / 'summary.json')
old_micro = next(command['argv'] for command in micro_proof['commands'] if '-sverilog' in command['argv'])
old_full = next(command['argv'] for command in load(TEMPLATE / 'compile-summary.json')['commands'] if command['phase'] == 'compile')
changes = {}
for label, original, is_micro in [('compile-radiant', old_micro, True), ('compile-full-radiant', old_full, False)]:
    command = next(command for command in commands if command['phase'] == label)
    changes[label] = compare_compile(original, command['argv'], is_micro)
    assert not re.search(r'Error-\[|Fatal:', read(local(command['log'])))
runner = ast.parse(read(ROOT / 'run_original_baseline_vcs.py'))
assignments = [node for node in ast.walk(runner) if isinstance(node, ast.Assign) and
               any(isinstance(target, ast.Name) and target.id == 'optional_wave' for target in node.targets)]
assert len(assignments) == 1
wave = ast.literal_eval(assignments[0].value)
old_tb = local(next(value for value in old_micro if value.endswith('/tb_soc.sv')))
original_tb = read(old_tb)
assert original_tb.count(wave) == 1
assert read(CASE / 'micro/tb_soc.sv') == original_tb.replace(wave, '')
assert re.search(r'(?m)^\+rad\s*\n\s*Performs Radiant', read(ROOT / 'work/vcs-optimization-help-v1/help-full64.txt'))

for kind in ['good', 'bad']:
    command = next(command for command in commands if command['phase'] == 'radiant-' + kind)
    original_args = next(item['argv'] for item in micro_proof['commands'] if
                         item['argv'][0].endswith('/simv') and any(value.endswith('/' + kind + '.hex') for value in item['argv']))
    assert list(map(canonical, command['argv'][1:])) == list(map(canonical, [
            '+TRACE=' + str(CASE / 'micro/radiant' / (kind + '.tsv')) if value.startswith('+TRACE=') else value
            for value in original_args[1:]]))
    observed = CASE / 'micro/radiant' / (kind + '.tsv')
    reference = MICRO / (kind + '_trace.tsv')
    assert read(observed) == read(reference) and digest(observed) == digest(reference)
    log = read(local(command['log']))
    assert not re.search(r'Error-\[|SWIN_DRIVER_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
    if kind == 'good':
        assert micro_proof['normal_monitor'] in log and 'Fatal:' not in log
    else:
        reference_log = read(MICRO / 'bad_simulation.log')
        fatal = r'Fatal: "([^"\n]+)", (\d+): (\S+): at time (\d+) (\w+)'
        assert len(re.findall(fatal, log)) == log.count('Fatal:') == 1
        assert re.findall(fatal, log) == re.findall(fatal, reference_log)
        mismatch = r'^SWIN_MONITOR_FAIL arithmetic .+$'
        assert re.findall(mismatch, log, re.M) == re.findall(mismatch, reference_log, re.M)
        assert log.count('SWIN_MONITOR_FAIL') == 1

reference_log = read(TEMPLATE / 'prefix-20000000.log')
events = r'ORIGINAL_BASELINE_NODE_DONE node=(\d+) cycles=(\d+) CPU_cycle=(\d+)'
gold_events = re.findall(events, reference_log)
assert len(gold_events) == 385
gold_report = read(TEMPLATE / 'prefix-20000000-report.txt')
gold_args = load(TEMPLATE / 'prefix-20000000-command.json')['argv']
for command in [item for item in commands if item['phase'].startswith('prefix-')]:
    args = command['argv']
    assert len(args) == len(gold_args)
    for before, after in zip(gold_args, args):
        if before.startswith(('+REPORT=', '+LOGITS=')):
            assert before.split('=')[0] == after.split('=')[0]
            assert local(after.split('=', 1)[1]).is_relative_to(CASE / 'prefix')
        elif local(before) == TEMPLATE / 'simv':
            assert local(after) in [TEMPLATE / 'simv', CASE / 'full/radiant/simv']
        else:
            assert canonical(before) == canonical(after)
    log = read(local(command['log']))
    assert 'ORIGINAL_BASELINE_PREFIX_ONLY CPU_cycle=20000000' in log
    assert not re.search(r'Fatal:|Error-\[|ORIGINAL_BASELINE_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', log)
    assert re.findall(events, log) == gold_events
    report = local(next(value.split('=', 1)[1] for value in args if value.startswith('+REPORT=')))
    assert read(report) == gold_report and digest(report) == digest(TEMPLATE / 'prefix-20000000-report.txt')
samples = result['prefix_samples']
assert len(samples) in [1, 2]
for sample in samples:
    number = sample['round']
    times = {mode: next(item['elapsed_s'] for item in commands if item['phase'] == 'prefix-%d-%s' % (number, mode))
             for mode in ['baseline', 'radiant']}
    assert sample['baseline_wall_s'] == times['baseline'] and sample['radiant_wall_s'] == times['radiant']
    assert sample['observed_host_speedup'] == times['baseline'] / times['radiant']
eligible = len(samples) == 2 and min(sample['observed_host_speedup'] for sample in samples) >= 1.15
assert ('ELIGIBLE_FOR_SEPARATE_REVIEW' in result['status']) == eligible
for key in ['new_full_inference_started', 'full_model_accepted', 'hardware_latency_measured',
            'official_accuracy_accepted', 'before_after_ADP_accepted']:
    assert result[key] is False
used[str(Path(__file__))] = digest(Path(__file__))
OUT.mkdir()
proof = {'status': 'ORIGINAL_VCS_RADIANT_ISOLATED_FLAGS_TRACES_AND_PREFIX_REVIEW_PASS',
         'observed_at': datetime.now(timezone.utc).isoformat(), 'compile_changes': changes,
         'original_runtime_files_unchanged': len(runtime), 'micro_results': 75,
         'prefix_completed_node_events_exact': 385, 'prefix_CPU_cycles': 20000000,
         'prefix_host_wall_samples': samples, 'eligible_for_separate_full_run_review': eligible,
         'host_CPU_affinity_controlled': False,
         'host_wall_speedup_causally_attributed_to_rad': False,
         'new_full_run_adoption_qualified_by_this_review': False,
         'adoption_requires_same_core_host_comparison': True,
         'original_negative_monitor_detection': True, 'CPU_negative_detection_claimed': False,
         'full_inference_accepted': False, 'hardware_performance_accepted': False,
         'official_accuracy_accepted': False, 'source_sha256': used}
(OUT / 'summary.json').write_text(json.dumps(proof, indent=2) + '\n')
print(json.dumps({key: value for key, value in proof.items() if key != 'source_sha256'}, indent=2))
