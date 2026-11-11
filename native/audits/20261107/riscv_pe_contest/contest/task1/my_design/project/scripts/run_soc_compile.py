#!/usr/bin/env python3
"""Map the SoC CPU/router while preserving the verified accelerator.

PASS covers mapping, saved constraints and structural preservation only. It
does not mean whole-SoC timing acceptance or program simulation has passed.
"""
import argparse
from collections import Counter
from decimal import Decimal
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
from verify_soc_constraints import verify_port_report, verify_saved_clock_sdc


def merge_hashes(*maps):
    result = {}
    for mapping in maps:
        for name, digest in mapping.items():
            if name in result and result[name] != digest:
                raise RuntimeError('Conflicting provenance hashes: ' + name)
            result[name] = digest
    return result


def check_hashes(hashes):
    for name, digest in hashes.items():
        if not Path(name).is_file() or sha(name) != digest:
            raise RuntimeError('Missing or changed input: ' + name)


def output_dependencies(folder, data):
    return {str(folder / name): digest for name, digest in data['output_sha256'].items()}


def select_source():
    """Require a complete, still-current constraints result and its read parent."""
    rejected = []
    for folder in sorted((TASK / 'work').glob('soc_constraints.*'),
                         key=lambda path: path.stat().st_mtime, reverse=True):
        report = folder / 'summary.json'
        if not report.is_file():
            continue
        try:
            data = json.loads(report.read_text())
            if data.get('status') != 'PASS':
                continue
            if (data.get('compile_performed') is not False or data.get('soc_mapped') is not False or
                    data['dc_constraint_summary']['top'] != 'soc_top'):
                raise RuntimeError('Expected the uncompiled constrained SoC')
            parent = Path(data['source_soc_read_case'])
            parent_report = parent / 'summary.json'
            parent_data = json.loads(parent_report.read_text())
            if parent_data.get('status') != 'PASS' or parent_data.get('cpu_control_mapped') is not False:
                raise RuntimeError('The parent SoC import did not pass')
            dependencies = merge_hashes(
                data['source_dependencies_sha256'], data['input_sha256'],
                output_dependencies(folder, data), output_dependencies(parent, parent_data),
                {str(report): sha(report), str(parent_report): sha(parent_report),
                 str(folder / 'expected.tcl'): data['expected_tcl_sha256']},
                parent_data['input_sha256'], parent_data['reused_accelerator_source_sha256'],
                parent_data['conversion']['input_sha256'])
            for required in ('dc/soc_constrained.ddc', 'dc/soc_constrained.v',
                             'dc/soc_constraints.sdc', 'dc/ports.rpt'):
                if str(folder / required) not in dependencies:
                    raise RuntimeError('Incomplete constraints provenance: ' + required)
            check_hashes(dependencies)
            return folder, data, parent, parent_data, dependencies, rejected
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            rejected.append({'case': str(folder), 'reason': str(error)})
    raise RuntimeError('No intact passing constraints result matches the current sources; '
                       'run run_soc_constraints.tcl first. Rejected: ' + json.dumps(rejected))


def audit_sdc(path, output_dir, label):
    """Interpret actual saved SDC using a restricted set of expected commands.

    Canonical Tcl lists preserve each argument, port bit and numerical value;
    comparison then allows ordering differences but no changed assumptions.
    """
    script = output_dir / 'audit_sdc_commands.tcl'
    script.write_text(r'''
proc get_ports {args} {return [lindex $args end]}
proc get_clocks {args} {return [lindex $args end]}
foreach name {set_units set_load create_clock set_clock_uncertainty
    set_clock_transition set_input_delay set_output_delay
    set_input_transition set_fix_hold} {
    proc $name {args} [format {puts [linsert $args 0 %s]} $name]
}
source [lindex $argv 0]
''')
    process = subprocess.run(['tclsh', str(script), str(path)], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=20, check=True)
    if process.stderr.strip():
        raise RuntimeError('Unexpected saved SDC diagnostic: ' + process.stderr)
    records = process.stdout.splitlines()
    hold = [line for line in records if line.startswith('set_fix_hold ')]
    if len(hold) > 1 or any(line != 'set_fix_hold core_clk' for line in hold):
        raise RuntimeError('Unexpected saved hold-repair command: ' + repr(hold))
    canonical = [line for line in records if not line.startswith('set_fix_hold ')]
    (output_dir / (label + '_sdc_commands.txt')).write_text(process.stdout)
    # Reuse the existing independent clock checker on canonical commands with
    # only the hold-repair request removed, without changing the old helper.
    clock_input = output_dir / (label + '_sdc_without_fix_hold.tcl')
    clock_input.write_text('\n'.join(canonical) + '\n')
    clock_result = verify_saved_clock_sdc(clock_input, output_dir)
    return Counter(canonical), clock_result, hold


