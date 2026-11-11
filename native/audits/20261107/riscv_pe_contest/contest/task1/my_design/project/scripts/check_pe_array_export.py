#!/usr/bin/env python3
"""Check exported 16x16 array artifacts using bounded-memory file scans.

This verifies exported file presence and basic structure. It does not run
compile, prove equivalence, annotate SDF, or establish timing closure.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys


ARTIFACTS = (
    'pe_array_mapped.v', 'pe_array_mapped.ddc', 'pe_array_constraints.sdc',
    'pe_array_mapped.sdf', 'area.rpt', 'setup.rpt', 'hold.rpt',
    'violations.rpt', 'constraints_check.rpt', 'export_summary.rpt',
)
TIMING_CHECKS = (
    'SETUP', 'HOLD', 'SETUPHOLD', 'RECOVERY', 'REMOVAL', 'RECREM',
    'WIDTH', 'PERIOD', 'SKEW', 'NOCHANGE',
)
ENTRY = re.compile(rb'\((IOPATH|INTERCONNECT|' +
                   b'|'.join(name.encode('ascii') for name in TIMING_CHECKS) +
                   rb')\b')
QUOTED_STRING = re.compile(rb'"(?:\\.|[^"\\])*"')
CELLTYPE = re.compile(rb'^\s*\(CELLTYPE\s+"([^"]+)"\)\s*$')
INSTANCE = re.compile(rb'^\s*\(INSTANCE\s*([^()]*)\)\s*$')
HEADER = re.compile(rb'^\s*\((DESIGN|SDFVERSION|PROCESS|TIMESCALE)\s+([^\n]+)\)\s*$')
MODULE = re.compile(rb'^\s*module\s+([^\s(#;]+)')
ENDMODULE = re.compile(rb'^\s*endmodule\b')
PE_INSTANCE = re.compile(rb'^\s*pe_1sram_4mul\s+([^\s(]+)\s*\(')
ERROR = re.compile(rb'^\s*(?:Error(?::|-\[)|Fatal:|FATAL:)', re.I)
WARNING = re.compile(rb'^\s*Warning(?::|-\[)')
DIAGNOSTIC_CODE = re.compile(rb'\(([A-Z]+-\d+)\)|Warning-\[([^]]+)\]')


def fingerprint(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def scan_netlist(path):
    modules = Counter()
    instances = set()
    current_module = None
    with path.open('rb') as stream:
        for line_number, line in enumerate(stream, 1):
            declaration = MODULE.match(line)
            if declaration:
                if current_module is not None:
                    raise ValueError('Nested/unclosed module at netlist line %d' % line_number)
                current_module = declaration.group(1).decode('ascii')
                modules[current_module] += 1
            instance = PE_INSTANCE.match(line)
            if instance:
                if current_module != 'pe_array':
                    raise ValueError('PE instance outside pe_array at netlist line %d' % line_number)
                name = instance.group(1).decode('ascii')
                if name in instances:
                    raise ValueError('Duplicate PE instance: ' + name)
                instances.add(name)
            if ENDMODULE.match(line):
                if current_module is None:
                    raise ValueError('Unexpected endmodule at netlist line %d' % line_number)
                current_module = None
    if current_module is not None:
        raise ValueError('Truncated netlist: unterminated module ' + current_module)
    if modules.get('pe_array') != 1 or modules.get('pe_1sram_4mul') != 1:
        raise ValueError('Expected one pe_array and one shared pe_1sram_4mul definition')
    if any(count != 1 for count in modules.values()):
        raise ValueError('Duplicate module definitions in the exported netlist')
    if len(instances) != 256:
        raise ValueError('Expected 256 PE instances; found %d' % len(instances))
    return {'module_definitions': len(modules), 'pe_instances': len(instances),
            'top': 'pe_array', 'reused_pe_module': 'pe_1sram_4mul'}


def scan_sdf(path):
    counts = Counter()
    celltypes = Counter()
    headers = {}
    sram_instances = set()
    current_celltype = None
    cell_has_instance = False
    balance = 0
    saw_first_line = False
    with path.open('rb') as stream:
        for line_number, line in enumerate(stream, 1):
            if not saw_first_line and line.strip():
                if line.strip() != b'(DELAYFILE':
                    raise ValueError('Expected the DC SDF DELAYFILE header')
                saw_first_line = True
            # DC exports one complete quoted field on each line. Excluding
            # strings keeps header text from affecting structural parentheses.
            outside_strings = QUOTED_STRING.sub(b'', line)
            balance += outside_strings.count(b'(') - outside_strings.count(b')')
            if balance < 0:
                raise ValueError('Unexpected SDF close parenthesis at line %d' % line_number)
            header = HEADER.match(line)
            if header:
                name = header.group(1).decode('ascii')
                if name in headers:
                    raise ValueError('Duplicate SDF header field: ' + name)
                headers[name] = header.group(2).decode('ascii').strip().strip('"')
            celltype = CELLTYPE.match(line)
            if celltype:
                if current_celltype is not None and not cell_has_instance:
                    raise ValueError('SDF cell is missing its INSTANCE field')
                current_celltype = celltype.group(1).decode('ascii')
                celltypes[current_celltype] += 1
                cell_has_instance = False
            instance = INSTANCE.match(line)
            if instance:
                if current_celltype is None or cell_has_instance:
                    raise ValueError('Unexpected SDF INSTANCE at line %d' % line_number)
                cell_has_instance = True
                if current_celltype == 'SRAM_6T_CORE_16x8_MC_TB':
                    name = instance.group(1).decode('ascii').strip()
                    if not name or name in sram_instances:
                        raise ValueError('Empty/duplicate SRAM SDF instance: ' + name)
                    sram_instances.add(name)
            for entry in ENTRY.findall(line):
                counts[entry.decode('ascii')] += 1
    if not saw_first_line or balance != 0 or not cell_has_instance:
        raise ValueError('Truncated or structurally unclosed SDF file')
    if headers.get('DESIGN') != 'pe_array':
        raise ValueError('SDF DESIGN must be pe_array')
    if headers.get('SDFVERSION') != 'OVI 2.1':
        raise ValueError('Expected the original DC SDF 2.1 export')
    if headers.get('PROCESS') != 'typical' or headers.get('TIMESCALE') != '1ns':
        raise ValueError('Expected the typical library corner with 1ns SDF units')
    if (celltypes.get('SRAM_6T_CORE_16x8_MC_TB') != 256 or
            len(sram_instances) != 256):
        raise ValueError('Expected 256 unique SRAM cells in SDF')
    if counts.get('IOPATH', 0) <= 0 or counts.get('INTERCONNECT', 0) <= 0:
        raise ValueError('SDF must contain path and interconnect delays')
    if sum(counts.get(name, 0) for name in TIMING_CHECKS) <= 0:
        raise ValueError('SDF must contain timing checks')
    return {'headers': headers, 'celltypes': dict(sorted(celltypes.items())),
            'entries': dict(sorted(counts.items())),
            'sram_instances': len(sram_instances)}


def scan_report_diagnostics(paths):
    warnings = Counter()
    codes = Counter()
    for path in paths:
        with path.open('rb') as stream:
            for line_number, line in enumerate(stream, 1):
                if ERROR.match(line):
                    raise ValueError('Error diagnostic in %s:%d: %s' %
                                     (path.name, line_number, line.decode('utf-8', 'replace').strip()))
                if WARNING.match(line):
                    warnings[path.name] += 1
                    for first, second in DIAGNOSTIC_CODE.findall(line):
                        codes[(first or second).decode('ascii')] += 1
    return {'warning_lines_by_file': dict(sorted(warnings.items())),
            'warning_codes': dict(sorted(codes.items())),
            'meaning': 'Warnings are retained; file export is not a timing acceptance result.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path)
    output_dir = parser.parse_args().output_dir.resolve()
    if not output_dir.is_dir():
        raise ValueError('Output directory does not exist: ' + str(output_dir))
    summary = {
        'status': 'CHECKING', 'top': 'pe_array', 'rows': 16, 'columns': 16,
        'scope': 'Export of a 16x16 array reusing mapped PE cells; typical corner, pre-layout.',
        'compile_performed_by_this_step': False, 'gate_simulation_performed': False,
        'sdf_annotated': False, 'formal_equivalence_checked': False,
        'full_soc_verified': False, 'physical_parasitics': False,
        'timing_acceptance_established_by_this_check': False,
    }
    manifest = output_dir / 'artifact_summary.json'
    try:
        before = {}
        artifacts = {}
        for name in ARTIFACTS:
            path = output_dir / name
            if not path.is_file() or path.stat().st_size <= 0:
                raise ValueError('Missing or empty export artifact: ' + str(path))
            before[name] = fingerprint(path)
            artifacts[name] = {'path': str(path), 'size_bytes': before[name][2],
                               'sha256': checksum(path)}
        summary['artifacts'] = artifacts
        summary['netlist'] = scan_netlist(output_dir / 'pe_array_mapped.v')
        summary['sdf'] = scan_sdf(output_dir / 'pe_array_mapped.sdf')
        reports = [output_dir / name for name in ARTIFACTS if name.endswith('.rpt')]
        summary['report_diagnostics'] = scan_report_diagnostics(reports)
        for name, expected in before.items():
            if fingerprint(output_dir / name) != expected:
                raise RuntimeError('Artifact changed during verification: ' + name)
        summary['status'] = 'EXPORTED'
        print('PE_ARRAY_ARTIFACTS_CHECKED pe_instances=256 sram_instances=256', flush=True)
    except Exception as exc:
        summary.update(status='FAIL', error=str(exc))
        raise
    finally:
        manifest.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PE_ARRAY_ARTIFACTS_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
