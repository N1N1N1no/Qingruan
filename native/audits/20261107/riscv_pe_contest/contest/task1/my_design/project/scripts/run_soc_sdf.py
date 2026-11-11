#!/usr/bin/env python3
"""Run the unchanged CPU smoke program in RTL and the maximum whole-SoC SDF.

Uses synthesis estimates at one library corner; this is not physical signoff.
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from prepare_pe_sdf_models import prepare_models
from prepare_soc_sdf import prepare_soc_sdf, validate_soc_annotation
from run_soc_functional import (PROJECT, DESIGN, TASK, MEMORY_LIMIT, MAX_LOG_BYTES,
    sha, save, merge_hashes, check_hashes, select_source, source_tree_files,
    check_netlist, limit_child, stop_child, read_log, inspect_simulation)
from run_pe_axi_sdf import compiled_runtime_files


def select_export(explicit=None):
    candidates = [Path(explicit).resolve()] if explicit else sorted(
        (TASK / 'work').glob('soc_sdf_export.*'), key=lambda p: p.stat().st_mtime, reverse=True)
    rejected = []
    for case in candidates:
        try:
            report = case / 'summary.json'
            record = json.loads(report.read_text())
            if record.get('status') != 'PASS':
                raise RuntimeError('Export is not PASS')
            hashes = merge_hashes(record['source_dependencies_sha256'], record['input_sha256'],
                {str(case / n): d for n, d in record['output_sha256'].items()},
                {str(report): sha(report)})
            for name in ('dc/soc_mapped.sdf', 'dc/soc_mapped.v', 'dc/soc_mapped.ddc', 'dc/soc_constraints.sdc'):
                if str(case / name) not in hashes:
                    raise RuntimeError('Missing required export provenance: ' + name)
            check_hashes(hashes)
            return case, record, hashes, rejected
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            rejected.append({'case': str(case), 'reason': str(error)})
    if explicit:
        raise RuntimeError('Requested SDF export is not intact: ' + json.dumps(rejected))
    return None, None, None, rejected


def validate_probe(log, model_path):
    scope = 'tb_soc.dut.u_pe.u_pe_array.GEN_ROW_0__GEN_COL_0__u_pe.u_sram.u_macro'
    violations = re.findall(r'(?m)^"([^"\n]+)", (\d+): Timing violation in (\S+)$', log)
    notifiers = re.findall(r'(?m)^SRAM_TIMING_VIOLATION instance=(\S+) time_ns=([0-9.]+)$', log)
    windows = re.findall(r'\$setuphold\(\s*posedge clk:(\d+),\s*wd_in\[0\]:(\d+),'
                         r'\s*limits:\s*\((\d+),(\d+)\)', log)
    if (len(violations) != 1 or Path(violations[0][0]).resolve() != model_path.resolve() or
            violations[0][2] != scope or len(notifiers) != 1 or notifiers[0][0] != scope or
            len(windows) != 1 or log.count('Timing violation') != 1 or
            log.count('SRAM_TIMING_VIOLATION') != 1 or
            re.findall(r'(?m)^SOC_SDF_TIMING_PROBE_DONE$', log) != ['SOC_SDF_TIMING_PROBE_DONE'] or
            'SOC_SMOKE_PASS' in log or re.search(r'(?im)^\s*(?:Warning|Lint)(?:-|:)', log)):
        raise RuntimeError('Timing probe did not report exactly its deliberate SRAM hold violation')
    clock, data, setup, hold = map(int, windows[0])
    if (data-clock, setup, hold) != (100, 9000, 1000):
        raise RuntimeError('Unexpected annotated SRAM timing window in negative control')
    if abs(Decimal(notifiers[0][1])-Decimal(data)/1000000) > Decimal('0.000001'):
        raise RuntimeError('Timing notifier timestamp differs from the injected hold event')
    return {'pass': True, 'scope': scope, 'data_change_after_clock_fs': data-clock,
            'setup_limit_fs': setup, 'hold_limit_fs': hold,
            'timing_violations': 1, 'sram_notifier_events': 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-case', type=Path)
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    case = Path(tempfile.mkdtemp(prefix='soc_sdf.', dir=TASK / 'work'))
    print('RESULT=' + str(case), flush=True)
    inputs = case / 'inputs'
    inputs.mkdir()
    summary = {'status': 'RUNNING', 'scope': 'CPU smoke program in original RTL versus whole mapped SoC with synthesis SDF',
        'cpu_included': True, 'router_included': True, 'sdf_annotated': False,
        'sdf_triplet': 'MAXIMUM', 'library_corner': 'typical', 'clock_period_ns': 10,
        'external_memory_response_delay_ns': 1, 'specify_enabled_for_gate': True,
        'timing_checks_enabled_for_gate': True, 'negative_timing_checks_enabled': True,
        'external_axi_receiver_setup_check_enabled': True,
        'gate_defines': ['TETRAMAX', 'NTC', 'RECREM'],
        'sdf_annotation_owner': 'soc_sdf_observer, fixed filename; no +SDF runtime argument',
        'original_tb_dynamic_annotation_branch_enabled': False,
        'gate_timing_options': ['-negdelay', '+neg_tchk', '+sdfverbose', '+sdfsummary'],
        'formal_equivalence_checked': False, 'physical_parasitics_used': False,
        'physical_signoff_established': False, 'multicorner_verified': False,
        'child_address_space_limit_bytes': MEMORY_LIMIT, 'commands': [], 'cases': []}
    save(case / 'summary.json', summary)
    env = os.environ.copy()
    for name in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKEFILES'):
        env.pop(name, None)
    env.update(LC_ALL='C', PYTHONDONTWRITEBYTECODE='1')

    def run(argv, cwd, log_path, phase, timeout, allow_timing_violation=False):
        argv = list(map(str, argv))
        record = {'argv': argv, 'cwd': str(cwd), 'log': str(log_path), 'phase': phase, 'timeout_s': timeout}
        summary['commands'].append(record)
        save(case / 'summary.json', summary)
        print('SOC_SDF_PHASE=' + phase, flush=True)
        start = time.monotonic()
        with log_path.open('w') as stream:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=limit_child)
            try:
                while True:
                    remaining = timeout-(time.monotonic()-start)
                    if remaining <= 0:
                        raise RuntimeError('Timeout during ' + phase + '; inspect ' + str(log_path))
                    try:
                        code = process.wait(timeout=min(15, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if log_path.stat().st_size > MAX_LOG_BYTES:
                            raise RuntimeError('Too many diagnostics during ' + phase)
                        print('SOC_SDF_RUNNING phase=' + phase + ' elapsed_s=' +
                              str(int(time.monotonic()-start)), flush=True)
            except BaseException:
                stop_child(process)
                record.update(exit_code=process.returncode, elapsed_s=round(time.monotonic()-start, 3))
                raise
        record.update(exit_code=code, elapsed_s=round(time.monotonic()-start, 3))
        text = read_log(log_path)
        if code or re.search(r'(?im)^\s*(?:Error(?:-\[|:)|Fatal:|FATAL:)|'
                             r'SOC_PROGRAM_MONITOR_FAIL|SOC_SMOKE_FAIL|SOC_SDF_DELAY_FAIL|SOC_SDF_OBSERVER_FAIL|TIMEOUT after', text):
            raise RuntimeError('Command failed during ' + phase + '; inspect ' + str(log_path))
        if not allow_timing_violation and re.search(r'(?i)timing[_ ]violation', text):
            raise RuntimeError('Timing violation during ' + phase + '; inspect ' + str(log_path))
        return text

    try:
        exported, export_summary, dependencies, rejected = select_export(args.export_case)
        if exported is None:
            mapped, _, _, _, _ = select_source()
            exported = Path(tempfile.mkdtemp(prefix='soc_sdf_export.', dir=TASK / 'work'))
            run([sys.executable, PROJECT / 'scripts/export_soc_sdf.py', '--source', mapped,
                 '--output', exported], case, case / 'export.log', 'export_whole_soc_sdf', 1800)
            exported, export_summary, dependencies, _ = select_export(exported)
        summary.update(source_soc_sdf_export_case=str(exported), source_dependencies_sha256=dependencies,
                       rejected_export_cases=rejected)
        mapped = Path(export_summary['source_soc_compile_case'])
        mapped_summary = json.loads((mapped / 'summary.json').read_text())
        read_summary = json.loads((Path(mapped_summary['source_soc_read_case']) / 'summary.json').read_text())
        files = {
            'original.sdf': exported / 'dc/soc_mapped.sdf',
            'soc_mapped.v': exported / 'dc/soc_mapped.v',
            'soc_constraints.sdc': exported / 'dc/soc_constraints.sdc',
            'export_summary.json': exported / 'summary.json',
            'source_compile_summary.json': mapped / 'summary.json',
            'standard_model.v': TASK / 'libraries/standard_timing.v',
            'sram_model.v': TASK / 'libraries/sram_timing.v',
            'vcs_vm.py': TASK / 'scripts/vcs_vm.py',
            'compat.c': DESIGN / 'compat/pthread_yield_compat.c',
            'tb_soc.sv': PROJECT / 'tb/tb_soc.sv',
            'axi_ram_model.sv': PROJECT / 'tb/axi_ram_model.sv',
            'reference_sram.v': PROJECT / 'tb/sram_reference_model.v',
            'soc_program_monitor.sv': PROJECT / 'tests/soc_program_monitor.sv',
            'soc_sdf_observer.sv': PROJECT / 'tests/soc_sdf_observer.sv',
        }
        for name in ('run_soc_sdf.py', 'run_soc_functional.py', 'run_pe_axi_sdf.py', 'run_pe_axi_functional.py',
                     'prepare_soc_sdf.py', 'prepare_pe_axi_sdf.py', 'prepare_pe_sdf_models.py',
                     'validate_pe_axi_sdf_warnings.py', 'validate_soc_sdf_warnings.py'):
            files[name] = PROJECT / 'scripts' / name
        for name in ('run_soc_sdf.sh', 'run_soc_sdf.tcl'):
            files[name] = PROJECT / 'synth' / name
        for index, path in enumerate((TASK.parent / 'env.sh', TASK.parent / 'runtime/env.vm.sh',
                                      TASK.parent / 'runtime/env.scl2018.sh', TASK.parent / 'runtime/env.dcpt.sh')):
            files[f'environment_{index}.sh'] = path
        live_hashes = {str(path): sha(path) for path in files.values()}
        merge_hashes(dependencies, live_hashes)
        summary['input_sha256'] = live_hashes
        for name, path in files.items():
            shutil.copyfile(path, inputs / name)
            if sha(inputs / name) != live_hashes[str(path)]:
                raise RuntimeError('Input changed while freezing: ' + str(path))
        summary['netlist_structure'] = check_netlist(inputs / 'soc_mapped.v',
            [inputs / 'standard_model.v', inputs / 'sram_model.v'])
        working = case / 'source'
        shutil.copytree(DESIGN, working, ignore=shutil.ignore_patterns(
            'build', '.git', '__pycache__', '*.pyc', '*.vpd', '*.vcd', 'ucli.key'))
        original_sources = {str(p): sha(p) for p in source_tree_files(DESIGN)}
        frozen_sources = {str(p.relative_to(working)): sha(p) for p in source_tree_files(working)}
        if {str(Path(p).relative_to(DESIGN)): d for p, d in original_sources.items()} != frozen_sources:
            raise RuntimeError('The frozen source tree differs from the production source')
        summary.update(source_tree_sha256=original_sources, frozen_source_tree_sha256=frozen_sources)
        correspondence = {}
        for name, digest in read_summary['conversion']['input_sha256'].items():
            path = Path(name)
            if path.is_relative_to(DESIGN) and 'build' not in path.relative_to(DESIGN).parts:
                if sha(working / path.relative_to(DESIGN)) != digest:
                    raise RuntimeError('The CPU/PE RTL or configuration differs from synthesis: ' + name)
                correspondence[name] = digest
        summary['synthesis_source_correspondence_sha256'] = correspondence
        project, vendor = working / 'project', working / 'vendor/Cores-VeeR-EL2'
        env['RV_ROOT'] = str(vendor)
        common = ['PREPARE_ARGS=--skip-config', 'RV_ROOT=' + str(vendor), 'PYTHON=' + sys.executable,
                  'RISCV_PREFIX=' + env.get('RISCV_PREFIX', 'riscv-none-elf-'), 'ISA=rv32imc_zicsr']
        run(['make', 'prepare', *common], project, case / 'prepare.log', 'prepare_frozen_configuration', 120)
        restored = (project / 'build/synth.f').read_text().replace(str(project), str(PROJECT)).replace(
            str(vendor), str(DESIGN / 'vendor/Cores-VeeR-EL2'))
        expected = read_summary['conversion']['input_sha256'][str(PROJECT / 'build/synth.f')]
        import hashlib
        if hashlib.sha256(restored.encode()).hexdigest() != expected:
            raise RuntimeError('Prepared source order differs from the mapped design source order')
        summary['normalized_synthesis_source_list_sha256'] = expected
        run(['make', '-B', 'sw', *common], project, case / 'firmware.log', 'compile_riscv_firmware', 120)
        firmware = inputs / 'program.hex'
        shutil.copyfile(project / 'build/program.hex', firmware)
        summary['firmware_hex_sha256'] = sha(firmware)
        summary['firmware_artifacts_sha256'] = {str(p.relative_to(case)): sha(p)
            for p in (project / 'build').iterdir() if p.is_file() and
            p.name in {'program.hex', 'program.bin', 'pe_smoke.elf', 'pe_smoke.dis', 'pe_smoke.map', 'rtl.f', 'synth.f'}}
        summary['model_adaptation'] = prepare_models(inputs / 'standard_model.v', inputs / 'sram_model.v', case / 'models')
        gate = case / 'gate'
        gate.mkdir()
        summary['sdf_adaptation'] = prepare_soc_sdf(inputs / 'original.sdf', gate / 'soc.sdf', inputs / 'soc_mapped.v')
        compat = case / 'pthread_yield_compat.o'
        run(['/usr/bin/gcc', '-fPIC', '-c', inputs / 'compat.c', '-o', compat],
            case, case / 'compatibility.log', 'compile_vcs_host_compatibility', 60)
        traces, checks = [], []
        from validate_soc_sdf_warnings import validate_diagnostics
        for name in ('rtl', 'gate'):
            folder = case / name
            folder.mkdir(exist_ok=True)
            options = [sys.executable, inputs / 'vcs_vm.py', '-full64', '-sverilog',
                '-timescale=1ns/1fs', '+define+VCS', '-debug_access',
                '+define+AXI_RX_TIMING_CHECK', '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0',
                '+incdir+' + str(project / 'snapshots/soc'), '-assert', 'svaext',
                '-top', 'tb_soc', '-top', 'soc_program_monitor',
                '-Mdir=' + str(folder / 'csrc'), '-o', folder / 'simv', '-LDFLAGS', str(compat)]
            if name == 'rtl':
                sources = ['+nospecify', '+define+PE_REFERENCE_MACRO', '-f', project / 'build/rtl.f',
                           inputs / 'reference_sram.v']
            else:
                # GATE_SIM only selects the original TB's variable-filename
                # $sdf_annotate task. VCS 2016 rejects that unused task during
                # compile-time annotation; the third top owns the sole fixed
                # annotation call, so keep the original TB branch disabled.
                options += ['+define+TETRAMAX+NTC+RECREM', '-negdelay', '+neg_tchk', '+sdfverbose',
                            '+sdfsummary', '-top', 'soc_sdf_observer']
                sources = [inputs / 'soc_mapped.v', case / 'models/standard_model.v', case / 'models/sram_model.v',
                           inputs / 'soc_sdf_observer.sv']
            compile_log = run([*options, *sources, inputs / 'axi_ram_model.sv', inputs / 'tb_soc.sv',
                               inputs / 'soc_program_monitor.sv'], folder, folder / 'compile.log', name + '_compile', args.timeout)
            runtime_before = compiled_runtime_files(folder) if name == 'gate' else None
            trace = folder / 'trace.tsv'
            log = run([folder / 'simv', '+MEM_HEX=' + str(firmware), '+TRACE=' + str(trace)],
                      folder, folder / 'run.log', name + '_simulate', args.timeout)
            counts = inspect_simulation(log, trace, name)
            diagnostics = validate_diagnostics(case, name)
            summary['cases'].append({'case': name, 'pass': True, 'checks': counts,
                                     'diagnostics': diagnostics})
            checks.append(counts)
            traces.append(trace)
            if name == 'gate':
                annotation = validate_soc_annotation(folder, summary['sdf_adaptation'],
                    reviewed_warning_count=diagnostics.get('reviewed_annotation_warning_count', 0))
                summary.update(annotation=annotation, sdf_annotated=True)
                matches = re.findall(r'^SOC_SDF_DELAY_OBSERVED events=(\d+) first_sram_ns=([0-9.eE+-]+)$', log, re.M)
                if len(matches) != 1 or int(matches[0][0]) <= 0 or not Decimal('0') < Decimal(matches[0][1]) < Decimal('5'):
                    raise RuntimeError('No observed nonzero annotated SRAM delay during CPU execution')
                summary['observed_delay'] = {'events': int(matches[0][0]), 'first_sram_ns': matches[0][1]}
                interconnect = re.findall(
                    r'^SOC_SDF_INTERCONNECT_DELAY_OBSERVED events=(\d+) min_ns=([0-9.eE+-]+) max_ns=([0-9.eE+-]+)$', log, re.M)
                representative = diagnostics['swc_review']['representative_delay']
                if len(interconnect) != 1 or int(interconnect[0][0]) < 2:
                    raise RuntimeError('No observed delay on the reviewed cross-hierarchy INTERCONNECT')
                expected_delays = sorted(Decimal(representative[key]) for key in ('rise_ns', 'fall_ns'))
                actual_delays = [Decimal(value) for value in interconnect[0][1:]]
                if any(not value.is_finite() for value in actual_delays) or any(
                        abs(actual-expected) > Decimal('0.000001')
                        for actual, expected in zip(actual_delays, expected_delays)):
                    raise RuntimeError('Observed reviewed INTERCONNECT delays differ from exact SDF values')
                summary['observed_reviewed_interconnect_delay'] = {
                    'events': int(interconnect[0][0]), 'observed_min_ns': str(actual_delays[0]),
                    'observed_max_ns': str(actual_delays[1]), 'expected': representative,
                    'comparison_tolerance_ns': '0.000001'}
                if compiled_runtime_files(folder) != runtime_before:
                    raise RuntimeError('The annotated gate runtime changed during normal simulation')
                annotation_hashes = {n: sha(folder / n) for n in ('sdfAnnotateInfo', 'sdf_annotation.log')}
                probe = case / 'timing_probe'
                probe.mkdir()
                os.link(folder / 'soc.sdf', probe / 'soc.sdf')
                probe_log = run([folder / 'simv', '+MEM_HEX=' + str(firmware),
                    '+TRACE=' + str(probe / 'trace.tsv'), '+SOC_SDF_TIMING_PROBE'],
                    probe, probe / 'run.log', 'timing_negative_control', args.timeout, allow_timing_violation=True)
                probe_record = validate_probe(probe_log, case / 'models/sram_model.v')
                if compiled_runtime_files(folder) != runtime_before:
                    raise RuntimeError('The same compiled gate runtime was not retained through the timing probe')
                if any(sha(folder / n) != h for n, h in annotation_hashes.items()):
                    raise RuntimeError('Normal annotation reports changed during the negative control')
                sdf_hash = summary['sdf_adaptation']['simulation_sdf_sha256']
                if sha(folder / 'soc.sdf') != sdf_hash or sha(probe / 'soc.sdf') != sdf_hash:
                    raise RuntimeError('The negative control did not use the exact normal SDF')
                if any((probe / n).exists() for n in annotation_hashes):
                    raise RuntimeError('Unexpected regenerated annotation reports in the probe require review')
                summary['compiled_gate_runtime'] = {'files': runtime_before, 'file_count': len(runtime_before),
                    'verified_before_and_after_normal_and_probe': True}
                summary['timing_negative_control'] = {**probe_record, 'same_compiled_gate_runtime_verified': True,
                    'compile_time_annotation_reused': True, 'probe_annotation_reports_regenerated': False,
                    'simulation_sdf_sha256': sdf_hash, 'log': str(probe / 'run.log'),
                    'normal_annotation_reports_sha256': annotation_hashes}
            save(case / 'summary.json', summary)
            print('SOC_' + name.upper() + '_SDF_PROGRAM_CHECK_PASS pe_reads=64 pe_writes=87', flush=True)
        if checks[0] != checks[1] or traces[0].read_bytes() != traces[1].read_bytes():
            raise RuntimeError('Original RTL and SDF SoC accepted-transaction traces differ')
        check_hashes(merge_hashes(dependencies, live_hashes, original_sources))
        check_hashes({str(case / p): h for p, h in summary['firmware_artifacts_sha256'].items()})
        if {str(p.relative_to(working)): sha(p) for p in source_tree_files(working)} != frozen_sources:
            raise RuntimeError('The isolated source tree changed during the regression')
        for name, path in files.items():
            if sha(inputs / name) != live_hashes[str(path)]:
                raise RuntimeError('A frozen input changed: ' + name)
        if sha(firmware) != summary['firmware_hex_sha256']:
            raise RuntimeError('The firmware changed during the regression')
        summary.update(status='PASS', output_traces_match=True, normal_timing_violations=0,
            soc_program_simulated=True, reference_pe_output_words_checked=64, pe_config_writes_checked=87,
            output_sha256={str(p.relative_to(case)): sha(p) for n in ('rtl', 'gate', 'timing_probe')
                for p in (case / n).iterdir() if p.is_file() and p.name in
                {'compile.log', 'run.log', 'trace.tsv', 'sdf_annotation.log', 'sdfAnnotateInfo'}})
        save(case / 'summary.json', summary)
        print('SOC_GATE_SDF_PASS', flush=True)
        print('SOC_SDF_SCOPE: current C smoke program; MAXIMUM triplet at typical synthesis corner; no physical parasitics or formal equivalence.', flush=True)
        return 0
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(case / 'summary.json', summary)
        print('SOC_GATE_SDF_FAILED: ' + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    def terminated(signum, frame):
        raise InterruptedError('Received signal ' + str(signum))
    signal.signal(signal.SIGTERM, terminated)
    raise SystemExit(main())
