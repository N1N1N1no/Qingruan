#!/usr/bin/env python3
"""Compare the full 16x16 RTL array with its exported, SDF-annotated netlist."""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from prepare_pe_sdf_models import prepare_models
from prepare_pe_array_sdf import prepare_array_sdf, validate_array_annotation

PROJECT = Path(__file__).resolve().parents[1]
TASK = PROJECT.parents[1]
EXPORT = PROJECT / 'build/dc_pe_array_export'
MEMORY_LIMIT = 9 * 1024**3


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_log(path):
    if path.stat().st_size > 64 * 1024**2:
        raise RuntimeError('Too many diagnostics; inspect ' + str(path))
    return path.read_text(errors='replace')


def limit_child():
    # Keep the already open user DC session and the VM within available memory.
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))


def stop_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # A shell can exit before a compiler grandchild. Kill the remaining
        # group even when the group leader already returned from wait().
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def validate_gate_diagnostics(compile_log, simulation_log, annotation):
    text = compile_log + '\n' + simulation_log
    codes = set(re.findall(r'(?:Warning|Lint)-\[([^]]+)\]', text))
    if codes - {'LINX_KRNL', 'TFIPC-L'}:
        raise RuntimeError('Unreviewed gate diagnostic codes: ' + ', '.join(sorted(codes)))
    for message in re.findall(r'Lint-\[TFIPC-L\](.*?)(?=\n\n)', compile_log, re.S):
        ports = re.findall(r"\b(input|output|inout) port '([^']+)' is not connected", message)
        if not ports or any(direction != 'output' or name not in {'QN', 'Cout'}
                            for direction, name in ports):
            raise RuntimeError('Missing gate ports require review')
    if (re.search(r'(?im)^\s*(?:Warning|Error|Fatal)(?:-|:)', annotation) or
            re.findall(r'Total errors:\s*(\d+)', annotation) != ['0'] or
            re.findall(r'Total warnings:\s*(\d+)', annotation) != ['0']):
        raise RuntimeError('SDF annotation has errors, warnings, or incomplete statistics')
    return sorted(codes)


