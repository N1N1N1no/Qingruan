#!/usr/bin/env python3
"""Export matching single-PE netlist/SDF and check timed gate simulation."""
import hashlib
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from prepare_pe_sdf_models import prepare_models
from prepare_pe_sdf import prepare_sdf

PROJECT = Path(__file__).resolve().parents[1]
TASK = PROJECT.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_annotation(case, sdf_record):
    """Compare VCS's SDF-entry and applied-limit counts against the exact SDF."""
    report = case / 'sdfAnnotateInfo'
    text = report.read_text()
    headings = ['Static entries from SDF files:',
                'Annotated entries in elaborated design:',
                'Static entries in elaborated design:']
    blocks = [text.split(headings[i], 1)[1].split(headings[i + 1], 1)[0]
              for i in range(2)]
    counts = [dict((key.strip(), int(value)) for key, value in
                   re.findall(r'^#[ \t]+([^=\n]+?)[ \t]*=[ \t]*(\d+)[ \t]*$', block, re.M))
              for block in blocks]
    source, applied = counts
    entries = sdf_record['sdf_entries_after']
    expected_source = {key: value for key, value in entries.items() if value}
    checks = sum(entries[key] for key in entries if key not in {'IOPATH', 'INTERCONNECT'})
    expected_source['TIMINGCHECK(ALL)'] = checks
    if source != expected_source:
        raise RuntimeError('SDF summary source counts differ: ' + str(report))
    # VCS counts both limits of each combined $recrem check as applied checks.
    expected_limits = checks + entries['RECREM'] + entries['SETUPHOLD']
    sdf_text = (case / 'pe_mapped.sdf').read_text()
    negative_limits = 0
    for line in sdf_text.splitlines():
        if re.match(r'\s*\((?:SETUP|HOLD|RECREM|SETUPHOLD)\s', line):
            for value in re.findall(r'\(([-+0-9.eE]+):([-+0-9.eE]+):([-+0-9.eE]+)\)', line):
                negative_limits += Decimal(value[2]) < 0  # MAXIMUM selection
    leaf_cells = len(re.findall(r'\(CELLTYPE "', sdf_text)) - 1
    expected_applied = {'IOPATH Delays': entries['IOPATH'],
                        'INTERCONNECT(MIPD)': entries['INTERCONNECT'],
                        'Timing Checks': expected_limits,
                        'Negative Tchecks': negative_limits,
                        'Annotated instances': leaf_cells}
    if any(applied.get(key) != value for key, value in expected_applied.items()):
        raise RuntimeError('Incomplete SDF annotation counts: ' + str(report))
    if not expected_limits or not negative_limits:
        raise RuntimeError('Expected nonzero timing and negative timing checks')
    return {'source': source, 'applied': applied, 'expected_limits': expected_limits,
            'expected_negative_limits': negative_limits, 'report': str(report),
            'report_sha256': sha(report)}


