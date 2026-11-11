#!/usr/bin/env python3
"""Check paired AXI-system exports without loading the large SDF into memory.

These are file and hierarchy checks, not SDF annotation, simulation, formal
equivalence, or timing acceptance. Original exported files are never changed.
"""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys


PROJECT = Path(__file__).resolve().parents[1]
TASK = PROJECT.parents[1]
TOP = 'sram_multiplier_system_ID_WIDTH3'
SRAM = 'SRAM_6T_CORE_16x8_MC_TB'
CONSTANTS = {'**logic_0**', '**logic_1**'}
ARTIFACTS = (
    'pe_axi_mapped.v', 'pe_axi_mapped.ddc', 'pe_axi_constraints.sdc',
    'pe_axi_mapped.sdf', 'area.rpt', 'setup.rpt', 'hold.rpt',
    'violations.rpt', 'constraints_check.rpt', 'export_summary.rpt',
    'cell_counts.rpt', 'units.rpt', 'clocks.rpt', 'ports.rpt',
    'check_design.rpt', 'array_before.txt', 'array_after.txt',
)
TIMING_TYPES = {
    'SETUP', 'HOLD', 'SETUPHOLD', 'RECOVERY', 'REMOVAL', 'RECREM',
    'WIDTH', 'PERIOD', 'SKEW', 'NOCHANGE',
}
ENTRY = re.compile(rb'\((IOPATH|INTERCONNECT|' +
                   b'|'.join(name.encode('ascii') for name in sorted(TIMING_TYPES)) + rb')\b')
TRIPLET = re.compile(rb'\(([-+0-9.eE]+):([-+0-9.eE]+):([-+0-9.eE]+)\)')
QUOTED = re.compile(rb'"(?:\\.|[^"\\])*"')
CELLTYPE = re.compile(rb'^\s*\(CELLTYPE\s+"([^"\r\n]+)"\)\s*$')
INSTANCE = re.compile(rb'^\s*\(INSTANCE\s*([^()\r\n]*)\)\s*$')
HEADER = re.compile(rb'^\s*\((DESIGN|SDFVERSION|PROCESS|TIMESCALE|DIVIDER)\s+([^\r\n]+)\)\s*$')
MODULE = re.compile(r'^\s*module\s+(\w+)\b')
CELL = re.compile(r'^\s*(\w+)\s+(\\\S+|\w+)\s*\(')
ERROR = re.compile(rb'^\s*(?:Error(?::|-\[)|Fatal:|FATAL:)', re.I)
WARNING = re.compile(rb'^\s*Warning(?::|-\[)')
CODE = re.compile(rb'\(([A-Z0-9]+-\d+)\)|Warning-\[([^]]+)\]')


