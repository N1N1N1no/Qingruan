#!/usr/bin/env python3
"""Prepare a value-preserving, independently mapped whole-SoC SDF copy.

Only the OVI 2.1 version spelling and exact adjacent RN/CK recovery/hold
pairs change. Every leaf instance is checked against the mapped Verilog;
the original bytes are reconstructed in a hash stream. No SDF is buffered.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

from prepare_pe_axi_sdf import (
    CHECK_TYPES, TIMING_TYPES, CELLTYPE, INSTANCE, HEADER, QUOTED,
    RECOVERY, HOLD, OLD_VERSION, NEW_VERSION, SELECTIONS,
    _count_line, _fingerprint, _values, sha,
)

TOP = 'soc_top'
SRAM = 'SRAM_6T_CORE_16x8_MC_TB'
RESET_TYPES = {'DFFR_X1', 'DFFR_X2', 'SDFFR_X1'}
ALLOWED_FORMS = set(CHECK_TYPES) | {
    'DELAYFILE', 'SDFVERSION', 'DESIGN', 'DATE', 'VENDOR', 'PROGRAM',
    'VERSION', 'DIVIDER', 'VOLTAGE', 'PROCESS', 'TEMPERATURE', 'TIMESCALE',
    'CELL', 'CELLTYPE', 'INSTANCE', 'DELAY', 'ABSOLUTE', 'TIMINGCHECK',
    'COND', 'CONDELSE',
}


def _identifier(token):
    return token[1:] if token.startswith('\\') else token


def _sdf_path(text):
    """Split hierarchy at unescaped / and unescape each identifier once."""
    if not text:
        return []
    parts, part = [], []
    escaped = False
    for char in text:
        if escaped:
            part.append(char)
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == '/':
            if not part:
                raise ValueError('Empty SDF instance path component')
            parts.append(''.join(part))
            part = []
        else:
            part.append(char)
    if escaped or not part:
        raise ValueError('Truncated SDF escape or instance path')
    parts.append(''.join(part))
    return parts


class MappedInstances:
    """Resolve paths and mark ordinals without retaining expanded paths."""
    def __init__(self, netlist):
        raw = Path(netlist).read_text()
        text = re.sub(r'/\*.*?\*/|//[^\n]*', '', raw, flags=re.S)
        blocks = re.findall(r'(?ms)^\s*module\s+(\w+)\s*\(.*?;(.*?)^\s*endmodule\b', text)
        self.modules = {}
        for name, body in blocks:
            if name in self.modules or re.search(r'\balways(?:_\w+)?\b|\binitial\b', body):
                raise ValueError('Duplicate or behavioral mapped module: ' + name)
            children = {}
            for ref, instance in re.findall(r'(?m)^\s*(\w+)\s+(\\\S+|[A-Za-z_$][\w$]*)\s*\(', body):
                instance = _identifier(instance)
                if instance in children:
                    raise ValueError('Duplicate mapped instance: ' + name + '.' + instance)
                children[instance] = ref
            self.modules[name] = children
        if TOP not in self.modules:
            raise ValueError('Mapped netlist has no soc_top')
        self.sizes, self.leaves, self.offsets = {}, {}, {}

        def measure(name, active):
            if name in self.sizes:
                return
            if name in active:
                raise ValueError('Recursive mapped hierarchy: ' + name)
            if name not in self.modules:
                self.sizes[name] = 1
                self.leaves[name] = Counter({name: 1})
                return
            size, counts, offsets = 1, Counter(), {}
            for instance, ref in self.modules[name].items():
                measure(ref, active | {name})
                offsets[instance] = size
                size += self.sizes[ref]
                counts.update(self.leaves[ref])
            if not counts:
                raise ValueError('Empty mapped module: ' + name)
            self.sizes[name], self.leaves[name], self.offsets[name] = size, counts, offsets

        measure(TOP, set())
        self.expected_leaves = self.leaves[TOP]
        self.leaf_count = sum(self.expected_leaves.values())
        if self.expected_leaves[SRAM] != 256:
            raise ValueError('Expected 256 physical SRAM leaves in soc_top')
        self.seen = bytearray((self.sizes[TOP] + 7) // 8)
        self.physical_seen, self.hierarchical_seen = Counter(), Counter()
        self.identity_hash = hashlib.sha256()

    def mark(self, instance, celltype):
        ref, ordinal = TOP, 0
        for part in _sdf_path(instance):
            if ref not in self.modules or part not in self.modules[ref]:
                raise ValueError('SDF instance absent from mapped hierarchy: ' + instance)
            ordinal += self.offsets[ref][part]
            ref = self.modules[ref][part]
        if ref != celltype:
            raise ValueError('SDF celltype differs from mapped instance: ' + instance)
        mask = 1 << (ordinal % 8)
        if self.seen[ordinal // 8] & mask:
            raise ValueError('Duplicate SDF instance: ' + instance)
        self.seen[ordinal // 8] |= mask
        target = self.hierarchical_seen if ref in self.modules else self.physical_seen
        target[ref] += 1
        self.identity_hash.update((instance + '\t' + celltype + '\n').encode('ascii'))

    def finish(self):
        if self.physical_seen != self.expected_leaves:
            raise ValueError('SDF does not contain every physical mapped leaf exactly once')
        if self.hierarchical_seen[TOP] != 1:
            raise ValueError('SDF does not contain exactly one root soc_top cell')


def prepare_soc_sdf(source, target, netlist):
    """Write simulation SDF, reversible changes JSONL and compatibility JSON."""
    source, target, netlist = map(Path, (source, target, netlist))
    if source.resolve() == target.resolve():
        raise ValueError('Refusing to overwrite original SDF')
    states = {path: _fingerprint(path) for path in (source, netlist)}
    hierarchy = MappedInstances(netlist)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    changes = target.with_suffix(target.suffix + '.changes.jsonl')
    changes_partial = changes.with_name(changes.name + '.partial')
    manifest = target.with_suffix(target.suffix + '.compatibility.json')
    manifest.unlink(missing_ok=True)
    before, after, celltypes, converted = [Counter() for _ in range(4)]
    negative_before = Counter({name: 0 for name in SELECTIONS})
    negative_after = Counter({name: 0 for name in SELECTIONS})
    source_hash, output_hash, reconstructed_hash, changes_hash = [hashlib.sha256() for _ in range(4)]
    source_lines = output_lines = source_bytes = output_bytes = 0
    balance = cell_count = 0
    current_cell = current_instance = None
    current_pairs = 0
    headers = {}

    def finalize_cell():
        if current_cell is None:
            if cell_count:
                raise ValueError('SDF CELL lacks CELLTYPE')
            return
        if current_instance is None:
            raise ValueError('SDF CELL lacks INSTANCE')
        if current_pairs != (1 if current_cell in RESET_TYPES else 0):
            raise ValueError('Incorrect reset pair count at ' + current_instance)

    try:
        with source.open('rb') as original, partial.open('wb') as simulation, changes_partial.open('wb') as audit:
            def consume(line):
                nonlocal source_lines, source_bytes, balance
                source_lines += 1
                if source_lines == 1 and line.strip() != b'(DELAYFILE':
                    raise ValueError('Expected original DC DELAYFILE')
                form = re.match(rb'^\s*\(([A-Z][A-Z0-9_]*)\b', line)
                if form and form[1].decode('ascii') not in ALLOWED_FORMS:
                    raise ValueError('Unreviewed SDF form at line ' + str(source_lines))
                outside = QUOTED.sub(b'', line)
                balance += outside.count(b'(') - outside.count(b')')
                if balance < 0:
                    raise ValueError('Unmatched SDF close parenthesis')
                source_bytes += len(line)
                source_hash.update(line)
                _count_line(line, before, negative_before)
                if source_lines % 1000000 == 0:
                    print('SOC_SDF_CONVERSION_LINES=' + str(source_lines), flush=True)

            def emit(line, reconstructed=None):
                nonlocal output_lines, output_bytes
                simulation.write(line)
                output_lines += line.count(b'\n')
                output_bytes += len(line)
                output_hash.update(line)
                reconstructed_hash.update(line if reconstructed is None else reconstructed)
                _count_line(line, after, negative_after)

            for line in original:
                consume(line)
                stripped = line.strip()
                header = HEADER.match(line)
                if header:
                    key = header[1].decode('ascii')
                    if key in headers:
                        raise ValueError('Duplicate SDF header: ' + key)
                    headers[key] = header[2].decode('ascii').strip().strip('"')
                if stripped.startswith(b'(SDFVERSION '):
                    if stripped != OLD_VERSION:
                        raise ValueError('Expected original OVI 2.1 SDF version')
                    emit(line.replace(OLD_VERSION, NEW_VERSION), line)
                    continue
                if stripped == b'(CELL':
                    finalize_cell()
                    current_cell = current_instance = None
                    current_pairs = 0
                    cell_count += 1
                cell = CELLTYPE.match(line)
                if cell:
                    if current_cell is not None or not cell_count:
                        raise ValueError('Duplicate/unexpected CELLTYPE')
                    current_cell = cell[1].decode('ascii')
                    celltypes[current_cell] += 1
                instance = INSTANCE.match(line)
                if instance:
                    if current_cell is None or current_instance is not None:
                        raise ValueError('Duplicate/unexpected INSTANCE')
                    current_instance = instance[1].decode('ascii').strip()
                    if (not current_instance) != (current_cell == TOP):
                        raise ValueError('Only root soc_top may have an empty instance')
                    hierarchy.mark(current_instance, current_cell)
                if b'(RECREM ' in line or b'(REMOVAL ' in line:
                    raise ValueError('Source must retain original DC reset checks')
                if b'(RECOVERY ' not in line:
                    emit(line)
                    continue
                recovery = RECOVERY.fullmatch(line)
                if not recovery or current_cell not in RESET_TYPES or current_instance is None or current_pairs:
                    raise ValueError('Unreviewed recovery check at line ' + str(source_lines))
                hold_line = original.readline()
                if not hold_line:
                    raise ValueError('Truncated reset pair')
                consume(hold_line)
                hold = HOLD.fullmatch(hold_line)
                if not hold or any(recovery[name] != hold[name] for name in ('indent', 'reset', 'clock', 'ending')):
                    raise ValueError('Recovery must be followed by the exact reset HOLD pair')
                recovery_value, removal_value = recovery['value'], hold['value']
                if any(a + b < 0 for a, b in zip(_values(recovery_value), _values(removal_value))):
                    raise ValueError('Negative recovery/removal total window')
                combined = (recovery['indent'] + b'(RECREM ' + recovery['reset'] + b' ' +
                            recovery['clock'] + b' ' + recovery_value + b' ' + removal_value + b')' + recovery['ending'])
                emit(combined, line + hold_line)
                current_pairs += 1
                converted[current_cell] += 1
                change = {'celltype': current_cell, 'instance': current_instance,
                          'source_recovery_line': source_lines - 1,
                          'original': (line + hold_line).decode('ascii'),
                          'simulation': combined.decode('ascii')}
                encoded = (json.dumps(change, separators=(',', ':')) + '\n').encode('ascii')
                audit.write(encoded)
                changes_hash.update(encoded)
            finalize_cell()
        hierarchy.finish()
        expected_headers = {'DESIGN': TOP, 'SDFVERSION': 'OVI 2.1', 'PROCESS': 'typical',
                            'TIMESCALE': '1ns', 'DIVIDER': '/'}
        if any(headers.get(key) != value for key, value in expected_headers.items()):
            raise ValueError('Unexpected original SDF design/version/corner/units/divider')
        expected_reset = Counter({name: hierarchy.expected_leaves[name] for name in RESET_TYPES
                                  if hierarchy.expected_leaves[name]})
        total_pairs = sum(expected_reset.values())
        expected_after = before.copy()
        expected_after['RECOVERY'] -= total_pairs
        expected_after['HOLD'] -= total_pairs
        expected_after['RECREM'] += total_pairs
        if (not total_pairs or converted != expected_reset or before['RECOVERY'] != total_pairs or
                before['HOLD'] < total_pairs or before['RECREM'] or before['REMOVAL'] or
                any(after[key] != expected_after[key] for key in CHECK_TYPES)):
            raise ValueError('Unexpected before/after SDF reset or entry counts')
        if (balance or source_hash.digest() != reconstructed_hash.digest() or
                source_bytes != states[source][2] or negative_before != negative_after):
            raise ValueError('SDF byte reconstruction, numeric limits or structure failed')
        if any(_fingerprint(path) != state for path, state in states.items()):
            raise RuntimeError('SDF or mapped Verilog changed during preparation')
        record = {
            'top': TOP, 'source': str(source.resolve()), 'source_sha256': source_hash.hexdigest(),
            'netlist': str(netlist.resolve()), 'netlist_sha256': sha(netlist),
            'simulation_sdf': str(target.resolve()), 'simulation_sdf_sha256': output_hash.hexdigest(),
            'source_bytes': source_bytes, 'simulation_sdf_bytes': output_bytes,
            'source_lines': source_lines, 'simulation_sdf_lines': output_lines,
            'source_sdfversion': 'OVI 2.1', 'simulation_sdfversion': '3.0',
            'headers': headers, 'reset_pairs_converted': total_pairs, 'celltype_counts': dict(converted),
            'all_celltype_counts': dict(sorted(celltypes.items())),
            'sdf_entries_before': {name: before[name] for name in CHECK_TYPES},
            'sdf_entries_after': {name: after[name] for name in CHECK_TYPES},
            'sdf_selection': 'MAXIMUM', 'expected_negative_limits': negative_after['maximum'],
            'negative_limits_before': dict(negative_before), 'negative_limits_after': dict(negative_after),
            'leaf_cells': hierarchy.leaf_count, 'physical_leaf_celltypes': dict(sorted(hierarchy.expected_leaves.items())),
            'sram_instances': hierarchy.expected_leaves[SRAM], 'sdf_cells': cell_count,
            'hierarchical_sdf_cells': dict(sorted(hierarchy.hierarchical_seen.items())),
            'mapped_module_definitions': len(hierarchy.modules),
            'elaborated_mapped_instances': hierarchy.sizes[TOP],
            'instance_paths_and_celltypes_checked': True,
            'checked_instance_identity_sha256': hierarchy.identity_hash.hexdigest(),
            'value_triplets_preserved_exactly': True, 'other_bytes_unchanged': True,
            'source_reconstructed_sha256': reconstructed_hash.hexdigest(),
            'changes_jsonl': str(changes.resolve()), 'changes_jsonl_sha256': changes_hash.hexdigest(),
            'manifest': str(manifest.resolve()),
        }
        os.replace(partial, target)
        os.replace(changes_partial, changes)
        manifest.write_text(json.dumps(record, indent=2) + '\n')
        return record
    except BaseException:
        partial.unlink(missing_ok=True)
        changes_partial.unlink(missing_ok=True)
        raise


def validate_soc_annotation(case_dir, adaptation, reviewed_warning_count=0):
    """Match every SDF path/check count; optional warnings require prior review."""
    case = Path(case_dir)
    report, log_path = case / 'sdfAnnotateInfo', case / 'sdf_annotation.log'
    if adaptation.get('top') != TOP or adaptation.get('sdf_selection') != 'MAXIMUM':
        raise ValueError('Expected whole-SoC MAXIMUM SDF adaptation')
    if not isinstance(reviewed_warning_count, int) or reviewed_warning_count < 0:
        raise ValueError('Invalid explicitly reviewed warning count')
    log = log_path.read_text()
    warnings = re.findall(r'(?m)^Warning-\[([^]]+)\]', log)
    errors = re.findall(r'Total errors:\s*(\d+)', log)
    totals = re.findall(r'Total warnings:\s*(\d+)', log)
    if (errors != ['0'] or totals != [str(reviewed_warning_count)] or
            len(warnings) != reviewed_warning_count or
            re.search(r'(?im)^\s*(?:Error|Fatal)(?:-|:)', log)):
        raise ValueError('SDF annotation errors, unreviewed warnings or missing totals')
    codes = re.findall(r'(?m)^\s*\[([^]]+)\][ \t]+(\d+)[ \t]*$', log)
    if Counter(warnings) != Counter({name: int(count) for name, count in codes}):
        raise ValueError('SDF diagnostic totals differ from actual warning records')
    headings = {'Static entries from SDF files:': 'source',
                'Annotated entries in elaborated design:': 'applied',
                'Static entries in elaborated design:': 'library'}
    blocks = {name: {} for name in headings.values()}
    seen, mode = set(), None
    row = re.compile(r'^#[ \t]+([^=\n]+?)[ \t]*=[ \t]*(\d+)[ \t]*$')
    for line in report.read_text().splitlines():
        heading = line.lstrip('#').strip()
        if heading in headings:
            mode = headings[heading]
            if mode in seen:
                raise ValueError('Duplicate VCS SDF summary section')
            seen.add(mode)
            continue
        match = row.match(line)
        if match and mode is not None:
            key, value = match.groups()
            key = key.strip()
            if key in blocks[mode]:
                raise ValueError('Duplicate VCS SDF summary count')
            blocks[mode][key] = int(value)
    if seen != set(blocks):
        raise ValueError('Missing VCS SDF summary section')
    entries = adaptation['sdf_entries_after']
    expected_source = {key: value for key, value in entries.items() if value}
    checks = sum(entries[key] for key in TIMING_TYPES)
    expected_source['TIMINGCHECK(ALL)'] = checks
    if blocks['source'] != expected_source:
        raise ValueError('VCS source counts differ from every simulation-SDF entry')
    limits = checks + entries['RECREM'] + entries['SETUPHOLD']
    expected_applied = {'IOPATH Delays': entries['IOPATH'],
                        'INTERCONNECT(MIPD)': entries['INTERCONNECT'],
                        'Timing Checks': limits,
                        'Negative Tchecks': adaptation['expected_negative_limits'],
                        'Annotated instances': adaptation['leaf_cells']}
    if any(blocks['applied'].get(key) != value for key, value in expected_applied.items()):
        raise ValueError('Incomplete whole-SoC SDF annotation: ' + str(report))
    if (blocks['applied'].get('Total instances', -1) < adaptation['leaf_cells'] or limits <= 0 or
            adaptation['expected_negative_limits'] <= 0 or
            blocks['library'].get('IOPATH Delays', -1) < entries['IOPATH'] or
            blocks['library'].get('Timing Checks', -1) < limits):
        raise ValueError('Invalid library/instance totals or absent negative timing checks')
    return {'source': blocks['source'], 'applied': blocks['applied'], 'library': blocks['library'],
            'expected_limits': limits, 'expected_negative_limits': adaptation['expected_negative_limits'],
            'report': str(report.resolve()), 'report_sha256': sha(report),
            'annotation_log': str(log_path.resolve()), 'annotation_log_sha256': sha(log_path),
            'annotation_errors': 0, 'reviewed_annotation_warnings': reviewed_warning_count,
            'reviewed_warning_codes': dict(Counter(warnings))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('netlist', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_soc_sdf(args.source, args.target, args.netlist), indent=2))


if __name__ == '__main__':
    main()