def main():
    sources = {
        'pe_mapped.ddc': PROJECT / 'build/dc_pe_io/pe_mapped.ddc',
        'libraries/standard.db': PROJECT / 'build/dc_libraries/standard.db',
        'libraries/sram.db': PROJECT / 'build/dc_libraries/sram.db',
        'export_pe_sdf.tcl': PROJECT / 'synth/export_pe_sdf.tcl',
        'export_pe_sdf_batch.tcl': PROJECT / 'synth/export_pe_sdf_batch.tcl',
        'tb.sv': PROJECT / 'tests/tb_pe_gate_functional.sv',
        'reference_sram.v': PROJECT / 'tb/sram_reference_model.v',
        'standard_original.v': TASK / 'libraries/standard_timing.v',
        'sram_original.v': TASK / 'libraries/sram_timing.v',
        'compat.c': PROJECT.parent / 'compat/pthread_yield_compat.c',
        'vcs_vm.py': TASK / 'scripts/vcs_vm.py',
        'prepare_pe_sdf_models.py': PROJECT / 'scripts/prepare_pe_sdf_models.py',
        'prepare_pe_sdf.py': PROJECT / 'scripts/prepare_pe_sdf.py',
        'runner.py': Path(__file__).resolve(),
    }
    for file in ['rfsp16x8.v', 'Exact_8bit.v', 'adder.v', 'pe_1sram_4mul.v']:
        sources['rtl/' + file] = PROJECT / 'rtl' / file
    for path in sources.values():
        if not path.is_file():
            raise RuntimeError('Missing input: ' + str(path))
    work_root = TASK / 'work'
    work_root.mkdir(exist_ok=True)
    result = Path(tempfile.mkdtemp(prefix='pe_sdf.', dir=work_root))
    print('RESULT=' + str(result), flush=True)
    summary = {
        'status': 'RUNNING', 'scope': 'single PE, synthesis SDF, typical library corner',
        'sdf_triplet': 'MAXIMUM', 'sample_ns_after_posedge': 4.0,
        'soc_simulated': False, 'physical_parasitics': False,
        'formal_equivalence': False, 'cases': [],
    }
    before = {str(path): sha(path) for path in sources.values()}
    summary['input_sha256'] = before
    env = os.environ.copy()
    env['LC_ALL'] = 'C'

    def run(command, folder, log_name, timeout=240):
        with (folder / log_name).open('w') as log:
            completed = subprocess.run(list(map(str, command)), cwd=folder, env=env,
                                       stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=timeout)
        text = (folder / log_name).read_text(errors='replace')
        if completed.returncode or re.search(r'(?m)^\s*(?:Error(?:-\[|:)|Fatal:|FATAL:)', text):
            raise RuntimeError('Command failed; inspect ' + str(folder / log_name))
        return text

    try:
        snapshot = result / 'inputs'
        for name, path in sources.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha(target) != before[str(path)]:
                raise RuntimeError('Input changed while copying: ' + str(path))
        exported = result / 'export'
        exported.mkdir()
        env.update(PE_SDF_OUTPUT_DIR=str(exported),
                   PE_SDF_LIBRARY_DIR=str(snapshot / 'libraries'),
                   PE_SDF_DDC=str(snapshot / 'pe_mapped.ddc'))
        log = run(['dc_shell', '-f', snapshot / 'export_pe_sdf_batch.tcl'], exported, 'dc_export.log')
        if 'PE_SDF_EXPORTED ' not in log or 'PE_SDF_EXPORT_BATCH_DONE' not in log:
            raise RuntimeError('SDF export incomplete: ' + str(exported / 'dc_export.log'))
        sdf = exported / 'pe_mapped.sdf'
        netlist = exported / 'exported_pe.v'
        summary['export_sha256'] = {str(p): sha(p) for p in [sdf, netlist]}
        sdf_text = sdf.read_text()
        summary['sdf_entries'] = {key: len(re.findall(r'\(' + key + r'\s', sdf_text))
                                  for key in ['IOPATH', 'INTERCONNECT', 'SETUP', 'HOLD', 'RECOVERY']}
        summary['model_adaptation'] = prepare_models(snapshot / 'standard_original.v',
                                                     snapshot / 'sram_original.v', result / 'models')
        compat = result / 'pthread_yield_compat.o'
        run(['/usr/bin/gcc', '-fPIC', '-c', snapshot / 'compat.c', '-o', compat],
            result, 'compatibility.log')
        traces = []
        for name in ['rtl', 'gate']:
            case = result / name
            case.mkdir()
            if name == 'rtl':
                design_sources = [snapshot / ('rtl/' + file) for file in
                                  ['rfsp16x8.v', 'Exact_8bit.v', 'adder.v', 'pe_1sram_4mul.v']]
                design_sources += [snapshot / 'reference_sram.v']
                options = ['+nospecify', '+define+PE_REFERENCE_MACRO+PE_TIMING_SAMPLE']
                runtime_options = []
            else:
                summary['sdf_adaptation'] = prepare_sdf(sdf, case / 'pe_mapped.sdf')
                design_sources = [netlist, result / 'models/standard_model.v',
                                  result / 'models/sram_model.v']
                options = ['+define+TETRAMAX+NTC+RECREM+PE_TIMING_SAMPLE+PE_SDF_SIM',
                           '+neg_tchk', '+sdfverbose', '+sdfsummary', '+lint=TFIPC-L']
                runtime_options = ['+sdfverbose', '+sdfsummary']
            compile_log = run([sys.executable, snapshot / 'vcs_vm.py', '-full64', '-sverilog',
                 '-timescale=1ns/1ps', *options,
                 '-top', 'tb_pe_gate_functional', '-Mdir=' + str(case / 'csrc'),
                 '-o', case / 'simv', '-LDFLAGS', str(compat),
                 *design_sources, snapshot / 'tb.sv'], case, 'compile.log')
            trace = case / 'outputs.txt'
            log = run([case / 'simv', '+TRACE_FILE=' + str(trace), *runtime_options], case, 'run.log')
            match = re.search(
                r'^PE_FUNCTIONAL_TEST_PASS cycles=(\d+) pairs=(\d+) reset_checks=(\d+)$',
                log, re.M)
            if not match or re.search(r'SRAM_TIMING_VIOLATION|Timing violation|Timing Violation', log):
                raise RuntimeError('Simulation check failed; inspect ' + str(case / 'run.log'))
            cycles, pairs, resets = map(int, match.groups())
            if pairs != 65536 or cycles < 18000 or resets != 1:
                raise RuntimeError('Test coverage incomplete: ' + name)
            if len(trace.read_text().splitlines()) != cycles:
                raise RuntimeError('Incomplete output trace: ' + name)
            if name == 'gate':
                annotation = case / 'sdf_annotation.log'
                if not annotation.is_file() or 'PE_SDF_DELAY_OBSERVED ' not in log:
                    raise RuntimeError('No completed SDF annotation/delay observation')
                annotations = annotation.read_text(errors='replace')
                warning_codes = set(re.findall(r'(?:Warning|Lint)-\[([^]]+)\]', compile_log + log))
                if warning_codes - {'LINX_KRNL', 'TFIPC-L'} or re.search(
                        r'(?im)^\s*(?:Warning|Error|Fatal)(?:-|:)', annotations):
                    raise RuntimeError('Review gate diagnostics: ' + str(case))
                if (re.findall(r'Total errors:\s*(\d+)', annotations) != ['0'] or
                        re.findall(r'Total warnings:\s*(\d+)', annotations) != ['0']):
                    raise RuntimeError('SDF annotation has errors/warnings: ' + str(annotation))
                # Check the detailed missing-port messages too: only the unused
                # QN of flops and Cout of the top adders may be unconnected.
                port_messages = re.findall(r'Lint-\[TFIPC-L\](.*?)(?=\n\n)', compile_log, re.S)
                for message in port_messages:
                    missing_ports = re.findall(
                        r"\b(input|output|inout) port '([^']+)' is not connected", message)
                    if not missing_ports or any(direction != 'output' or port not in {'QN', 'Cout'}
                                                for direction, port in missing_ports):
                        raise RuntimeError('Review missing-port details: ' + str(case / 'compile.log'))
                summary['gate_warning_codes'] = sorted(warning_codes)
                summary['annotation'] = validate_annotation(case, summary['sdf_adaptation'])
                summary['observed_delay'] = re.search(r'^PE_SDF_DELAY_OBSERVED .+$', log, re.M).group(0)
                print('PE_SDF_ANNOTATION_PASS paths=' + str(summary['sdf_entries']['IOPATH']) +
                      ' timing_limits=' + str(summary['annotation']['expected_limits']), flush=True)
                probe = result / 'timing_probe'
                probe.mkdir()
                shutil.copyfile(case / 'pe_mapped.sdf', probe / 'pe_mapped.sdf')
                probe_log = run([case / 'simv', '+TRACE_FILE=' + str(probe / 'outputs.txt'),
                                 '+SDF_TIMING_PROBE'], probe, 'run.log')
                # VCS combines the SRAM's separate setup/hold checks when it
                # reports them. Confirm that the data actually changed within
                # the annotated hold window, not just any unrelated warning.
                violation = re.search(
                    r'\$setuphold\(\s*posedge clk:(\d+),\s*wd_in\[0\]:(\d+),'
                    r'\s*limits:\s*\((\d+),(\d+)\)', probe_log)
                if ('PE_SDF_TIMING_PROBE_DONE' not in probe_log or
                        'SRAM_TIMING_VIOLATION' not in probe_log or
                        'Timing violation' not in probe_log or not violation):
                    raise RuntimeError('Timing-check negative control did not detect the violation: ' + str(probe))
                clock_fs, data_fs, setup_fs, hold_fs = map(int, violation.groups())
                if not (0 < data_fs - clock_fs < hold_fs and setup_fs == 9000 and hold_fs == 1000):
                    raise RuntimeError('Negative control reported an unexpected timing window: ' + str(probe))
                summary['timing_negative_control'] = {
                    'pass': True, 'expected_violation': 'SRAM hold: change 0.1 ps after macro clock, limit 1 ps',
                    'data_change_after_clock_fs': data_fs - clock_fs, 'hold_limit_fs': hold_fs,
                    'log': str(probe / 'run.log'), 'log_sha256': sha(probe / 'run.log')}
                print('PE_SDF_TIMING_CHECK_ACTIVE', flush=True)
            traces.append(trace)
            summary['cases'].append({'case': name, 'pass': True, 'checked_cycles': cycles,
                                     'operand_pairs': pairs, 'trace_sha256': sha(trace)})
            print('PE_' + name.upper() + '_TIMED_CHECK_PASS cycles=' + str(cycles), flush=True)
        if sha(traces[0]) != sha(traces[1]):
            raise RuntimeError('RTL and timed gate output traces differ: ' + str(result))
        if any(sha(Path(path)) != expected for path, expected in before.items()):
            raise RuntimeError('Inputs changed during the test')
        summary.update(status='PASS', output_traces_match=True)
        print('PE_GATE_SDF_PASS', flush=True)
    except BaseException as exc:
        summary.update(status='FAIL', error=str(exc))
        raise
    finally:
        (result / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PE_GATE_SDF_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
