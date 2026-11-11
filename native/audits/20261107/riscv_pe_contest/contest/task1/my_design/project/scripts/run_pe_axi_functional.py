#!/usr/bin/env python3
"""Compare AXI-controlled 16x16 RTL and mapped gates; no SDF or timing checks."""
from collections import Counter
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


PROJECT = Path(__file__).resolve().parents[1]
TASK = PROJECT.parents[1]
EXPORT = PROJECT / 'build/dc_pe_axi_mapped'
TOP = 'sram_multiplier_system_ID_WIDTH3'
SRAM = 'SRAM_6T_CORE_16x8_MC_TB'
MEMORY_LIMIT = 8 * 1024**3
MAX_LOG_BYTES = 64 * 1024**2
RTL_FILES = ['rfsp16x8.v', 'Exact_8bit.v', 'adder.v', 'pe_1sram_4mul.v',
             'pe_array.v', 'pe_axi_wrapper.v']


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_log(path):
    if path.stat().st_size > MAX_LOG_BYTES:
        raise RuntimeError('Too many diagnostics; inspect ' + str(path))
    return path.read_text(errors='replace')


def limit_child():
    # Limit this runner's descendants, preserving memory for the user's DC.
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))


def stop_process_group(process):
    # Only this subprocess session, never another running DC/simulator session.
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


def check_netlist(path, library_paths):
    """Count physical instances through the shared, unparameterized DC hierarchy."""
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', path.read_text(), flags=re.S)
    definitions = re.findall(r'\bmodule\s+(\w+)\s*\(.*?\bendmodule\b', text, re.S)
    if len(definitions) != len(set(definitions)) or definitions.count(TOP) != 1:
        raise RuntimeError('Mapped netlist must contain unique modules and exactly one ' + TOP)
    blocks = re.findall(r'\bmodule\s+(\w+)\s*\(.*?;(.*?)\bendmodule\b', text, re.S)
    modules = {}
    for name, body in blocks:
        if re.search(r'\balways(?:_\w+)?\b|\binitial\b', body):
            raise RuntimeError('Unexpected behavioral logic in mapped module ' + name)
        modules[name] = Counter(ref for ref, _ in re.findall(
            r'^\s*(\w+)\s+(\\\S+|\w+)\s*\(', body, re.M))
    library_names = set()
    for library_path in library_paths:
        library_names.update(re.findall(r'\bmodule\s+(\w+)\b', library_path.read_text()))
    memo = {}

    def count(name, active):
        if name in memo:
            return memo[name].copy()
        if name in active:
            raise RuntimeError('Recursive hierarchy in mapped netlist: ' + name)
        if name not in modules:
            if name not in library_names:
                raise RuntimeError('Unresolved mapped cell type: ' + name)
            return Counter({name: 1})
        result = Counter()
        for ref, number in modules[name].items():
            for cell, descendants in count(ref, active | {name}).items():
                result[cell] += number * descendants
        if name.endswith('_pe_1sram_4mul'):
            result['__PE_INSTANCES__'] += 1
        memo[name] = result
        return result.copy()

    expanded = count(TOP, set())
    if expanded[SRAM] != 256 or expanded['__PE_INSTANCES__'] != 256:
        raise RuntimeError('Expected a full 16x16 mapped array with 256 PE/SRAM instances')
    return {'top': TOP, 'module_definitions': len(modules),
            'pe_instances': expanded.pop('__PE_INSTANCES__'),
            'sram_instances': expanded[SRAM],
            'leaf_cell_counts': dict(sorted(expanded.items()))}


