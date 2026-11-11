#!/usr/bin/env python3
"""Compare AXI RTL with its full mapped array and maximum synthesis SDF.

This is a typical-library, pre-layout simulation of the tested waveforms, not
physical signoff, full-SoC validation, or a formal equivalence proof.
"""
from collections import Counter
from decimal import Decimal
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
from prepare_pe_axi_sdf import prepare_axi_sdf, validate_axi_annotation
from run_pe_axi_functional import check_netlist, validate_diagnostics
from validate_pe_axi_sdf_warnings import validate_iwsba


PROJECT = Path(__file__).resolve().parents[1]
TASK = PROJECT.parents[1]
EXPORT = PROJECT / 'build/dc_pe_axi_export'
TOP = 'sram_multiplier_system_ID_WIDTH3'
MEMORY_LIMIT = 8 * 1024**3
MAX_LOG_BYTES = 64 * 1024**2
RTL_FILES = ['rfsp16x8.v', 'Exact_8bit.v', 'adder.v', 'pe_1sram_4mul.v',
             'pe_array.v', 'pe_axi_wrapper.v']


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_log(path):
    if path.stat().st_size > MAX_LOG_BYTES:
        raise RuntimeError('Too many diagnostics; inspect ' + str(path))
    return path.read_text(errors='replace')


def limit_child():
    # Preserve memory for the already open interactive DC session.
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))


def stop_process_group(process):
    # A shell may exit before its grandchildren; clean only this own session.
    for sig in [signal.SIGTERM, signal.SIGKILL]:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    process.wait(timeout=5)


def compiled_runtime_files(case):
    """Hash the executable and every file in its VCS runtime directory."""
    executable = case / 'simv'
    runtime = case / 'simv.daidir'
    if not executable.is_file() or not runtime.is_dir():
        raise RuntimeError('Missing compiled simulator runtime: ' + str(case))
    paths = [executable]
    for path in runtime.rglob('*'):
        if path.is_symlink() and path.is_dir():
            raise RuntimeError('Unexpected symlinked simulator runtime directory: ' + str(path))
        if path.is_file():
            paths.append(path)
        elif path.is_symlink():
            raise RuntimeError('Broken simulator runtime symlink: ' + str(path))
    if len(paths) < 2:
        raise RuntimeError('Empty simulator runtime directory: ' + str(runtime))
    return {str(path.relative_to(case)): {
                'size_bytes': path.stat().st_size, 'sha256': sha(path),
                'symlink_target': os.readlink(path) if path.is_symlink() else None,
            } for path in sorted(paths)}


def validate_probe_log(log, model_path):
    """Accept only the single deliberately induced, scoped SRAM hold failure."""
    scope = 'tb_pe_axi_sdf.dut.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro'
    violations = re.findall(r'(?m)^"([^"\n]+)", (\d+): Timing violation in (\S+)$', log)
    notifiers = re.findall(r'(?m)^SRAM_TIMING_VIOLATION instance=(\S+) time_ns=([0-9.]+)$', log)
    windows = re.findall(
        r'\$setuphold\(\s*posedge clk:(\d+),\s*wd_in\[0\]:(\d+),'
        r'\s*limits:\s*\((\d+),(\d+)\)', log)
    if (len(violations) != 1 or violations[0][0] != str(model_path) or
            violations[0][2] != scope or len(notifiers) != 1 or notifiers[0][0] != scope or
            len(windows) != 1 or log.count('Timing violation') != 1 or
            log.count('SRAM_TIMING_VIOLATION') != 1 or
            re.findall(r'(?m)^PE_AXI_SDF_TIMING_PROBE_DONE$', log) != ['PE_AXI_SDF_TIMING_PROBE_DONE'] or
            'PE_AXI_TEST_PASS' in log or re.search(r'(?im)^\s*(?:Warning|Lint)(?:-|:)', log)):
        raise RuntimeError('Probe did not contain exactly its deliberate scoped SRAM timing violation')
    clock_fs, data_fs, setup_fs, hold_fs = map(int, windows[0])
    if not (data_fs - clock_fs == 100 and setup_fs == 9000 and hold_fs == 1000):
        raise RuntimeError('Negative control reported an unexpected SRAM timing window')
    if abs(Decimal(notifiers[0][1]) - Decimal(data_fs) / Decimal(1000000)) > Decimal('0.000001'):
        raise RuntimeError('SRAM notifier time does not match the deliberate input event')
    return {'scope': scope, 'data_change_after_clock_fs': data_fs - clock_fs,
            'setup_limit_fs': setup_fs, 'hold_limit_fs': hold_fs,
            'timing_violations': 1, 'sram_notifier_events': 1}


