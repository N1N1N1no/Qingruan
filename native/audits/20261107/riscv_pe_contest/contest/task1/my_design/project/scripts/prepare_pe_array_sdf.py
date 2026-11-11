#!/usr/bin/env python3
"""Stream DC's array SDF into a value-preserving VCS RECREM-compatible copy.

Only the SDF version and exact adjacent reset RECOVERY/HOLD pairs change.
The original file is untouched. No complete cell, SDF, or change list is kept
in memory; the large top cell is copied a line at a time.
"""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re


CHECK_TYPES = ('IOPATH', 'INTERCONNECT', 'SETUP', 'HOLD', 'SETUPHOLD',
               'RECOVERY', 'REMOVAL', 'RECREM', 'WIDTH', 'PERIOD')
TIMING_TYPES = set(CHECK_TYPES) - {'IOPATH', 'INTERCONNECT'}
ENTRY = re.compile(rb'\((' + b'|'.join(x.encode('ascii') for x in CHECK_TYPES) + rb')\s')
TRIPLET = re.compile(rb'\(([-+0-9.eE]+):([-+0-9.eE]+):([-+0-9.eE]+)\)')
RECOVERY = re.compile(
    rb'^(?P<indent>[ \t]*)\(RECOVERY (?P<reset>\(posedge RN\)) '
    rb'(?P<clock>\(posedge CK\)) (?P<value>\([^()\r\n]+\))\)(?P<ending>\r?\n)$')
HOLD = re.compile(
    rb'^(?P<indent>[ \t]*)\(HOLD (?P<reset>\(posedge RN\)) '
    rb'(?P<clock>\(posedge CK\)) (?P<value>\([^()\r\n]+\))\)(?P<ending>\r?\n)$')
CELLTYPE = re.compile(rb'^\s*\(CELLTYPE "([^"\r\n]+)"\)\s*$')
INSTANCE = re.compile(rb'^\s*\(INSTANCE\s*([^()\r\n]*)\)\s*$')
QUOTED = re.compile(rb'"(?:\\.|[^"\\])*"')
OLD_VERSION = b'(SDFVERSION "OVI 2.1")'
NEW_VERSION = b'(SDFVERSION "3.0")'
EXPECTED_ENTRIES = {'IOPATH': 3488768, 'INTERCONNECT': 1462016,
                    'SETUP': 214528, 'HOLD': 290304, 'RECOVERY': 75776}
EXPECTED_RESET_CELLS = {'DFFR_X1': 60928, 'SDFFR_X1': 14848}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _values(value):
    match = TRIPLET.fullmatch(value)
    if not match:
        raise ValueError('Expected a complete numeric min:typ:max timing value')
    result = [Decimal(x.decode('ascii')) for x in match.groups()]
    if not all(x.is_finite() for x in result):
        raise ValueError('Non-finite timing value')
    return result


def _count_line(line, counts):
    """Count actual file entries and MAXIMUM-selected negative timing limits."""
    names = [x.decode('ascii') for x in ENTRY.findall(line)]
    counts.update(names)
    timing = [name for name in names if name in TIMING_TYPES]
    if not timing:
        return 0
    if len(timing) != 1:
        raise ValueError('Expected one timing check per DC output line')
    values = TRIPLET.findall(line)
    expected_limits = 2 if timing[0] in {'RECREM', 'SETUPHOLD'} else 1
    if len(values) != expected_limits:
        raise ValueError('Unexpected timing-check value format: ' + timing[0])
    negative = 0
    for triple in values:
        decoded = [Decimal(x.decode('ascii')) for x in triple]
        if not all(x.is_finite() for x in decoded):
            raise ValueError('Non-finite timing-check limit')
        negative += decoded[2] < 0
    return negative