def validate_diagnostics(compile_log, simulation_log, case_name='gate', rtl_array_path=None):
    codes = set(re.findall(r'(?:Warning|Lint)-\[([^]]+)\]', compile_log + '\n' + simulation_log))
    allowed_codes = {'LINX_KRNL', 'TFIPC-L'}
    reviewed = []
    if case_name == 'rtl' and 'SIOB' in codes:
        if rtl_array_path is None:
            raise RuntimeError('RTL SIOB review requires its frozen pe_array.v source')
        source = rtl_array_path.read_text()
        normalized = re.sub(r'\s+', '', source)
        required_branches = [
            '.data_in((col==0)?data_in[row*32+:32]:data_wire[(row*N+col-1)*32+:32]),',
            '.psum_in((row==0)?psum_in[col*128+:128]:psum_wire[((row-1)*N+col)*128+:128]),',
        ]
        if any(branch not in normalized for branch in required_branches):
            raise RuntimeError('Reviewed constant ternary branches changed in pe_array.v')
        instance = re.search(r'(?m)^\s*pe_1sram_4mul[ \t]+u_pe[ \t]*\(', source)
        if instance is None:
            raise RuntimeError('Reviewed pe_array u_pe instance changed')
        instance_line = source.count('\n', 0, source.index('pe_1sram_4mul', instance.start())) + 1
        messages = re.findall(r'(?ms)^Warning-\[SIOB\](.*?)(?=\n\n|\Z)', compile_log)
        expected = Counter({'data_wire[((((0 * N) + 0) - 1) * 32)+:32]': 1})
        expected.update('psum_wire[((((0 - 1) * N) + ' + str(col) + ') * 128)+:128]'
                        for col in range(16))
        observed = Counter()
        for message in messages:
            match = re.fullmatch(
                r' Select index out of bounds\n([^\n]+), (\d+)\n"([^"\n]+)"\n'
                r'\s+The select index is out of declared bounds : \[(\d+):0\]\.\n'
                r'\s+In module instance : u_pe_array[ \t]*\n'
                r'\s+In module : pe_array\.[ \t]*', message)
            if match is None:
                raise RuntimeError('Unreviewed RTL SIOB diagnostic format')
            filename, line, expression, upper_bound = match.groups()
            expected_bound = '8191' if expression.startswith('data_wire[') else '32767'
            if (filename != str(rtl_array_path) or int(line) != instance_line or
                    upper_bound != expected_bound or expression not in expected):
                raise RuntimeError('Unreviewed RTL out-of-bounds select: ' + expression)
            observed[expression] += 1
        if (observed != expected or len(messages) != 17 or
                len(re.findall(r'(?:Warning|Lint)-\[SIOB\]', compile_log)) != 17 or
                'SIOB' in simulation_log):
            raise RuntimeError('Expected only the 17 reviewed constant unselected RTL branches')
        allowed_codes.add('SIOB')
        reviewed.append({
            'code': 'SIOB', 'count': 17, 'source': str(rtl_array_path),
            'module': 'pe_array', 'instance': 'u_pe_array',
            'explanation': 'VCS checks constant-unselected ternary branches at the first row/column; '
                           'col==0 selects data_in and row==0 selects psum_in instead.',
            'expressions': dict(sorted(observed.items())),
        })
    if codes - allowed_codes:
        raise RuntimeError('Unreviewed simulator diagnostics: ' + ', '.join(sorted(codes)))
    unused_output_counts = Counter()
    for message in re.findall(r'Lint-\[TFIPC-L\](.*?)(?=\n\n)', compile_log, re.S):
        ports = re.findall(r"\b(input|output|inout) port '([^']+)' is not connected", message)
        if not ports:
            raise RuntimeError('Missing cell ports require review')
        for direction, port in ports:
            if direction != 'output':
                raise RuntimeError('Unconnected cell input/inout requires review')
            if port == 'Q':
                instance = re.search(r'(?ms)^"(\w+)\s+(?:\\\S+|\w+)\s*\((.*?)\);"$', message)
                if (case_name != 'gate' or instance is None or instance[1] != 'DFFR_X1' or
                        not re.search(r'\.QN\s*\(\s*[^\s()]+\s*\)', instance[2]) or
                        re.search(r'\.Q\s*\(', instance[2])):
                    raise RuntimeError('Unused Q requires DFFR_X1 with its QN output connected')
                unused_output_counts['DFFR_X1.Q_with_QN_connected'] += 1
            elif port in {'QN', 'Cout'}:
                unused_output_counts[port] += 1
            else:
                raise RuntimeError('Unconnected cell output requires review: ' + port)
    if unused_output_counts:
        reviewed.append({
            'code': 'TFIPC-L', 'unconnected_output_counts': dict(sorted(unused_output_counts.items())),
            'explanation': 'Unused complementary/carry outputs are permitted; Q is permitted only '
                           'on DFFR_X1 instances with a nonempty QN connection and no Q connection. '
                           'No unconnected input or inout is permitted.',
        })
    if 'SRAM_TIMING_VIOLATION' in simulation_log:
        raise RuntimeError('Unexpected SRAM notifier activity during functional simulation')
    return {'codes': sorted(codes), 'reviewed_messages': reviewed}


