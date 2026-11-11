#!/usr/bin/env python3
"""Apply and verify whole-SoC boundary assumptions without compiling logic."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from run_soc_read import PROJECT, TASK, LIMIT, sha, save, limit_child, stop_child, bit_ports
from verify_soc_constraints import verilog_token_hash, verify_port_report, verify_saved_clock_sdc


def select_source():
    for folder in sorted((TASK / 'work').glob('soc_read.*'),
                         key=lambda path: path.stat().st_mtime, reverse=True):
        report = folder / 'summary.json'
        if not report.is_file():
            continue
        data = json.loads(report.read_text())
        if (data.get('status') != 'PASS' or data.get('cpu_control_mapped') is not False or
                data.get('dc_structure', {}).get('top') != 'soc_top'):
            continue
        dependencies = {**data['input_sha256'], **data['reused_accelerator_source_sha256'],
                        **data['conversion']['input_sha256']}
        if any(not Path(path).is_file() or sha(path) != expected
               for path, expected in dependencies.items()):
            continue
        if any(not (folder / name).is_file() or sha(folder / name) != data['output_sha256'][name]
               for name in ('dc/soc_elaborated.ddc', 'dc/soc_elaborated.v')):
            continue
        return folder, data, dependencies
    raise RuntimeError('No passing SoC read result matches the current design; run run_soc_read.tcl first')


def main():
    case = Path(tempfile.mkdtemp(prefix='soc_constraints.', dir=TASK / 'work'))
    print('RESULT=' + str(case), flush=True)
    inputs, dc = case / 'inputs', case / 'dc'
    inputs.mkdir()
    dc.mkdir()
    summary = {'status': 'RUNNING', 'scope': 'Whole-SoC clock and I/O assumptions only',
               'cpu_included': True, 'router_included': True, 'compile_performed': False,
               'soc_mapped': False, 'sta_acceptance_established': False,
               'soc_program_simulated': False, 'formal_equivalence_checked': False,
               'child_address_space_limit_bytes': LIMIT, 'commands': []}
    env = os.environ.copy()
    env.update(LC_ALL='C', SOC_CONSTRAINTS_CASE=str(case))

    def run(command, phase, timeout=900):
        record = {'argv': list(map(str, command)), 'cwd': str(dc), 'phase': phase,
                  'timeout_s': timeout, 'log': str(dc / 'run.log')}
        summary['commands'].append(record)
        print('SOC_CONSTRAINTS_PHASE=' + phase, flush=True)
        started = time.monotonic()
        with (dc / 'run.log').open('w') as log:
            process = subprocess.Popen(record['argv'], cwd=dc, env=env,
                                       stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       preexec_fn=limit_child)
            try:
                while True:
                    try:
                        code = process.wait(timeout=15)
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = time.monotonic() - started
                        print(f'SOC_CONSTRAINTS_RUNNING elapsed_s={int(elapsed)}', flush=True)
                        if elapsed >= timeout:
                            raise RuntimeError('DC constraints stage exceeded its time limit')
            except BaseException:
                stop_child(process)
                record.update(exit_code=process.returncode, elapsed_s=round(time.monotonic()-started, 2))
                raise
        record.update(exit_code=code, elapsed_s=round(time.monotonic()-started, 2))
        if code:
            raise RuntimeError('DC constraints stage failed; inspect ' + str(dc / 'run.log'))

    try:
        source, prior, source_dependencies = select_source()
        summary['source_soc_read_case'] = str(source)
        summary['source_dependencies_sha256'] = source_dependencies
        conversion = prior['conversion']
        original_ports = bit_ports({'ports': {
            name: {'direction': port['direction'], 'bits': list(range(port['width']))}
            for name, port in conversion['original_top_ports'].items()}})
        if len(original_ports) != 378:
            raise RuntimeError('Unexpected original SoC port interface')
        files = {
            'soc_elaborated.ddc': source / 'dc/soc_elaborated.ddc',
            'soc_elaborated.v': source / 'dc/soc_elaborated.v',
            'source_summary.json': source / 'summary.json',
            'standard.db': PROJECT / 'build/dc_libraries/standard.db',
            'sram.db': PROJECT / 'build/dc_libraries/sram.db',
            'soc_io.sdc': PROJECT / 'synth/soc_io.sdc',
            'constrain_soc_batch.tcl': PROJECT / 'synth/constrain_soc_batch.tcl',
            'run_soc_constraints.py': Path(__file__),
            'verify_soc_constraints.py': PROJECT / 'scripts/verify_soc_constraints.py',
            'run_soc_read.py': PROJECT / 'scripts/run_soc_read.py',
            'run_soc_constraints.sh': PROJECT / 'synth/run_soc_constraints.sh',
            'run_soc_constraints.tcl': PROJECT / 'synth/run_soc_constraints.tcl',
            'reference_soc_10ns.sdc': TASK / 'libraries/soc_10ns.sdc',
        }
        for index, path in enumerate((TASK.parent / 'env.sh', TASK.parent / 'runtime/env.vm.sh',
                                      TASK.parent / 'runtime/env.scl2018.sh', TASK.parent / 'runtime/env.dcpt.sh')):
            files[f'environment_{index}.sh'] = path
        hashes = {str(path): sha(path) for path in files.values()}
        summary['input_sha256'] = hashes
        for name, path in files.items():
            shutil.copyfile(path, inputs / name)
            if sha(inputs / name) != hashes[str(path)]:
                raise RuntimeError('Input changed while freezing: ' + str(path))
        for name in ('standard.db', 'sram.db'):
            if hashes[str(files[name])] != source_dependencies.get(str(files[name])):
                raise RuntimeError('Library differs from the saved SoC input: ' + name)
        structure = prior['dc_structure']
        expected = {
            'expected_top': 'soc_top', 'expected_top_inputs': 154,
            'expected_data_inputs': 152, 'expected_output_bits': 224,
            'expected_unmapped_count': int(structure['cpu_router_unmapped_leaf_cells']),
            'expected_pe_count': int(structure['pe_instances']),
            'expected_sram_count': int(structure['sram_instances']),
            'expected_wrapper_top': structure['mapped_pe_reference'],
            'expected_array_reference': prior['conversion']['pe_boundary'].get(
                'mapped_array_reference', structure.get('array_reference', '')),
        }
        # Earlier source summaries store the reference in their export manifest.
        if not expected['expected_array_reference']:
            export = json.loads((PROJECT / 'build/dc_pe_axi_export/artifact_summary.json').read_text())
            expected['expected_array_reference'] = export['source_design']['array_reference']
        for value in expected.values():
            if not re.fullmatch(r'[A-Za-z0-9_]+', str(value)):
                raise RuntimeError('Invalid expectation value')
        (case / 'expected.tcl').write_text(''.join('set ' + name + ' {' + str(value) + '}\n'
                                                  for name, value in expected.items()))
        expected_hash = sha(case / 'expected.tcl')
        summary['original_top_ports'] = original_ports
        run(['dc_shell', '-f', str(inputs / 'constrain_soc_batch.tcl')], 'apply_and_save')
        warnings = {}
        for path in dc.iterdir():
            if path.suffix not in ('.log', '.rpt'):
                continue
            text = path.read_text(errors='replace')
            if re.search(r'(?im)^\s*(?:Error:|Fatal:|Internal error)|'
                         r'\((?:LNK-(?:005|043)|UID-341|LINK-5)\)|'
                         r'Unable to resolve reference|Warning:[^\n]*unresolved', text):
                raise RuntimeError('DC error or unresolved reference in ' + str(path))
            warnings[path.name] = dict(Counter(re.findall(r'^\s*Warning:.*\(([^()]+)\)\s*$', text, re.M)))
        log = (dc / 'run.log').read_text()
        if len(re.findall(r'^DC_SOC_CONSTRAINTS_APPLIED\b', log, re.M)) != 1:
            raise RuntimeError('Missing actual DC constraints completion marker')
        before_ports = (dc / 'top_ports_before.tsv').read_text().splitlines()
        if sorted(before_ports) != sorted('\t'.join(item) for item in original_ports):
            raise RuntimeError('The saved SoC no longer matches the original port interface')
        for stem in ('top_ports', 'pe_pins'):
            if (dc / (stem + '_before.tsv')).read_bytes() != (dc / (stem + '_after.tsv')).read_bytes():
                raise RuntimeError('Connections or interface changed: ' + stem)
        summary['actual_port_verification'] = verify_port_report(dc / 'ports.rpt', original_ports)
        summary['actual_clock_verification'] = verify_saved_clock_sdc(dc / 'soc_constraints.sdc', case)
        print('SOC_CONSTRAINTS_PHASE=verify_unchanged_circuit', flush=True)
        original_tokens = verilog_token_hash(inputs / 'soc_elaborated.v')
        constrained_tokens = verilog_token_hash(dc / 'soc_constrained.v')
        if original_tokens != constrained_tokens:
            raise RuntimeError('The actual exported Verilog changed beyond comments/whitespace')
        summary['unchanged_circuit_verilog_tokens'] = constrained_tokens
        for name in ('soc_constrained.ddc', 'soc_constraints.sdc', 'soc_constrained.v'):
            if not (dc / name).is_file() or not (dc / name).stat().st_size:
                raise RuntimeError('Missing constrained output: ' + name)
        for path, expected_hash_value in {**source_dependencies, **hashes}.items():
            if sha(path) != expected_hash_value:
                raise RuntimeError('Source changed during constraints stage: ' + path)
        if sha(case / 'expected.tcl') != expected_hash:
            raise RuntimeError('Expectations changed during constraints stage')
        for name, path in files.items():
            if sha(inputs / name) != hashes[str(path)]:
                raise RuntimeError('Frozen input changed: ' + name)
        summary['dc_constraint_summary'] = dict(line.split('=', 1) for line in
                                                (dc / 'constraint_summary.rpt').read_text().splitlines())
        summary['warning_codes_by_file'] = warnings
        summary['output_sha256'] = {str(path.relative_to(case)): sha(path)
                                    for path in dc.iterdir() if path.is_file()}
        summary['expected_tcl_sha256'] = expected_hash
        summary['status'] = 'PASS'
        save(case / 'summary.json', summary)
        print('DC_SOC_CONSTRAINTS_APPLIED period_ns=10 data_input_bits=152 output_bits=224', flush=True)
        print('SOC_CONSTRAINTS_ONLY: timing assumptions saved; CPU mapping and SoC timing checks remain.', flush=True)
        return 0
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(case / 'summary.json', summary)
        print('SOC_CONSTRAINTS_FAILED: ' + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