def main():
    sources = {
        'netlist.v': EXPORT / 'pe_array_mapped.v',
        'original.sdf': EXPORT / 'pe_array_mapped.sdf',
        'export_manifest.json': EXPORT / 'artifact_summary.json',
        'constraints.sdc': EXPORT / 'pe_array_constraints.sdc',
        'tb.sv': PROJECT / 'tests/tb_pe_array_gate.sv',
        'reference_sram.v': PROJECT / 'tb/sram_reference_model.v',
        'standard_original.v': TASK / 'libraries/standard_timing.v',
        'sram_original.v': TASK / 'libraries/sram_timing.v',
        'compat.c': PROJECT.parent / 'compat/pthread_yield_compat.c',
        'vcs_vm.py': TASK / 'scripts/vcs_vm.py',
        'prepare_pe_sdf_models.py': PROJECT / 'scripts/prepare_pe_sdf_models.py',
        'prepare_pe_array_sdf.py': PROJECT / 'scripts/prepare_pe_array_sdf.py',
        'runner.py': Path(__file__).resolve(),
    }
    rtl_files = ['rfsp16x8.v', 'Exact_8bit.v', 'adder.v', 'pe_1sram_4mul.v', 'pe_array.v']
    for name in rtl_files:
        sources['rtl/' + name] = PROJECT / 'rtl' / name
    for path in sources.values():
        if not path.is_file():
            raise RuntimeError('Missing input: ' + str(path))
    manifest = json.loads(sources['export_manifest.json'].read_text())
    if manifest.get('status') != 'EXPORTED' or manifest.get('top') != 'pe_array':
        raise RuntimeError('Export the array first using synth/export_pe_array.tcl')
    result = Path(tempfile.mkdtemp(prefix='pe_array_sdf.', dir=TASK / 'work'))
    print('RESULT=' + str(result), flush=True)
    summary = {
        'status': 'RUNNING', 'scope': 'full 16x16 PE array, typical corner synthesis SDF',
        'sdf_triplet': 'MAXIMUM', 'sample_ns_after_posedge': 4.0,
        'soc_simulated': False, 'physical_parasitics': False,
        'formal_equivalence': False, 'child_address_space_limit_bytes': MEMORY_LIMIT,
        'cases': [], 'commands': [],
    }
    env = os.environ.copy()
    env['LC_ALL'] = 'C'

    def run(command, folder, log_name, phase, timeout=1200):
        command = list(map(str, command))
        summary['commands'].append({'argv': command, 'cwd': str(folder), 'phase': phase})
        log_path = folder / log_name
        print('PE_ARRAY_PHASE=' + phase, flush=True)
        start = time.monotonic()
        with log_path.open('w') as log:
            process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, preexec_fn=limit_child)
            try:
                while True:
                    try:
                        code = process.wait(timeout=min(30, max(0.1, timeout - (time.monotonic() - start))))
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = time.monotonic() - start
                        if elapsed >= timeout:
                            raise RuntimeError('Timeout during ' + phase + '; inspect ' + str(log_path))
                        print('PE_ARRAY_RUNNING phase=' + phase + ' elapsed_s=' + str(int(elapsed)), flush=True)
            except BaseException:
                stop_process_group(process)
                raise
        text = read_log(log_path)
        if code or re.search(r'(?m)^\s*(?:Error(?:-\[|:)|Fatal:|FATAL:)', text):
            raise RuntimeError('Command failed during ' + phase + '; inspect ' + str(log_path))
        return text

    try:
        before = {str(path): sha(path) for path in sources.values()}
        summary['input_sha256'] = before
        for name, export_name in [('netlist.v', 'pe_array_mapped.v'), ('original.sdf', 'pe_array_mapped.sdf'),
                                  ('constraints.sdc', 'pe_array_constraints.sdc')]:
            if before[str(sources[name])] != manifest['artifacts'][export_name]['sha256']:
                raise RuntimeError('Exported input differs from its manifest: ' + export_name)
        snapshot = result / 'inputs'
        for name, path in sources.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha(target) != before[str(path)]:
                raise RuntimeError('Input changed while copying: ' + str(path))
        print('PE_ARRAY_INPUTS_FROZEN', flush=True)
        summary['model_adaptation'] = prepare_models(snapshot / 'standard_original.v',
                                                     snapshot / 'sram_original.v', result / 'models')
        compat = result / 'pthread_yield_compat.o'
        run(['/usr/bin/gcc', '-fPIC', '-c', snapshot / 'compat.c', '-o', compat],
            result, 'compatibility.log', 'compatibility')
        traces = []
        for name in ['rtl', 'gate']:
            case = result / name
            case.mkdir()
            if name == 'rtl':
                design_sources = [snapshot / 'rtl' / file for file in rtl_files]
                design_sources.append(snapshot / 'reference_sram.v')
                options = ['+nospecify', '+define+PE_REFERENCE_MACRO']
            else:
                print('PE_ARRAY_PHASE=sdf_conversion', flush=True)
                summary['sdf_adaptation'] = prepare_array_sdf(snapshot / 'original.sdf', case / 'pe_array.sdf')
                print('PE_ARRAY_SDF_PREPARED', flush=True)
                design_sources = [snapshot / 'netlist.v', result / 'models/standard_model.v',
                                  result / 'models/sram_model.v']
                options = ['+define+TETRAMAX+NTC+RECREM+ARRAY_SDF_SIM', '+neg_tchk',
                           '+sdfverbose', '+sdfsummary', '+lint=TFIPC-L']
            compile_log = run([sys.executable, snapshot / 'vcs_vm.py', '-full64', '-sverilog',
                              '-timescale=1ns/1fs', *options, '-top', 'tb_pe_array_gate',
                              '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv',
                              '-LDFLAGS', str(compat), *design_sources, snapshot / 'tb.sv'],
                             case, 'compile.log', name + '_compile')
            trace = case / 'outputs.txt'
            log = run([case / 'simv', '+TRACE_FILE=' + str(trace), '+sdfverbose', '+sdfsummary'],
                      case, 'run.log', name + '_simulation', timeout=600)
            match = re.search(r'^PE_ARRAY_TEST_PASS cycles=(\d+) checked_outputs=(\d+) reset_checks=(\d+)$', log, re.M)
            if not match or re.search(r'(?i)timing[_ ]violation', log):
                raise RuntimeError('Array checks failed; inspect ' + str(case / 'run.log'))
            cycles, outputs, resets = map(int, match.groups())
            if cycles < 1200 or outputs != cycles * 64 or resets != 1:
                raise RuntimeError('Incomplete array test coverage: ' + name)
            with trace.open() as stream:
                lines = sum(1 for _ in stream)
            if lines != cycles:
                raise RuntimeError('Incomplete array output trace: ' + name)
            coverage = re.search(r'^PE_ARRAY_COVERAGE .+$', log, re.M)
            if not coverage:
                raise RuntimeError('No array coverage record: ' + name)
            coverage_counts = {key: int(value) for key, value in
                               re.findall(r'(\w+)=(\d+)', coverage.group(0))}
            expected_coverage = {'pes': 256, 'sram_writes': 4096, 'sram_reads': 4096,
                                 'read_rows': 16, 'write_rows': 16, 'disabled_rows': 16}
            if (any(coverage_counts.get(key) != value for key, value in expected_coverage.items()) or
                    any(coverage_counts.get(key, 0) <= 0 for key in
                        ['negative_operands', 'negative_zero_operands', 'wrap_events'])):
                raise RuntimeError('Incomplete array coverage counters: ' + name)
            if name == 'gate':
                annotation = read_log(case / 'sdf_annotation.log')
                summary['gate_warning_codes'] = validate_gate_diagnostics(compile_log, log, annotation)
                summary['annotation'] = validate_array_annotation(case, summary['sdf_adaptation'])
                delay = re.search(r'^PE_ARRAY_SDF_DELAY_OBSERVED .+$', log, re.M)
                if not delay:
                    raise RuntimeError('No observed nonzero SRAM delay in the array')
                summary['observed_delay'] = delay.group(0)
                print('PE_ARRAY_SDF_ANNOTATION_PASS', flush=True)
                probe = result / 'timing_probe'
                probe.mkdir()
                os.link(case / 'pe_array.sdf', probe / 'pe_array.sdf')
                probe_log = run([case / 'simv', '+TRACE_FILE=' + str(probe / 'outputs.txt'),
                                 '+SDF_TIMING_PROBE'], probe, 'run.log', 'timing_negative_control', timeout=120)
                violation = re.search(
                    r'\$setuphold\(\s*posedge clk:(\d+),\s*wd_in\[0\]:(\d+),'
                    r'\s*limits:\s*\((\d+),(\d+)\)', probe_log)
                if ('PE_ARRAY_SDF_TIMING_PROBE_DONE' not in probe_log or
                        'SRAM_TIMING_VIOLATION' not in probe_log or 'Timing violation' not in probe_log or not violation):
                    raise RuntimeError('Array timing negative control did not detect the violation: ' + str(probe))
                clock_fs, data_fs, setup_fs, hold_fs = map(int, violation.groups())
                if not (0 < data_fs - clock_fs < hold_fs and setup_fs == 9000 and hold_fs == 1000):
                    raise RuntimeError('Negative control reported an unexpected timing window')
                summary['timing_negative_control'] = {
                    'pass': True, 'data_change_after_clock_fs': data_fs - clock_fs,
                    'hold_limit_fs': hold_fs, 'log': str(probe / 'run.log')}
                print('PE_ARRAY_SDF_TIMING_CHECK_ACTIVE', flush=True)
            traces.append(trace)
            summary['cases'].append({'case': name, 'checked_cycles': cycles,
                                     'checked_outputs': outputs, 'reset_checks': resets,
                                     'coverage': coverage_counts, 'trace_sha256': sha(trace)})
            print('PE_ARRAY_' + name.upper() + '_CHECK_PASS cycles=' + str(cycles), flush=True)
        if sha(traces[0]) != sha(traces[1]):
            raise RuntimeError('RTL and timed gate array output traces differ')
        if any(sha(Path(path)) != value for path, value in before.items()):
            raise RuntimeError('Inputs changed during the test')
        summary.update(status='PASS', output_traces_match=True)
        print('PE_ARRAY_GATE_SDF_PASS', flush=True)
    except BaseException as exc:
        summary.update(status='FAIL', error=str(exc))
        raise
    finally:
        (result / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PE_ARRAY_GATE_SDF_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