def validate_annotation_log(text, reviewed_warning_count=0):
    if (re.search(r'(?im)^\s*(?:Warning|Error|Fatal)(?:-|:)', text) or
            re.findall(r'Total errors:\s*(\d+)', text) != ['0'] or
            re.findall(r'Total warnings:\s*(\d+)', text) != [str(reviewed_warning_count)]):
        raise RuntimeError('SDF annotation has errors, warnings, or incomplete statistics')


def validate_case(log, trace, name):
    matches = re.findall(r'^PE_AXI_TEST_PASS writes=(\d+) reads=(\d+) results=(\d+) resets=(\d+)$',
                         log, re.M)
    if len(matches) != 1 or tuple(map(int, matches[0])) != (263, 402, 192, 1):
        raise RuntimeError('Missing or incomplete AXI test completion: ' + name)
    writes, reads, results, resets = map(int, matches[0])
    coverage_matches = re.findall(
        r'^PE_AXI_COVERAGE pes=(\d+) sram_addresses=(\d+) byte_checks=(\d+) '
        r'errors=(\d+) aw_first=(\d+) w_first=(\d+) retained_results=(\d+)$', log, re.M)
    if len(coverage_matches) != 1:
        raise RuntimeError('Missing unique AXI coverage marker: ' + name)
    pe_count, address_count, byte_checks, errors, aw_first, w_first, retained = map(int, coverage_matches[0])
    if ((pe_count, address_count, byte_checks, errors, retained) != (256, 2, 5, 6, 64) or
            aw_first != 132 or w_first != 131 or aw_first + w_first != writes):
        raise RuntimeError('Missing AXI scenarios: ' + name)
    if not trace.is_file() or not trace.stat().st_size:
        raise RuntimeError('Missing AXI trace: ' + name)
    lines = trace.read_text().splitlines()
    tags = Counter(line.split()[0] if line.split() else '' for line in lines)
    if len(lines) != 666 or tags != Counter({'W': writes, 'R': reads, 'RESET': resets}):
        raise RuntimeError('Incomplete AXI transaction trace: ' + name)
    if any(re.search(r'(?i)\b[0-9a-f]*[xz][0-9a-f]*\b', line) for line in lines):
        raise RuntimeError('Unknown values in AXI trace: ' + name)
    return {'case': name, 'pass': True,
            'checks': {'writes': writes, 'reads': reads, 'results': results, 'resets': resets},
            'coverage': {'pe_instances': pe_count, 'sram_addresses': address_count,
                         'sram_bytes_written': pe_count * address_count,
                         'byte_strobe_checks': byte_checks, 'error_responses': errors,
                         'aw_before_w': aw_first, 'w_before_aw': w_first,
                         'retained_sram_results': retained},
            'trace_lines': len(lines), 'trace_sha256': sha(trace)}