def read_records(path, fields):
    rows = [line.split('\t') for line in path.read_text().splitlines()]
    if not rows or any(len(row) != fields for row in rows):
        raise RuntimeError('Malformed interface records: ' + str(path))
    if len({row[0] for row in rows}) != len(rows):
        raise RuntimeError('Duplicate interface records: ' + str(path))
    return rows


def expected_from_sources(prior, read_summary):
    structure = prior['dc_constraint_summary']
    read_structure = read_summary['dc_structure']
    return {
        'expected_top': 'soc_top', 'expected_top_inputs': 154,
        'expected_data_inputs': 152, 'expected_output_bits': 224,
        'expected_unmapped_count': int(structure['cpu_router_unmapped_leaf_cells']),
        'expected_pe_count': int(structure['pe_instances']),
        'expected_sram_count': int(structure['sram_instances']),
        'expected_wrapper_top': structure['mapped_pe_reference'],
        'expected_array_reference': structure['array_reference'],
        'expected_wrapper_fingerprint': read_structure['mapped_pe_fingerprint_before'],
        'expected_array_fingerprint': read_structure['array_fingerprint_before'],
    }


def check_verification_inputs(case, summary, reverification):
    check_hashes(summary['source_dependencies_sha256'])
    hashes = summary['input_sha256']
    frozen = summary.get('frozen_input_sha256')
    if frozen is None:
        # Compatibility with a completed DC run made before verify-case was
        # introduced. Name aliases are explicit, never inferred by content.
        aliases = {
            'soc_constrained.ddc': 'soc_constrained.ddc',
            'soc_constraints.sdc': 'soc_constraints.sdc',
            'source_summary.json': 'summary.json',
            'source_read_summary.json': 'summary.json',
        }
        frozen = {}
        for snapshot in (case / 'inputs').iterdir():
            if snapshot.name == 'source_summary.json':
                source = Path(summary['source_soc_constraints_case']) / 'summary.json'
            elif snapshot.name == 'source_read_summary.json':
                source = Path(summary['source_soc_read_case']) / 'summary.json'
            elif snapshot.name.startswith('environment_'):
                index = int(snapshot.stem.split('_')[1])
                source = [TASK.parent / 'env.sh', TASK.parent / 'runtime/env.vm.sh',
                          TASK.parent / 'runtime/env.scl2018.sh', TASK.parent / 'runtime/env.dcpt.sh'][index]
            else:
                candidates = [name for name in hashes if Path(name).name == aliases.get(snapshot.name, snapshot.name)]
                if len(candidates) != 1:
                    raise RuntimeError('Cannot recover the frozen input identity: ' + snapshot.name)
                source = Path(candidates[0])
            frozen[snapshot.name] = hashes[str(source)]
    for name, digest in frozen.items():
        if sha(case / 'inputs' / name) != digest:
            raise RuntimeError('Frozen input changed: ' + name)
    allowed_driver_updates = {
        str(PROJECT / 'scripts/run_soc_compile.py'),
        str(PROJECT / 'synth/compile_soc_batch.tcl'),
        str(PROJECT / 'synth/run_soc_compile.sh'),
        str(PROJECT / 'synth/run_soc_compile.tcl'),
    }
    drift = {}
    for name, digest in hashes.items():
        if Path(name).is_file() and sha(name) == digest:
            continue
        if not reverification or name not in allowed_driver_updates:
            raise RuntimeError('Source changed during synthesis: ' + name)
        drift[name] = {'used_snapshot_sha256': digest,
                       'current_sha256': sha(name) if Path(name).is_file() else None}
    if reverification:
        summary['live_compile_driver_changes_after_run'] = drift
    return frozen


