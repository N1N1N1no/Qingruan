#!/usr/bin/env python3
"""Export synthesis SDF from an intact, mapped SoC checkpoint without recompiling.

PASS covers export provenance, hierarchy, interfaces, constraints and netlist
preservation. It does not establish SDF annotation or timing simulation success.
"""
import argparse
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

from run_soc_compile import audit_sdc, read_records
from verify_soc_constraints import verify_port_report

PROJECT = Path(__file__).resolve().parents[1]
TASK = PROJECT.parents[1]
LIMIT = 6 * 1024**3
MAX_LOG = 64 * 1024**2


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024**2), b''):
            h.update(part)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def merge_hashes(*maps):
    result = {}
    for mapping in maps:
        for name, digest in mapping.items():
            if name in result and result[name] != digest:
                raise RuntimeError('Conflicting provenance: ' + name)
            result[name] = digest
    return result


def check_hashes(mapping):
    for name, digest in mapping.items():
        if not Path(name).is_file() or sha(name) != digest:
            raise RuntimeError('Missing or changed input: ' + name)


def limit_child():
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))


def stop_child(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def netlist_modules(path):
    """Compare exact lexical tokens per module; only comments/order are ignored."""
    text = Path(path).read_text()
    # DC emits no strings with comments; retain quoted strings as one token so
    # comments within a legal string cannot be stripped accidentally.
    pattern = r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*|\\[^\s]+|[A-Za-z_$][A-Za-z0-9_$]*|\d[^\s(),;{}\[\]:+*/&|^~!=<>?-]*|[^\s]'
    tokens = [m.group() for m in re.finditer(pattern, text, re.S)
              if not m.group().startswith(('/*', '//'))]
    result = {}
    cursor = 0
    while cursor < len(tokens):
        if tokens[cursor] != 'module' or cursor + 1 >= len(tokens):
            raise RuntimeError('Unexpected text outside mapped module: ' + str(path))
        end = tokens.index('endmodule', cursor)
        name = tokens[cursor + 1]
        if name in result:
            raise RuntimeError('Duplicate mapped module: ' + name)
        result[name] = tokens[cursor:end+1]
        cursor = end + 1
    return result


def source_data(folder):
    report = folder / 'summary.json'
    data = json.loads(report.read_text())
    structure = data['dc_synthesis_summary']
    if (data.get('status') != 'PASS' or data.get('soc_mapped') is not True or
            data.get('compile_performed') is not True or structure.get('top') != 'soc_top' or
            structure.get('unmapped_leaf_cells') != '0' or
            structure.get('pe_instances') != '256' or structure.get('sram_instances') != '256'):
        raise RuntimeError('Expected a complete fully mapped whole-SoC result')
    dependencies = merge_hashes(data['source_dependencies_sha256'], data['input_sha256'],
        {str(folder / name): digest for name, digest in data['output_sha256'].items()},
        {str(report): sha(report), str(folder / 'expected.tcl'): data['expected_tcl_sha256']})
    check_hashes(dependencies)
    for required in ['dc/soc_mapped.ddc', 'dc/soc_mapped.v', 'dc/soc_constraints.sdc']:
        if str(folder / required) not in dependencies:
            raise RuntimeError('Missing mapped provenance: ' + required)
    return data, structure, dependencies


def inspect_sdf(path):
    """Read the gigabyte-scale export line by line, retaining only counters."""
    counts = Counter()
    headers = []
    pattern = re.compile(r'\((CELLTYPE|IOPATH|INTERCONNECT|TIMINGCHECK)\b')
    with path.open() as stream:
        for number, line in enumerate(stream):
            if number < 32:
                headers.append(line)
            counts.update(pattern.findall(line))
    header = ''.join(headers)
    if (not re.search(r'\(SDFVERSION\s+"(?:OVI )?2\.1"\)', header) or
            not re.search(r'\(DESIGN\s+"soc_top"\)', header)):
        raise RuntimeError('Unexpected SDF version or design')
    if min(counts['CELLTYPE'], counts['IOPATH'], counts['INTERCONNECT'], counts['TIMINGCHECK']) <= 0:
        raise RuntimeError('Incomplete mapped SoC delay/timing-check export')
    return {'version': 'OVI 2.1' if '"OVI 2.1"' in header else '2.1',
        'design': 'soc_top', 'cell_records': counts['CELLTYPE'],
        'iopath_records': counts['IOPATH'], 'interconnect_records': counts['INTERCONNECT'],
        'timingcheck_sections': counts['TIMINGCHECK'], 'size_bytes': path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    source = args.source.resolve()
    case = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='soc_sdf_export.', dir=TASK / 'work'))
    case.mkdir(parents=True, exist_ok=True)
    if any(case.iterdir()):
        parser.error('--output must be an empty directory')
    inputs, dc = case / 'inputs', case / 'dc'
    inputs.mkdir()
    dc.mkdir()
    print('RESULT=' + str(case), flush=True)
    summary = {'status': 'RUNNING', 'scope': 'Already mapped whole-SoC synthesis SDF export only',
        'source_soc_compile_case': str(source), 'cpu_included': True, 'router_included': True,
        'compile_performed': False, 'sdf_exported': False, 'sdf_annotated': False,
        'soc_program_simulated': False, 'timing_acceptance_established': False,
        'physical_parasitics_used': False, 'formal_equivalence_checked': False,
        'child_address_space_limit_bytes': LIMIT, 'commands': []}
    save(case / 'summary.json', summary)
    env = os.environ.copy()
    env.update(LC_ALL='C', SOC_SDF_EXPORT_CASE=str(case), PYTHONDONTWRITEBYTECODE='1')
    try:
        prior, structure, dependencies = source_data(source)
        files = {
            'soc_mapped.ddc': source / 'dc/soc_mapped.ddc',
            'soc_mapped.v': source / 'dc/soc_mapped.v',
            'soc_constraints.sdc': source / 'dc/soc_constraints.sdc',
            'source_summary.json': source / 'summary.json',
            'source_pe_pins.tsv': source / 'dc/pe_pins_after.tsv',
            'standard.db': PROJECT / 'build/dc_libraries/standard.db',
            'sram.db': PROJECT / 'build/dc_libraries/sram.db',
            'pe_axi_fingerprint.tcl': PROJECT / 'synth/pe_axi_fingerprint.tcl',
            'export_soc_sdf_batch.tcl': PROJECT / 'synth/export_soc_sdf_batch.tcl',
            'export_soc_sdf.py': Path(__file__).resolve(),
            'run_soc_compile.py': PROJECT / 'scripts/run_soc_compile.py',
            'run_soc_read.py': PROJECT / 'scripts/run_soc_read.py',
            'verify_soc_constraints.py': PROJECT / 'scripts/verify_soc_constraints.py',
        }
        for index, path in enumerate([TASK.parent / 'env.sh', TASK.parent / 'runtime/env.vm.sh',
                TASK.parent / 'runtime/env.scl2018.sh', TASK.parent / 'runtime/env.dcpt.sh']):
            files[f'environment_{index}.sh'] = path
        hashes = {str(path): sha(path) for path in files.values()}
        merge_hashes(dependencies, hashes)
        for name, path in files.items():
            shutil.copyfile(path, inputs / name)
            if sha(inputs / name) != hashes[str(path)]:
                raise RuntimeError('Input changed while freezing: ' + str(path))
        expected = {'expected_top': 'soc_top', 'expected_top_inputs': 154,
            'expected_data_inputs': 152, 'expected_output_bits': 224,
            'expected_pe_count': 256, 'expected_sram_count': 256,
            'expected_design_count': int(structure['imported_designs']),
            'expected_wrapper_top': structure['mapped_pe_reference'],
            'expected_array_reference': structure['array_reference'],
            'expected_wrapper_fingerprint': structure['mapped_pe_fingerprint_after'],
            'expected_array_fingerprint': structure['array_fingerprint_after']}
        if any(not re.fullmatch(r'[A-Za-z0-9_]+', str(v)) for v in expected.values()):
            raise RuntimeError('Invalid frozen Tcl expectation')
        (case / 'expected.tcl').write_text(''.join('set ' + k + ' {' + str(v) + '}\n' for k,v in expected.items()))
        summary.update(source_dependencies_sha256=dependencies, input_sha256=hashes,
            frozen_input_sha256={name: hashes[str(path)] for name,path in files.items()},
            expected_tcl_sha256=sha(case / 'expected.tcl'))
        save(case / 'summary.json', summary)
        command = ['dc_shell', '-f', str(inputs / 'export_soc_sdf_batch.tcl')]
        record = {'argv': command, 'cwd': str(dc), 'timeout_s': args.timeout,
            'log': str(dc / 'run.log'), 'phase': 'export_mapped_soc_sdf'}
        summary['commands'].append(record)
        save(case / 'summary.json', summary)
        started = time.monotonic()
        with (dc / 'run.log').open('w') as log:
            process = subprocess.Popen(command, cwd=dc, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=limit_child)
            try:
                while True:
                    remaining = args.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise RuntimeError('DC SDF export timed out; inspect ' + str(dc / 'run.log'))
                    try:
                        code = process.wait(timeout=min(15, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        print('SOC_SDF_EXPORT_RUNNING elapsed_s=' + str(int(time.monotonic()-started)), flush=True)
                        if (dc / 'run.log').stat().st_size > MAX_LOG:
                            raise RuntimeError('Too many DC diagnostics')
            except BaseException:
                stop_child(process)
                record.update(exit_code=process.returncode, elapsed_s=round(time.monotonic()-started,3))
                raise
        record.update(exit_code=code, elapsed_s=round(time.monotonic()-started,3))
        if code:
            raise RuntimeError('DC SDF export failed; inspect ' + str(dc / 'run.log'))
        warnings = {}
        for path in dc.iterdir():
            if path.suffix not in {'.log', '.rpt'}:
                continue
            if path.stat().st_size > MAX_LOG:
                raise RuntimeError('Too many diagnostics in ' + str(path))
            text = path.read_text(errors='replace')
            if re.search(r'(?im)^\s*(?:Error:|Fatal:|Internal error)|Unable to resolve reference|Warning:[^\n]*unresolved', text):
                raise RuntimeError('DC error or unresolved reference: ' + str(path))
            warnings[path.name] = dict(Counter(re.findall(r'^\s*Warning:.*\(([^()]+)\)\s*$', text, re.M)))
        if len(re.findall(r'^DC_SOC_SDF_EXPORTED\b', (dc/'run.log').read_text(), re.M)) != 1:
            raise RuntimeError('Missing actual SDF export completion marker')
        actual = dict(line.split('=',1) for line in (dc/'export_summary.rpt').read_text().splitlines())
        for key,value in {'top':'soc_top','unmapped_leaf_cells':'0','pe_instances':'256',
            'sram_instances':'256','compile_performed':'false','sdf_exported':'true',
            'scoped_in_memory_linking':'true','check_design_success':'true',
            'imported_designs':str(expected['expected_design_count']),
            'reachable_designs':str(expected['expected_design_count']),
            'mapped_pe_fingerprint':expected['expected_wrapper_fingerprint'],
            'array_fingerprint':expected['expected_array_fingerprint']}.items():
            if actual.get(key) != value:
                raise RuntimeError('Unexpected actual export result: ' + key)
        ports = read_records(dc/'top_ports.tsv',2)
        if sorted(ports) != sorted(prior['original_top_ports']):
            raise RuntimeError('Top bit ports differ from the mapped source')
        if read_records(dc/'pe_pins.tsv',3) != read_records(inputs/'source_pe_pins.tsv',3):
            raise RuntimeError('Accelerator pin connections differ from the mapped source')
        summary['actual_port_verification'] = verify_port_report(dc/'ports.rpt', [tuple(x) for x in ports])
        a, ac, ah = audit_sdc(inputs/'soc_constraints.sdc',case,'source')
        b, bc, bh = audit_sdc(dc/'soc_constraints.sdc',case,'exported')
        if a != b or ah != bh:
            raise RuntimeError('Saved timing assumptions changed during export')
        summary['unchanged_constraints'] = {'canonical_commands':sum(a.values()),'hold_commands':ah,
            'clock':bc,'all_saved_arguments_compared':True}
        original, exported = netlist_modules(inputs/'soc_mapped.v'), netlist_modules(dc/'soc_mapped.v')
        if original != exported:
            changed = sorted(name for name in original.keys() | exported.keys() if original.get(name) != exported.get(name))
            raise RuntimeError('Exported mapped netlist lexical tokens differ: '+repr(changed))
        summary['netlist_preservation'] = {'module_definitions':len(original),
            'lexical_tokens':sum(map(len,original.values())), 'exact_tokens_per_module':True,
            'ignored_differences':['comments','whitespace','module definition order']}
        del original, exported
        for name,digest in [('pe_fingerprint.txt',expected['expected_wrapper_fingerprint']),
                ('array_fingerprint.txt',expected['expected_array_fingerprint'])]:
            if sha(dc/name) != digest:
                raise RuntimeError('Actual fingerprint record differs: '+name)
        for name in ['soc_mapped.ddc','soc_mapped.v','soc_constraints.sdc','soc_mapped.sdf']:
            if not (dc/name).is_file() or not (dc/name).stat().st_size:
                raise RuntimeError('Missing actual export output: '+name)
        summary['sdf_contents'] = inspect_sdf(dc/'soc_mapped.sdf')
        check_hashes(merge_hashes(dependencies,hashes))
        for name,digest in summary['frozen_input_sha256'].items():
            if sha(inputs/name) != digest:
                raise RuntimeError('Frozen input changed: '+name)
        if sha(case/'expected.tcl') != summary['expected_tcl_sha256']:
            raise RuntimeError('Frozen expectations changed')
        summary.update(status='PASS',sdf_exported=True,dc_export_summary=actual,
            warning_codes_by_file=warnings,
            output_sha256={str(p.relative_to(case)):sha(p) for p in dc.iterdir() if p.is_file()})
        save(case/'summary.json',summary)
        print('SOC_SDF_EXPORT_PASS',flush=True)
        print('SOC_SDF_EXPORT_ONLY: no recompile, SDF annotation, program simulation or timing acceptance.',flush=True)
        return 0
    except BaseException as error:
        summary.update(status='FAIL',error=str(error))
        save(case/'summary.json',summary)
        print('SOC_SDF_EXPORT_FAILED: '+str(error),file=sys.stderr,flush=True)
        return 1


if __name__ == '__main__':
    def terminated(signum, frame):
        raise InterruptedError('Received signal '+str(signum))
    signal.signal(signal.SIGTERM,terminated)
    raise SystemExit(main())