def main():
    work_root = TASK / 'work'
    work_root.mkdir(exist_ok=True)
    result = Path(tempfile.mkdtemp(prefix='pe_axi_sdf.', dir=work_root))
    print('RESULT=' + str(result), flush=True)
    summary = {
        'status': 'RUNNING',
        'scope': 'AXI control and full 16x16 array, maximum triplet of typical-library synthesis SDF',
        'sdf_triplet': 'MAXIMUM', 'clock_period_ns': 10,
        'sdf_annotated': False, 'specify_enabled_for_gate': True,
        'timing_checks_enabled_for_gate': True, 'negative_timing_checks_enabled': True,
        'gate_defines': ['TETRAMAX', 'NTC', 'RECREM', 'PE_AXI_GATE_SIM', 'PE_AXI_SDF_SIM'],
        'gate_vcs_options': ['+neg_tchk', '+sdfverbose', '+sdfsummary', '+lint=TFIPC-L'],
        'cpu_included': False, 'router_included': False,
        'formal_equivalence': False, 'physical_parasitics': False,
        'all_interface_budget_combinations_covered': False,
        'physical_signoff_established': False,
        'child_address_space_limit_bytes': MEMORY_LIMIT,
        'cases': [], 'commands': [],
    }
    env = os.environ.copy()
    env['LC_ALL'] = 'C'

    def run(command, folder, log_name, phase, timeout=1200):
        command = list(map(str, command))
        log_path = folder / log_name
        record = {'argv': command, 'cwd': str(folder), 'phase': phase,
                  'log': str(log_path), 'timeout_s': timeout}
        summary['commands'].append(record)
        print('PE_AXI_SDF_PHASE=' + phase, flush=True)
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
                        if log_path.stat().st_size > MAX_LOG_BYTES:
                            raise RuntimeError('Too many diagnostics during ' + phase)
                        print('PE_AXI_SDF_RUNNING phase=' + phase + ' elapsed_s=' + str(int(elapsed)), flush=True)
            except BaseException:
                stop_process_group(process)
                raise
        record.update(returncode=code, elapsed_s=round(time.monotonic() - start, 3))
        text = read_log(log_path)
        if code or re.search(r'(?m)^\s*(?:Error(?:-\[|:)|Fatal:|FATAL:)', text):
            raise RuntimeError('Command failed during ' + phase + '; inspect ' + str(log_path))
        return text

    try:
        sources = {
            'netlist.v': EXPORT / 'pe_axi_mapped.v',
            'original.sdf': EXPORT / 'pe_axi_mapped.sdf',
            'export_manifest.json': EXPORT / 'artifact_summary.json',
            'constraints.sdc': EXPORT / 'pe_axi_constraints.sdc',
            'mapped.ddc': EXPORT / 'pe_axi_mapped.ddc',
            'export_summary.rpt': EXPORT / 'export_summary.rpt',
            'tb.sv': PROJECT / 'tests/tb_pe_axi_sdf.sv',
            'reference_sram.v': PROJECT / 'tb/sram_reference_model.v',
            'standard_original.v': TASK / 'libraries/standard_timing.v',
            'sram_original.v': TASK / 'libraries/sram_timing.v',
            'compat.c': PROJECT.parent / 'compat/pthread_yield_compat.c',
            'vcs_vm.py': TASK / 'scripts/vcs_vm.py',
            'prepare_pe_sdf_models.py': PROJECT / 'scripts/prepare_pe_sdf_models.py',
            'prepare_pe_axi_sdf.py': PROJECT / 'scripts/prepare_pe_axi_sdf.py',
            'run_pe_axi_functional.py': PROJECT / 'scripts/run_pe_axi_functional.py',
            'validate_pe_axi_sdf_warnings.py': PROJECT / 'scripts/validate_pe_axi_sdf_warnings.py',
            'runner.py': Path(__file__).resolve(),
            'run.sh': PROJECT / 'synth/run_pe_axi_sdf.sh',
            'run.tcl': PROJECT / 'synth/run_pe_axi_sdf.tcl',
            'environment/env.sh': TASK.parent / 'env.sh',
            'environment/env.vm.sh': TASK.parent / 'runtime/env.vm.sh',
            'environment/env.scl2018.sh': TASK.parent / 'runtime/env.scl2018.sh',
            'environment/env.dcpt.sh': TASK.parent / 'runtime/env.dcpt.sh',
        }
        for file in RTL_FILES:
            sources['rtl/' + file] = PROJECT / 'rtl' / file
        for path in sources.values():
            if not path.is_file():
                raise RuntimeError('Missing input: ' + str(path))
        before = {str(path): sha(path) for path in sources.values()}
        summary['input_sha256'] = before
        manifest = json.loads(sources['export_manifest.json'].read_text())
        if manifest.get('status') != 'EXPORTED' or manifest.get('top') != TOP:
            raise RuntimeError('Export the mapped AXI system using synth/export_pe_axi.tcl first')
        paired_files = {'netlist.v': 'pe_axi_mapped.v', 'original.sdf': 'pe_axi_mapped.sdf',
                        'constraints.sdc': 'pe_axi_constraints.sdc', 'mapped.ddc': 'pe_axi_mapped.ddc',
                        'export_summary.rpt': 'export_summary.rpt'}
        for name, export_name in paired_files.items():
            artifact = manifest['artifacts'][export_name]
            if (before[str(sources[name])] != artifact['sha256'] or
                    sources[name].stat().st_size != artifact['size_bytes']):
                raise RuntimeError('Exported input differs from its manifest: ' + export_name)
        for name in ['standard_original.v', 'sram_original.v']:
            source = sources[name]
            if before[str(source)] != manifest['checker_dependencies'].get(str(source)):
                raise RuntimeError('Simulation library differs from the exported design check: ' + str(source))
        snapshot = result / 'inputs'
        for name, path in sources.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha(target) != before[str(path)]:
                raise RuntimeError('Input changed while copying: ' + str(path))
        manifest = json.loads((snapshot / 'export_manifest.json').read_text())
        structure = check_netlist(snapshot / 'netlist.v',
                                  [snapshot / 'standard_original.v', snapshot / 'sram_original.v'])
        if (structure['leaf_cell_counts'] != manifest['netlist']['leaf_cell_counts'] or
                structure['pe_instances'] != 256 or structure['sram_instances'] != 256):
            raise RuntimeError('Frozen mapped netlist structure differs from the export manifest')
        summary['netlist_structure'] = structure
        print('PE_AXI_SDF_INPUTS_FROZEN sram_instances=256', flush=True)
        summary['model_adaptation'] = prepare_models(snapshot / 'standard_original.v',
                                                     snapshot / 'sram_original.v', result / 'models')
        compat = result / 'pthread_yield_compat.o'
        run(['/usr/bin/gcc', '-fPIC', '-c', snapshot / 'compat.c', '-o', compat],
            result, 'compatibility.log', 'compatibility', 60)
        traces = []
        for name in ['rtl', 'gate']:
            case = result / name
            case.mkdir()
            if name == 'rtl':
                design_sources = [snapshot / 'rtl' / file for file in RTL_FILES]
                design_sources.append(snapshot / 'reference_sram.v')
                options = ['+nospecify', '+define+PE_REFERENCE_MACRO', '+lint=TFIPC-L']
            else:
                print('PE_AXI_SDF_PHASE=sdf_conversion', flush=True)
                summary['sdf_adaptation'] = prepare_axi_sdf(snapshot / 'original.sdf',
                                                           case / 'pe_axi.sdf',
                                                           snapshot / 'export_manifest.json')
                print('PE_AXI_SDF_PREPARED', flush=True)
                design_sources = [snapshot / 'netlist.v', result / 'models/standard_model.v',
                                  result / 'models/sram_model.v']
                options = ['+define+TETRAMAX+NTC+RECREM+PE_AXI_GATE_SIM+PE_AXI_SDF_SIM',
                           '+neg_tchk', '+sdfverbose', '+sdfsummary', '+lint=TFIPC-L']
            compile_log = run([sys.executable, snapshot / 'vcs_vm.py', '-full64', '-sverilog',
                               '-timescale=1ns/1fs', *options, '-top', 'tb_pe_axi_sdf',
                               '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv',
                               '-LDFLAGS', str(compat), *design_sources, snapshot / 'tb.sv'],
                              case, 'compile.log', name + '_compile')
            if name == 'gate':
                gate_runtime = compiled_runtime_files(case)
                summary['compiled_gate_runtime'] = {
                    'executable': str(case / 'simv'), 'runtime_directory': str(case / 'simv.daidir'),
                    'files': gate_runtime, 'file_count': len(gate_runtime),
                }
            trace = case / 'trace.txt'
            log = run([case / 'simv', '+TRACE_FILE=' + str(trace), '+sdfverbose', '+sdfsummary'],
                      case, 'run.log', name + '_simulation')
            if name == 'gate':
                if compiled_runtime_files(case) != gate_runtime:
                    raise RuntimeError('Compiled gate runtime changed during normal simulation')
                summary['compiled_gate_runtime']['verified_before_and_after_normal_simulation'] = True
            if re.search(r'(?i)timing[_ ]violation', log):
                raise RuntimeError('Timing violation during normal AXI simulation: ' + str(case / 'run.log'))
            if re.search(r'(?im)^\s*(?:Warning|Lint)(?:-|:)', log):
                raise RuntimeError('Unexpected runtime warning during normal AXI simulation')
            diagnostic_compile_log = compile_log
            if name == 'gate':
                reviewed_annotation = validate_iwsba(compile_log, read_log(case / 'sdf_annotation.log'),
                                                     snapshot / 'netlist.v', case / 'pe_axi.sdf')
                diagnostic_compile_log = reviewed_annotation['sanitized_compile_log']
                validate_annotation_log(reviewed_annotation['sanitized_annotation_log'],
                                        reviewed_annotation['reviewed_warning_count'])
                summary['reviewed_sdf_warnings'] = {
                    key: value for key, value in reviewed_annotation.items()
                    if key not in {'sanitized_compile_log', 'sanitized_annotation_log'}}
            diagnostics = validate_diagnostics(diagnostic_compile_log, log, name, snapshot / 'rtl/pe_array.v')
            case_record = validate_case(log, trace, name)
            case_record.update(diagnostic_codes=diagnostics['codes'],
                               reviewed_diagnostics=diagnostics['reviewed_messages'])
            traces.append(trace)
            summary['cases'].append(case_record)
            print('PE_AXI_' + name.upper() + '_SDF_CHECK_PASS results=192', flush=True)
            if name == 'gate':
                summary['annotation'] = validate_axi_annotation(case, summary['sdf_adaptation'])
                summary['sdf_annotated'] = True
                delay_matches = re.findall(r'^PE_AXI_SDF_DELAY_OBSERVED events=(\d+) first_sram_ns=([0-9.eE+-]+)$',
                                           log, re.M)
                if (len(delay_matches) != 1 or int(delay_matches[0][0]) <= 0 or
                        not 0 < float(delay_matches[0][1]) < 5):
                    raise RuntimeError('No observed nonzero SRAM delay in the mapped AXI system')
                summary['observed_delay'] = {'events': int(delay_matches[0][0]),
                                             'first_sram_ns': float(delay_matches[0][1])}
                araddr_matches = re.findall(
                    r'^PE_AXI_SDF_ARADDR_DELAY_OBSERVED events=(\d+) min_ns=([0-9.eE+-]+) max_ns=([0-9.eE+-]+)$',
                    log, re.M)
                representative = reviewed_annotation['representative_delay']
                expected_delays = sorted(Decimal(representative[key]) for key in ['rise_ns', 'fall_ns'])
                if len(araddr_matches) != 1 or int(araddr_matches[0][0]) < 2:
                    raise RuntimeError('No observed delayed address INTERCONNECT despite reviewed annotation warning')
                observed_delays = [Decimal(value) for value in araddr_matches[0][1:]]
                if any(not value.is_finite() for value in observed_delays) or any(
                        abs(actual - expected) > Decimal('0.000001')
                        for actual, expected in zip(observed_delays, expected_delays)):
                    raise RuntimeError('Observed address INTERCONNECT delays differ from exact SDF values')
                summary['observed_warned_interconnect_delay'] = {
                    'events': int(araddr_matches[0][0]), 'observed_min_ns': str(observed_delays[0]),
                    'observed_max_ns': str(observed_delays[1]), 'expected': representative,
                    'comparison_tolerance_ns': '0.000001',
                }
                print('PE_AXI_SDF_ANNOTATION_PASS', flush=True)
                probe = result / 'timing_probe'
                probe.mkdir()
                os.link(case / 'pe_axi.sdf', probe / 'pe_axi.sdf')
                sdf_hash = summary['sdf_adaptation']['simulation_sdf_sha256']
                if (sha(case / 'pe_axi.sdf') != sdf_hash or sha(probe / 'pe_axi.sdf') != sdf_hash or
                        (case / 'pe_axi.sdf').stat().st_ino != (probe / 'pe_axi.sdf').stat().st_ino or
                        (case / 'pe_axi.sdf').stat().st_dev != (probe / 'pe_axi.sdf').stat().st_dev):
                    raise RuntimeError('Probe SDF is not the identical verified simulation SDF hardlink')
                annotation_files = {name: sha(case / name)
                                    for name in ['sdf_annotation.log', 'sdfAnnotateInfo']}
                if compiled_runtime_files(case) != gate_runtime:
                    raise RuntimeError('Compiled gate runtime changed before the timing probe')
                probe_log = run([case / 'simv', '+TRACE_FILE=' + str(probe / 'trace.txt'),
                                 '+SDF_TIMING_PROBE', '+sdfverbose', '+sdfsummary'],
                                probe, 'run.log', 'timing_negative_control')
                probe_checks = validate_probe_log(probe_log, result / 'models/sram_model.v')
                if compiled_runtime_files(case) != gate_runtime:
                    raise RuntimeError('Compiled gate runtime changed during the timing probe')
                if (sha(case / 'pe_axi.sdf') != sdf_hash or sha(probe / 'pe_axi.sdf') != sdf_hash or
                        any(sha(case / name) != expected for name, expected in annotation_files.items())):
                    raise RuntimeError('SDF or verified compile-time annotation reports changed during the probe')
                # VCS 2016 performs this annotation while building simv. The
                # second execution uses that exact annotated executable and
                # runtime bundle; it does not regenerate annotation reports.
                # Keep this provenance explicit instead of manufacturing a
                # second report or claiming a second annotation took place.
                probe_reports = [probe / 'sdf_annotation.log', probe / 'sdfAnnotateInfo']
                if any(path.exists() for path in probe_reports):
                    raise RuntimeError('Unexpected regenerated probe annotation reports require review')
                summary['compiled_gate_runtime']['verified_before_and_after_timing_probe'] = True
                summary['timing_negative_control'] = {
                    'pass': True, 'expected_violation_only_in_separate_probe': True,
                    **probe_checks, 'log': str(probe / 'run.log'),
                    'same_compiled_gate_runtime_verified': True,
                    'compiled_runtime_file_count': len(gate_runtime),
                    'compile_time_annotation_reused': True,
                    'probe_annotation_reports_regenerated': False,
                    'annotation_source_reports': {
                        name: {'path': str(case / name), 'sha256': expected}
                        for name, expected in annotation_files.items()},
                    'reviewed_annotation_warning_count': reviewed_annotation['reviewed_warning_count'],
                    'verified_annotation_metadata': summary['annotation'],
                    'simulation_sdf_sha256': sdf_hash, 'identical_sdf_hardlink_verified': True,
                    'provenance': 'The separate probe executed the unchanged simulator and every file '
                                  'in simv.daidir from the fully checked normal gate compile. '
                                  'VCS embedded annotation at compile time and did not regenerate probe reports.',
                }
                print('PE_AXI_SDF_TIMING_CHECK_ACTIVE', flush=True)
        if summary['cases'][0]['checks'] != summary['cases'][1]['checks'] or sha(traces[0]) != sha(traces[1]):
            raise RuntimeError('RTL and timed mapped AXI response traces differ')
        if any(sha(Path(path)) != expected for path, expected in before.items()):
            raise RuntimeError('Inputs changed during AXI SDF regression')
        summary.update(status='PASS', output_traces_match=True, normal_timing_violations=0)
        print('PE_AXI_GATE_SDF_PASS', flush=True)
    except BaseException as exc:
        summary.update(status='FAIL', error=str(exc))
        raise
    finally:
        (result / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    def handle_termination(signum, frame):
        raise InterruptedError('Received signal ' + str(signum))

    signal.signal(signal.SIGTERM, handle_termination)
    try:
        main()
    except KeyboardInterrupt:
        print('PE_AXI_GATE_SDF_FAILED: interrupted', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print('PE_AXI_GATE_SDF_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