def verify_result(case, summary, reverification=False):
    inputs, dc = case / 'inputs', case / 'dc'
    check_verification_inputs(case, summary, reverification)
    for name, digest in summary.get('dc_output_sha256_after_run', {}).items():
        if not (case / name).is_file() or sha(case / name) != digest:
            raise RuntimeError('DC output changed after its process completed: ' + name)
    prior = json.loads((inputs / 'source_summary.json').read_text())
    read_summary = json.loads((inputs / 'source_read_summary.json').read_text())
    expected = expected_from_sources(prior, read_summary)
    original_ports = [tuple(row) for row in summary['original_top_ports']]
    warnings = {}
    for path in dc.iterdir():
        if path.suffix not in ('.log', '.rpt'):
            continue
        text = path.read_text(errors='replace')
        if re.search(r'(?im)^\s*(?:Error:|Fatal:|Internal error)|'
                     r'\((?:LNK-(?:005|043)|UID-341|LINK-5)\)|'
                     r'Unable to resolve reference|Warning:[^\n]*unresolved', text):
            raise RuntimeError('DC error or unresolved reference in ' + str(path))
        warnings[path.name] = dict(Counter(re.findall(
            r'^\s*Warning:.*\(([^()]+)\)\s*$', text, re.M)))
    log = (dc / 'run.log').read_text()
    if len(re.findall(r'^DC_SOC_SYNTHESIS_DONE\b', log, re.M)) != 1:
        raise RuntimeError('Missing actual DC synthesis completion marker')
    synthesis = dict(line.split('=', 1) for line in
                     (dc / 'synthesis_summary.rpt').read_text().splitlines())
    for key, value in {
        'top': 'soc_top', 'unmapped_leaf_cells': '0', 'pe_instances': '256',
        'sram_instances': '256', 'pe_instance_protected': 'true',
        'pe_design_protected': 'true', 'bit_interfaces_preserved': 'true',
        'accelerator_structure_preserved': 'true', 'check_design_success': 'true',
        'compile_performed': 'true', 'soc_mapped': 'true',
        'mapped_pe_reference': expected['expected_wrapper_top'],
        'array_reference': expected['expected_array_reference'],
    }.items():
        if synthesis.get(key) != value:
            raise RuntimeError('Unexpected actual synthesis result: ' + key)
    for stem, digest in [('pe', expected['expected_wrapper_fingerprint']),
                         ('array', expected['expected_array_fingerprint'])]:
        if any(sha(dc / f'{stem}_{position}.txt') != digest for position in ('before', 'after')):
            raise RuntimeError('The protected accelerator fingerprint changed: ' + stem)
        prefix = 'mapped_pe' if stem == 'pe' else 'array'
        if any(synthesis.get(prefix + '_fingerprint_' + position) != digest
               for position in ('before', 'after')):
            raise RuntimeError('Fingerprint report disagrees with the actual records')
    for position in ('before', 'after'):
        ports = read_records(dc / f'top_ports_{position}.tsv', 2)
        if sorted(tuple(row) for row in ports) != sorted(original_ports):
            raise RuntimeError('The whole-SoC bit interface changed')
    pins_before = read_records(dc / 'pe_pins_before.tsv', 3)
    pins_after = read_records(dc / 'pe_pins_after.tsv', 3)
    if (len(pins_before) != 256 or sorted(row[:2] for row in pins_before) !=
            sorted(row[:2] for row in pins_after)):
        raise RuntimeError('The accelerator bit interface changed')
    summary['accelerator_pin_interface'] = {
        'bit_pins': 256, 'names_and_directions_preserved': True,
        'before_empty_net_records': [row[0] for row in pins_before if not row[2]],
        'after_empty_net_records': [row[0] for row in pins_after if not row[2]],
        'changed_net_records': [row[0] for row in pins_before
                                if dict((other[0], other[2]) for other in pins_after)[row[0]] != row[2]],
        'cpu_side_net_names_required_unchanged': False,
        'connection_semantics_verified_by_simulation': False}
    summary['actual_port_verification'] = verify_port_report(dc / 'ports.rpt', original_ports)
    print('SOC_COMPILE_PHASE=verify_saved_assumptions', flush=True)
    original_sdc, _, original_hold = audit_sdc(inputs / 'soc_constraints.sdc', case, 'source')
    loaded_sdc, _, loaded_hold = audit_sdc(dc / 'soc_constraints_before.sdc', case, 'loaded')
    mapped_sdc, clock_result, mapped_hold = audit_sdc(dc / 'soc_constraints.sdc', case, 'mapped')
    if original_sdc != loaded_sdc or original_sdc != mapped_sdc or original_hold != loaded_hold:
        raise RuntimeError('The actual saved timing assumptions changed during mapping')
    summary['actual_clock_verification'] = clock_result
    summary['unchanged_saved_assumptions'] = {
        'status': 'PASS', 'canonical_constraint_commands': sum(original_sdc.values()),
        'only_additional_command_allowed': 'set_fix_hold core_clk',
        'source_hold_commands': original_hold, 'mapped_hold_commands': mapped_hold,
        'all_saved_constraint_arguments_compared': True}
    setup, hold = (Decimal(synthesis[name]) for name in ('setup_slack_ns', 'hold_slack_ns'))
    if not setup.is_finite() or not hold.is_finite():
        raise RuntimeError('Invalid actual slack values')
    nonnegative = setup >= 0 and hold >= 0
    if synthesis.get('setup_and_hold_slack_nonnegative') != str(int(nonnegative)):
        raise RuntimeError('The timing flag disagrees with the actual slack values')
    for name in ('soc_mapped.ddc', 'soc_mapped.v', 'soc_constraints.sdc', 'area.rpt',
                 'setup.rpt', 'hold.rpt', 'violations.rpt', 'constraints_check.rpt'):
        if not (dc / name).is_file() or not (dc / name).stat().st_size:
            raise RuntimeError('Missing mapped output: ' + name)
    check_verification_inputs(case, summary, reverification)
    if sha(case / 'expected.tcl') != summary['expected_tcl_sha256']:
        raise RuntimeError('The frozen expectations changed during synthesis')
    summary.update(status='PASS', compile_performed=True, soc_mapped=True,
                   timing_met=bool(nonnegative),
                   setup_and_hold_slack_nonnegative=bool(nonnegative),
                   timing_met_scope='Reported setup/hold slack only; inspect all constraints and coverage',
                   setup_slack_ns=str(setup), hold_slack_ns=str(hold),
                   dc_synthesis_summary=synthesis, warning_codes_by_file=warnings,
                   output_sha256={str(path.relative_to(case)): sha(path)
                                  for path in dc.iterdir() if path.is_file()})
    save(case / 'summary.json', summary)
    print('DC_SOC_SYNTHESIS_DONE mapped=true unmapped_leaf_cells=0 sram_instances=256', flush=True)
    print(f'SOC_SETUP_SLACK_NS={setup}', flush=True)
    print(f'SOC_HOLD_SLACK_NS={hold}', flush=True)
    print('SOC_MAPPING_ONLY: timing reports require review; no whole-SoC program/SDF simulation performed.', flush=True)
    return 0


