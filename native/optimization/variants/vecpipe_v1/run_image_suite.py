"""Actual RV32+all8 inference for the remaining19 provided images.

Reuse the running primary's source/firmware/monitor and the qualified20 RAM
images. Compile one separate frozen runtime; never rebuild the running one.
Preserve each completed diagnostic and never silently retry a failed image.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
sys.path.insert(0, str(PROJECT / 'scripts'))
from run_pe_axi_sdf import compiled_runtime_files

parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / ('work/vecpipe-images-rv32-' + args.revision)
assert not case.exists(), 'Preserve started suite; do not launch duplicate images'
primary = ROOT / 'work/vecpipe-full-rv32-v1'
suite_path = ROOT / 'work/vecpipe-image-suite-v1/summary.json'
loader_path = ROOT / 'work/vecpipe-image-loader-v1/summary.json'
gate = ROOT / 'work/vecpipe-full-soc-gate/v1'


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify(hashes):
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name


def save(path, data):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


prep = json.loads((primary / 'preparation.json').read_text())
suite = json.loads(suite_path.read_text())
loader = json.loads(loader_path.read_text())
annotation = json.loads((gate / 'annotation-review.json').read_text())
probe = json.loads((gate / 'timing-probe-ucli-v1/summary.json').read_text())
assert prep['geometry'] == 'R8_C16_L4' and suite['image_count'] == 20
assert suite['all_preprocessed_inputs_byte_equal_to_existing_fusion_proof']
assert loader['status'] == 'ALL20_CANONICAL_RAM_ACTUAL_VCS_LOADER_ALL_SECTION_BYTES_PASS'
assert annotation['all_IOPATH_INTERCONNECT_and_timing_limits_applied']
assert probe['status'] == 'SAME_COMPILED_FULL_SOC_SRAM_TIMING_NEGATIVE_CONTROL_PASS'
for record in [prep, suite, loader, annotation, probe]:
    verify(record['source_sha256'])
records = [item for item in suite['images'] if item['image'] != prep['image']]
assert len(records) == 19
# Exercise the two unresolved public-reference cases first, then the remaining
# public cases, followed by all15 private-reference cases. None are omitted.
records.sort(key=lambda item: (0 if item['public_reference_top1'] is not None and not item['frozen_C_matches_public_top1'] else
                              1 if item['public_reference_top1'] is not None else 2, item['image']))
case.mkdir()
runtime_case = case / 'runtime'
runtime_case.mkdir()
master_sources = dict(prep['source_sha256'])
master_sources.update({str(path): sha(path) for path in [Path(__file__), primary / 'preparation.json',
    suite_path, loader_path, gate / 'annotation-review.json', gate / 'timing-probe-ucli-v1/summary.json',
    PROJECT / 'scripts/run_pe_axi_sdf.py']})
master_sources[str(suite_path)] = sha(suite_path)
master_prep = {'status': 'REMAINING19_REAL_RV32_SUITE_READY_RUNTIME_COMPILE_PENDING',
    'geometry': 'R8_C16_L4', 'primary_image_reused_without_restart': prep['image'],
    'primary_case': str(primary), 'execution_order': [item['image'] for item in records],
    'source_sha256': master_sources, 'clock_period_ns': 10, 'RAM_response_delay_ns': 1,
    'parallelism_policy': 'Initially2; at most4 as the two existing original-shortlist full VCS cases complete. Do not start another job below4GiB MemAvailable or above80% logical-CPU load. Existing jobs are never stopped.',
    'scope': 'Actual full CPU/RTL execution of all remaining19 images. Existing first-image job is retained. Per-image diagnostics, frozen-C bit equality and public reference comparison are distinct; no organizer accuracy or final selection from preparation.'}
save(case / 'preparation.json', master_prep)


def output_argument(argument):
    # The full fixture keeps its original sources and include directories.
    # Change only compiler output locations, including the compatibility object.
    return argument.replace(str(primary), str(runtime_case)) if (
        argument in [str(primary / 'compat.o'), str(primary / 'simv')]
        or argument.startswith('-Mdir=' + str(primary))) else argument


def mapped_compile(argument):
    if argument.startswith('/'):
        return output_argument(str(local(argument)))
    if argument.startswith('+incdir+'):
        return '+incdir+' + str(local(argument[len('+incdir+'):]))
    if '=' in argument:
        key, value = argument.split('=', 1)
        if value.startswith('/'):
            return output_argument(key + '=' + str(local(value)))
    return argument


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))


compile_records = []


def compile_run(argv, label, timeout):
    argv = list(map(str, argv))
    print('RV32_IMAGE_SUITE_PHASE ' + label, flush=True)
    started = time.monotonic()
    with (runtime_case / (label + '.log')).open('w') as log:
        child = subprocess.Popen(argv, cwd=runtime_case, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=limits)
        try:
            code = child.wait(timeout=timeout)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
    compile_records.append({'phase': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    assert code == 0, str(runtime_case / (label + '.log'))


for phase, limit in [('compat', 120), ('compile', 1800)]:
    argv = [mapped_compile(item) for item in prep['argv'][phase]]
    # Assert none of the compiler outputs refer to the already-running primary.
    if phase == 'compat':
        assert argv[-1] == str(runtime_case / 'compat.o')
    else:
        assert argv[argv.index('-o') + 1] == str(runtime_case / 'simv')
        assert '-Mdir=' + str(runtime_case / 'csrc') in argv
    compile_run(argv, phase, limit)
verify(master_sources)
runtime_files = compiled_runtime_files(runtime_case)
assert runtime_files
executable = runtime_case / 'simv'
runtime_proof = {'status': 'SAME_SOURCE_FULL_RV32_SUITE_RUNTIME_COMPILED_NOT_INFERENCE_ACCEPTED',
    'source_sha256': master_sources, 'commands': compile_records, 'runtime_files': runtime_files,
    'simulator_sha256': sha(executable), 'unchanged_primary_sources_and_firmware': True,
    'scope': 'Compiler output paths only differ from the existing full primary fixture; same source files, defines, clock/RAM model, monitor and full output/guard checks.'}
save(runtime_case / 'compile-summary.json', runtime_proof)
completed = []
active = {}
pending = list(records)
started = time.monotonic()


def capacity():
    finished = 0
    for geometry in ['R8_C32_L4', 'R16_C8_L4']:
        path = ROOT / 'work/full-rv32' / geometry / prep['image'] / 'summary.json'
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if (data['status'] == 'FULL_RV32_SINGLE_IMAGE_FROZEN_C_PASS_OFFICIAL_ACCURACY_OPEN'
                    and data['full_RV32_executed'] and data['all8_engines_participation_asserted']):
                finished += 1
    return min(4, 2 + finished)


def resource_headroom():
    fields = dict(re.findall(r'^(\w+):\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M))
    memory = int(fields['MemAvailable']) * 1024
    return memory >= 4 * 1024**3 and os.getloadavg()[0] < max(1, (os.cpu_count() or 1) * .8)


def start_image(item):
    verify(master_sources)
    assert compiled_runtime_files(runtime_case) == runtime_files
    directory = case / item['image']
    assert not directory.exists(), 'Never overwrite/restart a prior image'
    datafile = local(item['data_hex'])
    expected = ROOT / 'work/fusion-proof' / (item['image'] + '.baseline')
    assert sha(datafile) == item['data_hex_sha256'] and sha(expected) == item['frozen_C_logits_sha256']
    assert item['parameters'] == prep['parameters']
    directory.mkdir()
    argv = [str(executable)]
    for argument in prep['argv']['normal'][1:]:
        if argument.startswith('+MEM_HEX='):
            argv.append('+MEM_HEX=' + str(datafile))
        elif argument.startswith('+REPORT='):
            argv.append('+REPORT=' + str(directory / 'report.txt'))
        elif argument.startswith('+LOGITS='):
            argv.append('+LOGITS=' + str(directory / 'logits.bin'))
        else:
            argv.append(argument)
    hashes = {str(path): sha(path) for path in [datafile, expected, runtime_case / 'compile-summary.json', case / 'preparation.json']}
    save(directory / 'preparation.json', {'status': 'ACTUAL_RV32_FULL_IMAGE_READY', 'image': item['image'],
        'argv': argv, 'source_sha256': hashes, 'public_reference_top1': item['public_reference_top1'],
        'frozen_C_top1': item['frozen_C_top1'], 'simulator_sha256': runtime_proof['simulator_sha256'],
        'runtime_wall_limit_s': 86400, 'scope': 'Identical full-model monitor/firmware, only the already byte-validated image input and frozen reference RAM sections change.'})
    stream = (directory / 'normal.log').open('w')
    process = subprocess.Popen(argv, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                               start_new_session=True, preexec_fn=limits)
    active[item['image']] = {'process': process, 'stream': stream, 'item': item, 'directory': directory,
        'started': time.monotonic(), 'hashes': hashes, 'argv': argv, 'timed_out': False}
    save(directory / 'live-process.json', {'status': 'RUNNING', 'pid': process.pid, 'argv': argv})
    print('RV32_IMAGE_SUITE_LAUNCHED image=%s pid=%d' % (item['image'], process.pid), flush=True)


def finish_image(name, job):
    process, directory, item = job['process'], job['directory'], job['item']
    job['stream'].close()
    verify(job['hashes'])
    assert compiled_runtime_files(runtime_case) == runtime_files
    text = (directory / 'normal.log').read_text(errors='replace')
    report = (directory / 'report.txt').read_text() if (directory / 'report.txt').exists() else ''
    match = re.search(r'RESULT code=([0-9a-f]+) requested=(\d+) completed=(\d+) inference_cycles=(\d+) CPU_return_cycle=(\d+) top1=(\d+) expected_top1=(\d+) max_error_bits=([0-9a-f]+) logit_mismatched_words=(\d+) error_node=(\d+) node_events=(\d+)', report)
    measured = None
    if match:
        measured = dict(zip(['code','requested','completed','inference_cycles','CPU_return_cycle','top1','expected_top1','max_error_bits','logit_mismatched_words','error_node','node_events'], match.groups()))
        for key in measured:
            measured[key] = int(measured[key], 16 if key in ['code','max_error_bits'] else 10)
    finite = bit_exact = False
    actual_top = None
    logits_sha = None
    if (directory / 'logits.bin').exists() and (directory / 'logits.bin').stat().st_size == 4000:
        raw = (directory / 'logits.bin').read_bytes()
        values = struct.unpack('<1000f', raw)
        finite = all(math.isfinite(value) for value in values)
        actual_top = max(range(1000), key=values.__getitem__) if finite else None
        logits_sha = hashlib.sha256(raw).hexdigest()
        bit_exact = logits_sha == item['frozen_C_logits_sha256']
    internal = (process.returncode == 0 and not job['timed_out'] and
        text.count('FULL_MODEL_RV32_PASS') == text.count('FULL_MODEL_CPU_RETURN_PASS') == 1 and
        not re.search(r'Fatal:|Error-\[|FULL_MODEL_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|VECPIPE_FIFO_FAIL', text))
    if internal:
        assert measured and measured['code'] == 0x600dcafe
        assert measured['requested'] == measured['completed'] == 4640 and measured['node_events'] == 1312
        assert measured['top1'] == measured['expected_top1'] == actual_top and measured['error_node'] == 0
        assert measured['inference_cycles'] > 0 and measured['CPU_return_cycle'] > measured['inference_cycles']
    accepted = internal and finite and bit_exact
    execution_observed = 'FULL_MODEL_NODE_DONE node=' in text
    graph_completed = bool(measured and measured['requested'] == measured['completed'] == 4640
                           and measured['node_events'] == 1312
                           and re.search(r'FULL_MODEL_NODE_DONE node=1395 ', text))
    result = {'status': 'ACTUAL_RV32_FULL_IMAGE_FROZEN_C_BYTE_EXACT_PASS_OFFICIAL_REFERENCE_OPEN' if accepted else 'ACTUAL_RV32_FULL_IMAGE_DIAGNOSTIC_NOT_ACCEPTED',
        'image': name, 'actual_RV32_execution_observed': execution_observed,
        'complete_graph_execution_observed': graph_completed, 'geometry': 'R8_C16_L4',
        'CPU_internal_C_criterion_pass': internal, 'logits_byte_exact_to_frozen_C': bit_exact,
        'all1000_logits_finite': finite, 'actual_top1': actual_top,
        'public_reference_top1': item['public_reference_top1'],
        'public_reference_top1_matches': actual_top == item['public_reference_top1'] if actual_top is not None and item['public_reference_top1'] is not None else None,
        'measured': measured, 'logits_sha256': logits_sha, 'report': report,
        'source_sha256': job['hashes'], 'runtime_files': runtime_files,
        'simulator_sha256': runtime_proof['simulator_sha256'],
        'command': {'argv': job['argv'], 'returncode': process.returncode, 'elapsed_s': time.monotonic() - job['started'], 'timed_out': job['timed_out']},
        'normal_log_sha256': sha(directory / 'normal.log'), 'official_accuracy_accepted': False,
        'final_ADP_accepted': False, 'organizer_numeric_tolerance': None,
        'scope': 'Actual complete RV32+all8 execution with original full monitor, immutable firmware and input/reference RAM. Records raw logits even on CPU diagnostic returns when available. Frozen-C criterion, bit equality and supplied public labels are distinct; private organizer labels are unavailable.'}
    save(directory / ('summary.json' if accepted else 'failure.json'), result)
    save(directory / 'live-process.json', {'status': 'TERMINAL', 'returncode': process.returncode})
    completed.append({'image': name, 'status': result['status'], 'actual_top1': actual_top,
        'public_reference_top1_matches': result['public_reference_top1_matches'], 'byte_exact': bit_exact,
        'internal_C_pass': internal, 'actual_RV32_execution_observed': execution_observed,
        'complete_graph_execution_observed': graph_completed,
        'directory': str(directory), 'elapsed_s': result['command']['elapsed_s']})
    print('RV32_IMAGE_SUITE_TERMINAL image=%s returncode=%d byte_exact=%s' % (name, process.returncode, bit_exact), flush=True)


while pending or active:
    for name, job in list(active.items()):
        if job['process'].poll() is None and time.monotonic() - job['started'] > 86400:
            os.killpg(job['process'].pid, signal.SIGTERM)
            job['process'].wait(timeout=10)
            job['timed_out'] = True
        if job['process'].poll() is not None:
            finish_image(name, job)
            del active[name]
    while pending and len(active) < capacity() and resource_headroom():
        start_image(pending.pop(0))
    save(case / 'progress.json', {'status': 'REMAINING19_REAL_RV32_EXECUTION_ACTIVE',
        'active': [{'image': name, 'pid': job['process'].pid, 'elapsed_s': time.monotonic() - job['started']} for name, job in active.items()],
        'pending': [item['image'] for item in pending], 'completed': completed,
        'current_parallel_cap': capacity(), 'elapsed_s': time.monotonic() - started,
        'official_accuracy_accepted': False, 'scope': 'Execution progress only; no missing-result restart and no accuracy/ADP acceptance.'})
    if pending or active:
        print('RV32_IMAGE_SUITE_RUNNING active=%d pending=%d completed=%d' % (len(active), len(pending), len(completed)), flush=True)
        time.sleep(30)
verify(master_sources)
assert compiled_runtime_files(runtime_case) == runtime_files
save(case / 'summary.json', {'status': 'REMAINING19_RV32_IMAGE_ATTEMPTS_FINISHED_REVIEW_ALL_RESULTS',
    'completed': completed, 'attempts_finished': len(completed),
    'actual_RV32_execution_observed_images': sum(item['actual_RV32_execution_observed'] for item in completed),
    'complete_graph_execution_observed_images': sum(item['complete_graph_execution_observed'] for item in completed),
    'strict_frozen_C_byte_exact_passes': sum(item['internal_C_pass'] and item['byte_exact'] for item in completed),
    'first_image_case': str(primary), 'first_image_not_restarted': True,
    'source_sha256': master_sources, 'runtime_files': runtime_files,
    'elapsed_s': time.monotonic() - started, 'official_accuracy_accepted': False, 'final_ADP_accepted': False,
    'scope': 'Remaining19 runs finished, including diagnostics. Combine with the independently running original pipeline first image. Numerical/official-reference failures stay failures; private labels and final complete-system ADP/selection remain separate.'})
print('RV32_IMAGE_SUITE_FINISHED_REVIEW_ALL_PER_IMAGE_RESULTS', flush=True)