def fingerprint(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def scan_summary(path):
    values = {}
    for line in path.read_text().splitlines():
        for key, value in re.findall(r'\b([a-z_]+)=([^\s]+)', line):
            if key in values:
                raise ValueError('Duplicate export summary field: ' + key)
            values[key] = value
    required = {'top', 'array_reference', 'pe_reference', 'pe_instances',
                'sram_instances', 'mapped_leaf_cells', 'physical_leaf_cells',
                'virtual_constant_leaf_cells', 'unmapped_leaf_cells', 'period_ns',
                'setup_slack_ns', 'hold_slack_ns', 'array_fingerprint_before',
                'array_fingerprint_after'}
    if required - values.keys():
        raise ValueError('Missing export summary fields: ' + ', '.join(sorted(required - values.keys())))
    if (values['top'] != TOP or int(values['pe_instances']) != 256 or
            int(values['sram_instances']) != 256 or int(values['unmapped_leaf_cells']) != 0 or
            Decimal(values['period_ns']) != 10):
        raise ValueError('Unexpected mapped AXI system export summary')
    for key in ['setup_slack_ns', 'hold_slack_ns']:
        if not Decimal(values[key]).is_finite():
            raise ValueError('Non-finite slack in export summary')
    if values['array_fingerprint_before'] != values['array_fingerprint_after']:
        raise ValueError('Array fingerprint changed during export')
    return values


def scan_netlist(path, library_paths, summary):
    modules = {}
    current = None
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            line = line.split('//', 1)[0]
            declaration = MODULE.match(line)
            if declaration:
                if current is not None or declaration[1] in modules:
                    raise ValueError('Nested/duplicate module at netlist line %d' % line_number)
                current = declaration[1]
                modules[current] = {}
                continue
            if re.match(r'^\s*endmodule\b', line):
                if current is None:
                    raise ValueError('Unexpected endmodule at netlist line %d' % line_number)
                current = None
                continue
            if current is None:
                continue
            if re.search(r'\balways(?:_\w+)?\b|\binitial\b', line):
                raise ValueError('Behavioral logic in mapped module ' + current)
            cell = CELL.match(line)
            if cell:
                reference, name = cell.groups()
                name = name.lstrip('\\')
                if name in modules[current]:
                    raise ValueError('Duplicate instance in module ' + current + ': ' + name)
                modules[current][name] = reference
    if current is not None or TOP not in modules:
        raise ValueError('Missing top or unterminated netlist module')
    if modules[TOP].get('u_pe_array') != summary['array_reference']:
        raise ValueError('Mapped top does not instantiate its recorded array at u_pe_array')
    array_reference = summary['array_reference']
    pe_reference = summary['pe_reference']
    if (array_reference not in modules or pe_reference not in modules or
            Counter(modules[array_reference].values()) != Counter({pe_reference: 256})):
        raise ValueError('Expected all 256 PE instances directly beneath the preserved array')
    library_names = set()
    for library in library_paths:
        with library.open() as stream:
            for line in stream:
                match = MODULE.match(line)
                if match:
                    library_names.add(match[1])
    memo = {}

    def count(reference, active):
        if reference in memo:
            return memo[reference]
        if reference in active:
            raise ValueError('Recursive mapped hierarchy: ' + reference)
        if reference not in modules:
            if reference not in library_names:
                raise ValueError('Unresolved technology leaf: ' + reference)
            return Counter({reference: 1})
        result = Counter()
        for child, number in Counter(modules[reference].values()).items():
            for leaf, descendants in count(child, active | {reference}).items():
                result[leaf] += number * descendants
        memo[reference] = result
        return result

    leaves = count(TOP, set())
    if leaves[SRAM] != 256 or count(pe_reference, set())[SRAM] != 1:
        raise ValueError('Expected one SRAM per PE and 256 SRAMs in the mapped top')
    sram_paths = set()

    def visit(reference, prefix):
        if reference == SRAM:
            if prefix in sram_paths:
                raise ValueError('Duplicate mapped SRAM path: ' + prefix)
            sram_paths.add(prefix)
        elif reference in modules:
            for instance, child in modules[reference].items():
                if count(child, set())[SRAM]:
                    visit(child, prefix + '/' + instance if prefix else instance)

    visit(TOP, '')
    if len(sram_paths) != 256 or any(not name.startswith('u_pe_array/') for name in sram_paths):
        raise ValueError('SRAM paths must lie beneath the preserved u_pe_array')
    return {'top': TOP, 'module_definitions': len(modules),
            'array_reference': array_reference, 'pe_reference': pe_reference,
            'pe_instances': 256, 'sram_instances': 256,
            'physical_leaf_cells': sum(leaves.values()),
            'leaf_cell_counts': dict(sorted(leaves.items()))}, sram_paths


def scan_cell_counts(path, netlist, summary):
    counts = Counter()
    for line in path.read_text().splitlines():
        match = re.fullmatch(r'\s*(\S+)\s+(\d+)\s*', line)
        if not match:
            raise ValueError('Unexpected DC cell-count row: ' + line)
        reference, number = match.groups()
        reference = reference.strip('{}')
        if reference in counts or int(number) <= 0:
            raise ValueError('Duplicate or nonpositive DC cell-count row: ' + reference)
        counts[reference] = int(number)
    physical = Counter({name: count for name, count in counts.items() if name not in CONSTANTS})
    virtual_count = sum(counts[name] for name in CONSTANTS)
    if physical != Counter(netlist['leaf_cell_counts']):
        raise ValueError('Exported netlist technology counts differ from the source DC object')
    if (sum(counts.values()) != int(summary['mapped_leaf_cells']) or
            sum(physical.values()) != int(summary['physical_leaf_cells']) or
            virtual_count != int(summary['virtual_constant_leaf_cells'])):
        raise ValueError('DC/source/netlist leaf totals disagree')
    return {'physical_leaf_cells': sum(physical.values()),
            'virtual_constant_leaf_cells': virtual_count,
            'mapped_leaf_cells_including_virtual_constants': sum(counts.values()),
            'reference_counts': dict(sorted(counts.items()))}


def scan_sdf(path, netlist, expected_sram_paths):
    counts, celltypes = Counter(), Counter()
    negative = Counter(minimum=0, typical=0, maximum=0)
    headers = {}
    sram_instances = set()
    current_celltype = None
    current_instance = None
    cell_has_instance = False
    balance = cell_count = empty_instances = 0
    saw_header = False
    with path.open('rb') as stream:
        for line_number, line in enumerate(stream, 1):
            stripped = line.strip()
            if not saw_header and stripped:
                if stripped != b'(DELAYFILE':
                    raise ValueError('Expected original DC DELAYFILE header')
                saw_header = True
            outside_strings = QUOTED.sub(b'', line)
            balance += outside_strings.count(b'(') - outside_strings.count(b')')
            if balance < 0:
                raise ValueError('Unexpected SDF close parenthesis at line %d' % line_number)
            if stripped == b'(CELL':
                if balance != 2 or (cell_count and not cell_has_instance):
                    raise ValueError('Unexpected/incomplete SDF CELL at line %d' % line_number)
                cell_count += 1
                current_celltype = current_instance = None
                cell_has_instance = False
            header = HEADER.match(line)
            if header:
                key = header[1].decode('ascii')
                if key in headers:
                    raise ValueError('Duplicate SDF header field: ' + key)
                headers[key] = header[2].decode('ascii').strip().strip('"')
            celltype = CELLTYPE.match(line)
            if celltype:
                if current_celltype is not None or not cell_count:
                    raise ValueError('Duplicate/unexpected SDF CELLTYPE')
                current_celltype = celltype[1].decode('ascii')
                celltypes[current_celltype] += 1
            instance = INSTANCE.match(line)
            if instance:
                if current_celltype is None or cell_has_instance:
                    raise ValueError('Duplicate/unexpected SDF INSTANCE')
                current_instance = instance[1].decode('ascii').strip()
                cell_has_instance = True
                if not current_instance:
                    empty_instances += 1
                    if current_celltype != TOP:
                        raise ValueError('Only the SDF top cell may have an empty INSTANCE')
                elif current_celltype == TOP:
                    raise ValueError('SDF top cell must have an empty INSTANCE')
                if current_celltype == SRAM:
                    if not current_instance or current_instance in sram_instances:
                        raise ValueError('Empty/duplicate SRAM SDF INSTANCE')
                    sram_instances.add(current_instance)
            names = [name.decode('ascii') for name in ENTRY.findall(line)]
            counts.update(names)
            timing_names = [name for name in names if name in TIMING_TYPES]
            if timing_names:
                if len(timing_names) != 1:
                    raise ValueError('Multiple timing checks on one DC SDF line')
                triples = TRIPLET.findall(line)
                expected_values = 2 if timing_names[0] in {'SETUPHOLD', 'RECREM', 'NOCHANGE'} else 1
                if len(triples) != expected_values:
                    raise ValueError('Unexpected timing-check numeric form: ' + timing_names[0])
                for triple in triples:
                    for selection, text in zip(['minimum', 'typical', 'maximum'], triple):
                        value = Decimal(text.decode('ascii'))
                        if not value.is_finite():
                            raise ValueError('Non-finite SDF timing check')
                        negative[selection] += value < 0
            if line_number % 1000000 == 0:
                print('PE_AXI_SDF_SCAN_LINES=' + str(line_number), flush=True)
    if not saw_header or balance or not cell_has_instance:
        raise ValueError('Truncated/unclosed SDF export')
    expected_headers = {'DESIGN': TOP, 'SDFVERSION': 'OVI 2.1', 'PROCESS': 'typical',
                        'TIMESCALE': '1ns', 'DIVIDER': '/'}
    if any(headers.get(key) != value for key, value in expected_headers.items()):
        raise ValueError('Unexpected AXI SDF design/version/corner/units/divider')
    expected_celltypes = Counter(netlist['leaf_cell_counts'])
    expected_celltypes[TOP] += 1
    if celltypes != expected_celltypes or cell_count != sum(expected_celltypes.values()) or empty_instances != 1:
        raise ValueError('SDF leaf cell types/counts differ from the exported mapped netlist')
    if sram_instances != expected_sram_paths:
        raise ValueError('SDF SRAM instance paths differ from the exported mapped hierarchy')
    if counts['IOPATH'] <= 0 or counts['INTERCONNECT'] <= 0 or not sum(counts[name] for name in TIMING_TYPES):
        raise ValueError('SDF must contain cell delays, interconnect delays and timing checks')
    return {'headers': headers, 'cell_count_including_top': cell_count,
            'exported_leaf_cells': cell_count - 1,
            'celltypes': dict(sorted(celltypes.items())), 'entries': dict(sorted(counts.items())),
            'negative_timing_limits': dict(negative), 'sram_instances': len(sram_instances),
            'sram_paths_match_netlist': True,
            'sram_instance_paths': sorted(sram_instances)}


def scan_reports(paths):
    warnings, codes = Counter(), Counter()
    for path in paths:
        with path.open('rb') as stream:
            for line_number, line in enumerate(stream, 1):
                if ERROR.match(line):
                    raise ValueError('Error in %s:%d: %s' %
                                     (path.name, line_number, line.decode('utf-8', 'replace').strip()))
                if WARNING.match(line):
                    warnings[path.name] += 1
                    for first, second in CODE.findall(line):
                        codes[(first or second).decode('ascii')] += 1
    return {'warning_lines_by_file': dict(sorted(warnings.items())),
            'warning_codes': dict(sorted(codes.items())),
            'meaning': 'Warnings retained; export completion does not imply timing acceptance.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path)
    output = parser.parse_args().output_dir.resolve()
    if not output.is_dir():
        raise ValueError('Missing export directory: ' + str(output))
    manifest = output / 'artifact_summary.json'
    summary = {'status': 'CHECKING', 'top': TOP, 'rows': 16, 'columns': 16,
               'scope': 'Paired mapped AXI/PE netlist and typical pre-layout synthesis SDF export.',
               'compile_performed_by_this_step': False, 'gate_simulation_performed': False,
               'sdf_annotated': False, 'formal_equivalence_checked': False,
               'full_soc_verified': False, 'cpu_included': False, 'physical_parasitics': False,
               'timing_acceptance_established_by_this_check': False}
    try:
        libraries = [TASK / 'libraries/standard_timing.v', TASK / 'libraries/sram_timing.v']
        dependencies = [Path(__file__).resolve(), *libraries,
                        PROJECT / 'synth/export_pe_axi.tcl',
                        PROJECT / 'synth/pe_axi_fingerprint.tcl',
                        PROJECT / 'build/dc_libraries/standard.db',
                        PROJECT / 'build/dc_libraries/sram.db']
        paths = {name: output / name for name in ARTIFACTS}
        all_paths = list(paths.values()) + dependencies
        before = {}
        artifacts = {}
        for path in all_paths:
            if not path.is_file() or path.stat().st_size <= 0:
                raise ValueError('Missing or empty export/check input: ' + str(path))
            before[path] = fingerprint(path)
            record = {'path': str(path), 'size_bytes': before[path][2], 'sha256': checksum(path)}
            if path in paths.values():
                artifacts[path.name] = record
        summary['artifacts'] = artifacts
        summary['checker_dependencies'] = {str(path): checksum(path) for path in dependencies}
        values = scan_summary(paths['export_summary.rpt'])
        summary['source_design'] = values
        array_hash = artifacts['array_before.txt']['sha256']
        if (array_hash != artifacts['array_after.txt']['sha256'] or
                array_hash != values['array_fingerprint_before']):
            raise ValueError('Exported fingerprint files and recorded array fingerprints disagree')
        netlist, sram_paths = scan_netlist(paths['pe_axi_mapped.v'], libraries, values)
        summary['netlist'] = netlist
        summary['dc_cell_counts'] = scan_cell_counts(paths['cell_counts.rpt'], netlist, values)
        summary['sdf'] = scan_sdf(paths['pe_axi_mapped.sdf'], netlist, sram_paths)
        summary['report_diagnostics'] = scan_reports([path for path in paths.values() if path.suffix == '.rpt'])
        for path, state in before.items():
            if fingerprint(path) != state:
                raise RuntimeError('Export/check input changed during verification: ' + str(path))
        summary['status'] = 'EXPORTED'
        print('PE_AXI_ARTIFACTS_CHECKED pe_instances=256 sram_instances=256', flush=True)
    except BaseException as exc:
        summary.update(status='FAIL', error=str(exc))
        raise
    finally:
        manifest.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PE_AXI_ARTIFACTS_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