def main():
    work_root = TASK / 'work'
    work_root.mkdir(exist_ok=True)
    result = Path(tempfile.mkdtemp(prefix='pe_axi_functional.', dir=work_root))
    print('RESULT=' + str(result), flush=True)
    summary = {
        'status': 'RUNNING',
        'scope': 'AXI control and full 16x16 PE array functional simulation',
        'sdf_annotated': False, 'specify_enabled': False,
        'timing_checks_enabled': False, 'timing_acceptance': False,
        'cpu_included': False, 'router_included': False,
        'formal_equivalence': False, 'physical_parasitics': False,
        'gate_model_mode': 'Audited compatible library copy, TETRAMAX branch; no NTC/RECREM',
        'functional_mode': {
            'gate_defines': ['TETRAMAX', 'PE_AXI_GATE_SIM'],
            'not_defined': ['NTC', 'RECREM'],
            'vcs_options': ['+nospecify'],
            'helper_timed_mode_options_applied': False,
            'note': 'The helper also records requirements for timed usage; this run only uses '
                    'its restored internal buffers with specify disabled.',
        },
        'child_address_space_limit_bytes': MEMORY_LIMIT,
        'cases': [], 'commands': [],
    }
    env = os.environ.copy()
    env['LC_ALL'] = 'C'

    def run(command, folder, log_name, phase, timeout):
        command = list(map(str, command))
        log_path = folder / log_name
        record = {'argv': command, 'cwd': str(folder), 'phase': phase,
                  'log': str(log_path), 'timeout_s': timeout}
        summary['commands'].append(record)
        print('PE_AXI_PHASE=' + phase, flush=True)
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
                        print('PE_AXI_RUNNING phase=' + phase + ' elapsed_s=' + str(int(elapsed)), flush=True)
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
            'synthesis_summary.rpt': EXPORT / 'synthesis_summary.rpt',
            'constraints.sdc': EXPORT / 'pe_axi_constraints.sdc',
            'tb.sv': PROJECT / 'tests/tb_pe_axi_gate.sv',
            'reference_sram.v': PROJECT / 'tb/sram_reference_model.v',
            'standard_model.v': TASK / 'libraries/standard_timing.v',
            'sram_model.v': TASK / 'libraries/sram_timing.v',
            'compat.c': PROJECT.parent / 'compat/pthread_yield_compat.c',
            'vcs_vm.py': TASK / 'scripts/vcs_vm.py',
            'runner.py': Path(__file__).resolve(),
            'prepare_pe_sdf_models.py': PROJECT / 'scripts/prepare_pe_sdf_models.py',
            'run.sh': PROJECT / 'synth/run_pe_axi_functional.sh',
            'run.tcl': PROJECT / 'synth/run_pe_axi_functional.tcl',
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
        snapshot = result / 'inputs'
        for name, path in sources.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha(target) != before[str(path)]:
                raise RuntimeError('Input changed while copying: ' + str(path))
        # Use this run's frozen netlist, never a potentially stale prior validation JSON.
        summary['netlist_structure'] = check_netlist(snapshot / 'netlist.v',
                                                    [snapshot / 'standard_model.v', snapshot / 'sram_model.v'])
        print('PE_AXI_INPUTS_FROZEN sram_instances=256', flush=True)
        summary['model_adaptation'] = prepare_models(snapshot / 'standard_model.v',
                                                     snapshot / 'sram_model.v', result / 'models')
        compat = result / 'pthread_yield_compat.o'
        run(['/usr/bin/gcc', '-fPIC', '-c', snapshot / 'compat.c', '-o', compat],
            result, 'compatibility.log', 'compatibility', 60)
        traces = []
        counts_by_case = []
        for name in ['rtl', 'gate']:
            case = result / name
            case.mkdir()
            if name == 'rtl':
                design_sources = [snapshot / 'rtl' / file for file in RTL_FILES]
                design_sources.append(snapshot / 'reference_sram.v')
                defines = ['+define+PE_REFERENCE_MACRO']
            else:
                design_sources = [snapshot / 'netlist.v', result / 'models/standard_model.v',
                                  result / 'models/sram_model.v']
                # This library-supported mode only removes auxiliary xbuf
                # feedback. The audited copy restores internal condition
                # buffers without input feedback. Actual combinational logic,
                # sequential UDPs and asynchronous reset remain active.
                # Specify is disabled here; no NTC or RECREM is defined.
                defines = ['+define+TETRAMAX+PE_AXI_GATE_SIM']
            compile_log = run([sys.executable, snapshot / 'vcs_vm.py', '-full64', '-sverilog',
                               '-timescale=1ns/1ps', '+nospecify', *defines, '+lint=TFIPC-L',
                               '-top', 'tb_pe_axi_gate', '-Mdir=' + str(case / 'csrc'),
                               '-o', case / 'simv', '-LDFLAGS', str(compat),
                               *design_sources, snapshot / 'tb.sv'], case, 'compile.log', name + '_compile', 1200)
            trace = case / 'trace.txt'
            simulation_log = run([case / 'simv', '+TRACE_FILE=' + str(trace)],
                                 case, 'run.log', name + '_simulate', 1200)
            diagnostics = validate_diagnostics(compile_log, simulation_log, name,
                                               snapshot / 'rtl/pe_array.v')
            matches = re.findall(r'^PE_AXI_TEST_PASS writes=(\d+) reads=(\d+) results=(\d+) resets=(\d+)$',
                                 simulation_log, re.M)
            if len(matches) != 1:
                raise RuntimeError('Missing unique AXI test completion marker: ' + name)
            writes, reads, results, resets = map(int, matches[0])
            if writes < 250 or reads < 350 or results != 192 or resets != 1:
                raise RuntimeError('Incomplete AXI test coverage: ' + name)
            coverage_matches = re.findall(
                r'^PE_AXI_COVERAGE pes=(\d+) sram_addresses=(\d+) byte_checks=(\d+) '
                r'errors=(\d+) aw_first=(\d+) w_first=(\d+) retained_results=(\d+)$',
                simulation_log, re.M)
            if len(coverage_matches) != 1:
                raise RuntimeError('Missing unique AXI coverage marker: ' + name)
            pe_count, address_count, byte_checks, errors, aw_first, w_first, retained = map(
                int, coverage_matches[0])
            if ((pe_count, address_count, byte_checks, errors, retained) != (256, 2, 5, 6, 64)
                    or aw_first < 100 or w_first < 100 or aw_first + w_first != writes):
                raise RuntimeError('Missing AXI scenarios: ' + name)
            if not trace.is_file() or not trace.stat().st_size:
                raise RuntimeError('Missing AXI trace: ' + name)
            trace_lines = trace.read_text().splitlines()
            trace_tags = Counter(line.split()[0] if line.split() else '' for line in trace_lines)
            if (len(trace_lines) != writes + reads + resets or
                    trace_tags != Counter({'W': writes, 'R': reads, 'RESET': resets})):
                raise RuntimeError('Incomplete AXI transaction trace: ' + name)
            if any(re.search(r'(?i)\b[0-9a-f]*[xz][0-9a-f]*\b', line) for line in trace_lines):
                raise RuntimeError('Unknown values in AXI trace: ' + name)
            counts = {'writes': writes, 'reads': reads, 'results': results, 'resets': resets}
            counts_by_case.append(counts)
            summary['cases'].append({'case': name, 'pass': True, 'checks': counts,
                                     'coverage': {'pe_instances': pe_count, 'sram_addresses': address_count,
                                                  'byte_strobe_checks': byte_checks, 'error_responses': errors,
                                                  'aw_before_w': aw_first, 'w_before_aw': w_first,
                                                  'retained_sram_results': retained},
                                     'diagnostic_codes': diagnostics['codes'],
                                     'reviewed_diagnostics': diagnostics['reviewed_messages'],
                                     'trace_lines': len(trace_lines), 'trace_sha256': sha(trace)})
            traces.append(trace)
            print('PE_AXI_' + name.upper() + '_FUNCTIONAL_PASS results=' + str(results), flush=True)
        if counts_by_case[0] != counts_by_case[1] or sha(traces[0]) != sha(traces[1]):
            raise RuntimeError('RTL and mapped AXI response traces or test counts differ')
        if any(sha(Path(path)) != expected for path, expected in before.items()):
            raise RuntimeError('Inputs changed during functional regression')
        summary.update(status='PASS', output_traces_match=True)
        print('PE_AXI_GATE_FUNCTIONAL_PASS', flush=True)
    except BaseException as exc:
        summary.update(status='FAIL', error=str(exc))
        raise
    finally:
        (result / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    def handle_termination(signum, frame):
        # A terminated front-end should not leave its detached VCS child alive.
        raise InterruptedError('Received signal ' + str(signum))

    signal.signal(signal.SIGTERM, handle_termination)
    try:
        main()
    except KeyboardInterrupt:
        print('PE_AXI_GATE_FUNCTIONAL_FAILED: interrupted', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print('PE_AXI_GATE_FUNCTIONAL_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