def reverify_case(case):
    case = case.resolve()
    print('RESULT=' + str(case), flush=True)
    report = case / 'summary.json'
    original = json.loads(report.read_text())
    # This entry never starts DC or upgrades incomplete/failed DC output.
    # Only a previously finished child run can be independently rechecked.
    if (not original.get('commands') or
            original['commands'][-1].get('exit_code') != 0):
        print('SOC_COMPILE_FAILED: --verify-case requires a completed successful DC child run',
              file=sys.stderr, flush=True)
        return 1
    if not original.get('dc_output_sha256_after_run'):
        print('SOC_COMPILE_FAILED: no original completed-DC output hash snapshot is available',
              file=sys.stderr, flush=True)
        return 1
    verification = Path(tempfile.mkdtemp(prefix='verification.', dir=case))
    shutil.copyfile(report, verification / 'original_summary.json')
    tools = {
        str(Path(__file__)): sha(Path(__file__)),
        str(PROJECT / 'scripts/run_soc_read.py'): sha(PROJECT / 'scripts/run_soc_read.py'),
        str(PROJECT / 'scripts/verify_soc_constraints.py'): sha(PROJECT / 'scripts/verify_soc_constraints.py'),
    }
    for name in tools:
        shutil.copyfile(name, verification / Path(name).name)
    summary = dict(original)
    summary['reverification'] = {'directory': str(verification), 'validator_sha256': tools,
                               'dc_rerun': False, 'prior_status': original.get('status')}
    summary.pop('error', None)
    try:
        code = verify_result(case, summary, reverification=True)
        save(verification / 'summary.json', summary)
        return code
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(verification / 'summary.json', summary)
        save(report, summary)
        print('SOC_COMPILE_FAILED: ' + str(error), file=sys.stderr, flush=True)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=1800,
                        help='maximum child DC seconds (default: 1800)')
    parser.add_argument('--verify-case', type=Path,
                        help='recheck an existing complete DC result without running DC')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.verify_case is not None:
        return reverify_case(args.verify_case)
    case = Path(tempfile.mkdtemp(prefix='soc_compile.', dir=TASK / 'work'))
    print('RESULT=' + str(case), flush=True)
    inputs, dc = case / 'inputs', case / 'dc'
    inputs.mkdir()
    dc.mkdir()
    summary = {'status': 'RUNNING', 'scope': 'Whole-SoC CPU/router mapping and preservation checks',
               'cpu_included': True, 'router_included': True, 'compile_performed': False,
               'soc_mapped': False, 'sta_acceptance_established': False,
               'soc_program_simulated': False, 'sdf_annotated': False,
               'physical_parasitics_used': False, 'formal_equivalence_checked': False,
               'child_address_space_limit_bytes': LIMIT, 'commands': []}
    env = os.environ.copy()
    env.update(LC_ALL='C', SOC_COMPILE_CASE=str(case))
    save(case / 'summary.json', summary)

    def run(command):
        record = {'argv': list(map(str, command)), 'cwd': str(dc), 'phase': 'map_and_report',
                  'timeout_s': args.timeout, 'log': str(dc / 'run.log')}
        summary['commands'].append(record)
        print('SOC_COMPILE_PHASE=map_and_report', flush=True)
        started = time.monotonic()
        with (dc / 'run.log').open('w') as log:
            process = subprocess.Popen(record['argv'], cwd=dc, env=env,
                                       stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       preexec_fn=limit_child)
            try:
                while True:
                    try:
                        code = process.wait(timeout=min(15, args.timeout))
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = time.monotonic() - started
                        print(f'SOC_COMPILE_RUNNING elapsed_s={int(elapsed)}', flush=True)
                        if elapsed >= args.timeout:
                            raise RuntimeError('DC mapping exceeded its time limit')
            except BaseException:
                stop_child(process)
                record.update(exit_code=process.returncode,
                              elapsed_s=round(time.monotonic() - started, 2))
                raise
        record.update(exit_code=code, elapsed_s=round(time.monotonic() - started, 2))
        if code:
            raise RuntimeError('DC mapping failed; inspect ' + str(dc / 'run.log'))

    try:
        source, prior, parent, read_summary, dependencies, rejected = select_source()
        summary.update(source_soc_constraints_case=str(source), source_soc_read_case=str(parent),
                       source_dependencies_sha256=dependencies, rejected_source_cases=rejected)
        structure = prior['dc_constraint_summary']
        read_structure = read_summary['dc_structure']
        export_path = PROJECT / 'build/dc_pe_axi_export/artifact_summary.json'
        export = json.loads(export_path.read_text())
        if export.get('status') != 'EXPORTED':
            raise RuntimeError('The accelerator export is incomplete')
        if (export['source_design']['array_reference'] != structure['array_reference'] or
                export['source_design']['array_fingerprint_before'] !=
                export['source_design']['array_fingerprint_after'] or
                read_structure['array_fingerprint_before'] !=
                export['source_design']['array_fingerprint_before'] or
                read_structure['array_fingerprint_after'] != read_structure['array_fingerprint_before'] or
                read_structure['mapped_pe_fingerprint_after'] !=
                read_structure['mapped_pe_fingerprint_before']):
            raise RuntimeError('Accelerator import and export fingerprints disagree')
        original_ports = [tuple(row) for row in prior['original_top_ports']]
        conversion_ports = bit_ports({'ports': {
            name: {'direction': port['direction'], 'bits': list(range(port['width']))}
            for name, port in read_summary['conversion']['original_top_ports'].items()}})
        if sorted(original_ports) != conversion_ports or len(original_ports) != 378:
            raise RuntimeError('The source port interface differs from the original SoC')
        files = {
            'soc_constrained.ddc': source / 'dc/soc_constrained.ddc',
            'soc_constraints.sdc': source / 'dc/soc_constraints.sdc',
            'source_summary.json': source / 'summary.json',
            'source_read_summary.json': parent / 'summary.json',
            'artifact_summary.json': export_path,
            'standard.db': PROJECT / 'build/dc_libraries/standard.db',
            'sram.db': PROJECT / 'build/dc_libraries/sram.db',
            'pe_axi_fingerprint.tcl': PROJECT / 'synth/pe_axi_fingerprint.tcl',
            'compile_soc_batch.tcl': PROJECT / 'synth/compile_soc_batch.tcl',
            'run_soc_compile.py': Path(__file__),
            'run_soc_compile.sh': PROJECT / 'synth/run_soc_compile.sh',
            'run_soc_compile.tcl': PROJECT / 'synth/run_soc_compile.tcl',
            'run_soc_read.py': PROJECT / 'scripts/run_soc_read.py',
            'verify_soc_constraints.py': PROJECT / 'scripts/verify_soc_constraints.py',
        }
        for index, path in enumerate((TASK.parent / 'env.sh', TASK.parent / 'runtime/env.vm.sh',
                                      TASK.parent / 'runtime/env.scl2018.sh', TASK.parent / 'runtime/env.dcpt.sh')):
            files[f'environment_{index}.sh'] = path
        hashes = {str(path): sha(path) for path in files.values()}
        merge_hashes(dependencies, hashes)
        for name in ('standard.db', 'sram.db', 'artifact_summary.json'):
            if hashes[str(files[name])] != dependencies.get(str(files[name])):
                raise RuntimeError('The mapped source dependency differs: ' + name)
        for name, path in files.items():
            shutil.copyfile(path, inputs / name)
            if sha(inputs / name) != hashes[str(path)]:
                raise RuntimeError('Input changed while freezing: ' + str(path))
        expected = expected_from_sources(prior, read_summary)
        if any(not re.fullmatch(r'[A-Za-z0-9_]+', str(value)) for value in expected.values()):
            raise RuntimeError('Invalid frozen expectation')
        (case / 'expected.tcl').write_text(''.join(
            'set ' + name + ' {' + str(value) + '}\n' for name, value in expected.items()))
        expected_hash = sha(case / 'expected.tcl')
        summary.update(input_sha256=hashes,
                       frozen_input_sha256={name: hashes[str(path)] for name, path in files.items()},
                       expected_tcl_sha256=expected_hash,
                       original_top_ports=original_ports)
        save(case / 'summary.json', summary)
        run(['dc_shell', '-f', str(inputs / 'compile_soc_batch.tcl')])
        summary['dc_output_sha256_after_run'] = {
            str(path.relative_to(case)): sha(path) for path in dc.iterdir() if path.is_file()}
        save(case / 'summary.json', summary)
        return verify_result(case, summary)
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(case / 'summary.json', summary)
        print('SOC_COMPILE_FAILED: ' + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