def prepare_array_sdf(source, target):
    """Write the array simulation SDF, change JSONL, and compatibility manifest."""
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError('Refusing to overwrite the original SDF')
    source_before = _fingerprint(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    changes = target.with_suffix(target.suffix + '.changes.jsonl')
    changes_partial = changes.with_name(changes.name + '.partial')
    manifest = target.with_suffix(target.suffix + '.compatibility.json')
    before = Counter()
    after = Counter()
    celltypes = Counter()
    converted = Counter()
    pe_counts = {}
    sram_instances = set()
    source_hash, output_hash, reconstructed_hash, changes_hash = [hashlib.sha256() for _ in range(4)]
    source_lines = output_lines = source_bytes = output_bytes = 0
    source_balance = 0
    negative_before = negative_after = 0
    version_count = design_count = 0
    current_cell = None
    current_instance = None
    current_pairs = 0
    cell_count = 0

    def finalize_cell():
        if current_cell is None:
            return
        if current_instance is None:
            raise ValueError('SDF cell has no INSTANCE field: ' + current_cell)
        if current_pairs != (1 if current_cell in EXPECTED_RESET_CELLS else 0):
            raise ValueError('Expected exactly one reset pair in ' + current_instance)

    try:
        with source.open('rb') as original, partial.open('wb') as simulation, changes_partial.open('wb') as audit:
            def consume(line):
                nonlocal source_lines, source_bytes, negative_before, source_balance
                source_lines += 1
                if source_lines == 1 and line.strip() != b'(DELAYFILE':
                    raise ValueError('Expected the DC DELAYFILE header')
                outside_strings = QUOTED.sub(b'', line)
                source_balance += outside_strings.count(b'(') - outside_strings.count(b')')
                if source_balance < 0:
                    raise ValueError('Unmatched SDF closing parenthesis')
                source_bytes += len(line)
                source_hash.update(line)
                negative_before += _count_line(line, before)

            def emit(line, reconstructed=None):
                nonlocal output_lines, output_bytes, negative_after
                simulation.write(line)
                output_lines += line.count(b'\n')
                output_bytes += len(line)
                output_hash.update(line)
                reconstructed_hash.update(line if reconstructed is None else reconstructed)
                negative_after += _count_line(line, after)

            for line in original:
                consume(line)
                stripped = line.strip()
                if stripped.startswith(b'(SDFVERSION '):
                    if stripped != OLD_VERSION or version_count:
                        raise ValueError('Expected exactly one original DC SDF 2.1 version field')
                    version_count += 1
                    emit(line.replace(OLD_VERSION, NEW_VERSION), line)
                    continue
                if stripped.startswith(b'(DESIGN '):
                    if stripped != b'(DESIGN "pe_array")' or design_count:
                        raise ValueError('Expected one pe_array SDF DESIGN field')
                    design_count += 1
                if stripped == b'(CELL':
                    finalize_cell()
                    current_cell = current_instance = None
                    current_pairs = 0
                    cell_count += 1
                cell = CELLTYPE.match(line)
                if cell:
                    if current_cell is not None:
                        raise ValueError('Duplicate CELLTYPE before next CELL')
                    current_cell = cell.group(1).decode('ascii')
                    celltypes[current_cell] += 1
                instance = INSTANCE.match(line)
                if instance:
                    if current_cell is None or current_instance is not None:
                        raise ValueError('Unexpected INSTANCE field')
                    current_instance = instance.group(1).decode('ascii').strip()
                    if current_cell in set(EXPECTED_RESET_CELLS) | {'SRAM_6T_CORE_16x8_MC_TB'}:
                        prefix = current_instance.split('/', 1)[0]
                        if not prefix or '/' not in current_instance:
                            raise ValueError('Expected the generated PE hierarchy for ' + current_instance)
                        pe_counts.setdefault(prefix, Counter())[current_cell] += 1
                    if current_cell == 'SRAM_6T_CORE_16x8_MC_TB':
                        if current_instance in sram_instances:
                            raise ValueError('Duplicate SRAM instance')
                        sram_instances.add(current_instance)
                if b'(RECREM ' in line or b'(REMOVAL ' in line:
                    raise ValueError('Expected untouched DC reset checks, not a preadapted SDF')
                if b'(RECOVERY ' not in line:
                    emit(line)
                    continue
                recovery = RECOVERY.fullmatch(line)
                if (not recovery or current_cell not in EXPECTED_RESET_CELLS or
                        current_instance is None or current_pairs):
                    raise ValueError('Unexpected/conditional recovery check at source line %d' % source_lines)
                hold_line = original.readline()
                if not hold_line:
                    raise ValueError('Truncated reset pair')
                consume(hold_line)
                hold = HOLD.fullmatch(hold_line)
                if not hold or any(recovery[name] != hold[name] for name in ['indent', 'reset', 'clock', 'ending']):
                    raise ValueError('Recovery must be followed by its exact reset HOLD pair')
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
                change_bytes = (json.dumps(change, separators=(',', ':')) + '\n').encode('ascii')
                audit.write(change_bytes)
                changes_hash.update(change_bytes)
            finalize_cell()
        normalized_before = {name: before[name] for name in CHECK_TYPES}
        normalized_after = {name: after[name] for name in CHECK_TYPES}
        expected_before = {name: EXPECTED_ENTRIES.get(name, 0) for name in CHECK_TYPES}
        expected_after = dict(expected_before)
        expected_after.update(HOLD=214528, RECOVERY=0, RECREM=75776)
        if normalized_before != expected_before or normalized_after != expected_after:
            raise ValueError('Unexpected source/output SDF entry counts')
        if dict(converted) != EXPECTED_RESET_CELLS or any(celltypes[name] != count for name, count in EXPECTED_RESET_CELLS.items()):
            raise ValueError('Incorrect DFFR/SDFFR reset pair count')
        expected_pe = {'DFFR_X1': 238, 'SDFFR_X1': 58, 'SRAM_6T_CORE_16x8_MC_TB': 1}
        if len(pe_counts) != 256 or any(dict(counts) != expected_pe for counts in pe_counts.values()):
            raise ValueError('Expected all 256 PE hierarchies to retain all their reset checks and SRAM')
        if celltypes['SRAM_6T_CORE_16x8_MC_TB'] != 256 or len(sram_instances) != 256:
            raise ValueError('Expected 256 unique SRAM cells')
        if cell_count != 780545 or celltypes['pe_array'] != 1:
            raise ValueError('Expected one array scope and 780544 annotated leaf cells')
        if (source_balance or version_count != 1 or design_count != 1 or
                source_hash.digest() != reconstructed_hash.digest()):
            raise ValueError('The simulation copy cannot reconstruct the original SDF exactly')
        if negative_before != 150016 or negative_after != negative_before:
            raise ValueError('Negative timing-check limits were lost or changed')
        if _fingerprint(source) != source_before:
            raise RuntimeError('Source changed while preparing SDF')
        record = {'source': str(source.resolve()), 'source_sha256': source_hash.hexdigest(),
                  'simulation_sdf': str(target.resolve()), 'simulation_sdf_sha256': output_hash.hexdigest(),
                  'source_bytes': source_bytes, 'simulation_sdf_bytes': output_bytes,
                  'source_lines': source_lines, 'simulation_sdf_lines': output_lines,
                  'source_sdfversion': 'OVI 2.1', 'simulation_sdfversion': '3.0',
                  'reset_pairs_converted': sum(converted.values()), 'celltype_counts': dict(converted),
                  'sdf_entries_before': normalized_before, 'sdf_entries_after': normalized_after,
                  'expected_negative_limits': negative_after, 'negative_limits_before': negative_before,
                  'leaf_cells': cell_count - 1, 'sram_instances': 256, 'pe_hierarchies': 256,
                  'value_triplets_preserved_exactly': True, 'other_bytes_unchanged': True,
                  'source_reconstructed_sha256': reconstructed_hash.hexdigest(),
                  'changes_jsonl': str(changes.resolve()), 'changes_jsonl_sha256': changes_hash.hexdigest(),
                  'manifest': str(manifest.resolve())}
        os.replace(partial, target)
        os.replace(changes_partial, changes)
        manifest.write_text(json.dumps(record, indent=2) + '\n')
        return record
    except BaseException:
        partial.unlink(missing_ok=True)
        changes_partial.unlink(missing_ok=True)
        raise


def validate_array_annotation(case, sdf_record):
    """Validate VCS +sdfsummary counts using conversion metadata, not the SDF."""
    report = Path(case) / 'sdfAnnotateInfo'
    headings = {'Static entries from SDF files:': 'source',
                'Annotated entries in elaborated design:': 'applied',
                'Static entries in elaborated design:': 'library'}
    blocks = {name: {} for name in headings.values()}
    seen = set()
    mode = None
    row = re.compile(r'^#[ \t]+([^=\n]+?)[ \t]*=[ \t]*(\d+)[ \t]*$')
    with report.open() as stream:
        for line in stream:
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
    entries = sdf_record['sdf_entries_after']
    expected_source = {key: value for key, value in entries.items() if value}
    checks = sum(entries[key] for key in TIMING_TYPES)
    expected_source['TIMINGCHECK(ALL)'] = checks
    if blocks['source'] != expected_source:
        raise ValueError('VCS summary source entries do not match the simulation SDF')
    limits = checks + entries['RECREM'] + entries['SETUPHOLD']
    expected_applied = {'IOPATH Delays': entries['IOPATH'], 'INTERCONNECT(MIPD)': entries['INTERCONNECT'],
                        'Timing Checks': limits, 'Negative Tchecks': sdf_record['expected_negative_limits'],
                        'Annotated instances': sdf_record['leaf_cells']}
    if any(blocks['applied'].get(key) != value for key, value in expected_applied.items()):
        raise ValueError('VCS annotation is incomplete: ' + str(report))
    if blocks['applied'].get('Total instances', -1) < sdf_record['leaf_cells']:
        raise ValueError('Invalid VCS total-instance count')
    if limits <= 0 or sdf_record['expected_negative_limits'] <= 0:
        raise ValueError('Expected timing checks including negative limits')
    return {'source': blocks['source'], 'applied': blocks['applied'],
            'library': blocks['library'], 'expected_limits': limits,
            'expected_negative_limits': sdf_record['expected_negative_limits'],
            'report': str(report.resolve()), 'report_sha256': sha(report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_array_sdf(args.source, args.target), indent=2))


if __name__ == '__main__':
    main()
