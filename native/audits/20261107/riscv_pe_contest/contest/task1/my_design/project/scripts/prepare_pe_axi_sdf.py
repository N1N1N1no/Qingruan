#!/usr/bin/env python3
"""Make an auditable VCS SDF copy of the exported AXI/PE system.

Only the SDF version and adjacent reset RECOVERY/HOLD pairs change. All
events, numeric triplets and other bytes are preserved, and the original
file is reconstructed in the hash stream. The large SDF is never buffered.
"""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re


TOP = 'sram_multiplier_system_ID_WIDTH3'
SRAM = 'SRAM_6T_CORE_16x8_MC_TB'
RESET_TYPES = {'DFFR_X1', 'DFFR_X2', 'SDFFR_X1'}
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
HEADER = re.compile(rb'^\s*\((DESIGN|SDFVERSION|PROCESS|TIMESCALE|DIVIDER)\s+([^\r\n]+)\)\s*$')
OLD_VERSION = b'(SDFVERSION "OVI 2.1")'
NEW_VERSION = b'(SDFVERSION "3.0")'
SELECTIONS = ('minimum', 'typical', 'maximum')


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


def _count_line(line, counts, negative):
    names = [x.decode('ascii') for x in ENTRY.findall(line)]
    counts.update(names)
    timing = [name for name in names if name in TIMING_TYPES]
    if not timing:
        return
    if len(timing) != 1:
        raise ValueError('Expected one timing check per DC SDF line')
    triples = TRIPLET.findall(line)
    expected_limits = 2 if timing[0] in {'RECREM', 'SETUPHOLD'} else 1
    if len(triples) != expected_limits:
        raise ValueError('Unexpected timing-check value format: ' + timing[0])
    for triple in triples:
        for name, value in zip(SELECTIONS, triple):
            decoded = Decimal(value.decode('ascii'))
            if not decoded.is_finite():
                raise ValueError('Non-finite timing-check limit')
            negative[name] += decoded < 0


def prepare_axi_sdf(source, target, export_manifest):
    """Convert using the paired export manifest (a Path or loaded dictionary)."""
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError('Refusing to overwrite the original SDF')
    manifest_path = None if isinstance(export_manifest, dict) else Path(export_manifest)
    manifest_state = None if manifest_path is None else _fingerprint(manifest_path)
    export = export_manifest if manifest_path is None else json.loads(manifest_path.read_text())
    if export.get('status') != 'EXPORTED' or export.get('top') != TOP:
        raise ValueError('Expected the successful paired AXI export manifest')
    exported_sdf = export['sdf']
    source_artifact = export['artifacts']['pe_axi_mapped.sdf']
    expected_headers = {'DESIGN': TOP, 'SDFVERSION': 'OVI 2.1', 'PROCESS': 'typical',
                        'TIMESCALE': '1ns', 'DIVIDER': '/'}
    if any(exported_sdf['headers'].get(key) != value for key, value in expected_headers.items()):
        raise ValueError('Unexpected source SDF design/version/corner/units/divider')
    expected_celltypes = Counter(exported_sdf['celltypes'])
    expected_reset = {name: expected_celltypes[name] for name in RESET_TYPES if expected_celltypes[name]}
    expected_before = {name: exported_sdf['entries'].get(name, 0) for name in CHECK_TYPES}
    if set(exported_sdf['entries']) - set(CHECK_TYPES):
        raise ValueError('Unreviewed SDF entry types in the export manifest')
    reset_total = sum(expected_reset.values())
    if (not reset_total or expected_before['RECOVERY'] != reset_total or
            expected_before['HOLD'] < reset_total or
            expected_before['RECREM'] or expected_before['REMOVAL']):
        raise ValueError('Expected one original recovery/hold reset pair per supported reset cell')
    expected_after = dict(expected_before)
    expected_after['RECOVERY'] = 0
    expected_after['HOLD'] -= reset_total
    expected_after['RECREM'] += reset_total
    expected_negative = {name: exported_sdf['negative_timing_limits'][name] for name in SELECTIONS}
    expected_sram_paths = set(exported_sdf['sram_instance_paths'])
    expected_leaves = exported_sdf['exported_leaf_cells']
    if (len(expected_sram_paths) != 256 or expected_celltypes[SRAM] != 256 or
            exported_sdf['sram_instances'] != 256 or expected_celltypes[TOP] != 1 or
            sum(expected_celltypes.values()) != expected_leaves + 1 or
            expected_leaves != export['netlist']['physical_leaf_cells']):
        raise ValueError('Inconsistent full-array cell counts in export manifest')
    source_before = _fingerprint(source)
    if source_before[2] != source_artifact['size_bytes']:
        raise ValueError('Source SDF size does not match its paired export manifest')
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    changes = target.with_suffix(target.suffix + '.changes.jsonl')
    changes_partial = changes.with_name(changes.name + '.partial')
    manifest = target.with_suffix(target.suffix + '.compatibility.json')
    manifest.unlink(missing_ok=True)
    before, after, celltypes, converted = [Counter() for _ in range(4)]
    negative_before = Counter({name: 0 for name in SELECTIONS})
    negative_after = Counter({name: 0 for name in SELECTIONS})
    sram_instances, headers = set(), {}
    source_hash, output_hash, reconstructed_hash, changes_hash = [hashlib.sha256() for _ in range(4)]
    source_lines = output_lines = source_bytes = output_bytes = 0
    source_balance = cell_count = 0
    current_cell = current_instance = None
    current_pairs = 0

    def finalize_cell():
        if current_cell is None:
            if cell_count:
                raise ValueError('SDF CELL is missing its CELLTYPE')
            return
        if current_instance is None:
            raise ValueError('SDF cell has no INSTANCE field: ' + current_cell)
        if current_pairs != (1 if current_cell in RESET_TYPES else 0):
            raise ValueError('Expected exactly one reset pair in ' + current_instance)

    try:
        with source.open('rb') as original, partial.open('wb') as simulation, changes_partial.open('wb') as audit:
            def consume(line):
                nonlocal source_lines, source_bytes, source_balance
                source_lines += 1
                if source_lines == 1 and line.strip() != b'(DELAYFILE':
                    raise ValueError('Expected the DC DELAYFILE header')
                outside_strings = QUOTED.sub(b'', line)
                source_balance += outside_strings.count(b'(') - outside_strings.count(b')')
                if source_balance < 0:
                    raise ValueError('Unmatched SDF closing parenthesis')
                source_bytes += len(line)
                source_hash.update(line)
                _count_line(line, before, negative_before)
                if source_lines % 1000000 == 0:
                    print('PE_AXI_SDF_CONVERSION_LINES=' + str(source_lines), flush=True)

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
                        raise ValueError('Duplicate SDF header field: ' + key)
                    headers[key] = header[2].decode('ascii').strip().strip('"')
                if stripped.startswith(b'(SDFVERSION '):
                    if stripped != OLD_VERSION:
                        raise ValueError('Expected the original DC SDF 2.1 version field')
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
                        raise ValueError('Duplicate/unexpected INSTANCE field')
                    current_instance = instance[1].decode('ascii').strip()
                    if (not current_instance) != (current_cell == TOP):
                        raise ValueError('Only the top SDF cell may have an empty INSTANCE')
                    if current_cell == SRAM:
                        if current_instance in sram_instances:
                            raise ValueError('Duplicate SRAM instance')
                        sram_instances.add(current_instance)
                if b'(RECREM ' in line or b'(REMOVAL ' in line:
                    raise ValueError('Expected untouched DC reset checks, not a preadapted SDF')
                if b'(RECOVERY ' not in line:
                    emit(line)
                    continue
                recovery = RECOVERY.fullmatch(line)
                if (not recovery or current_cell not in RESET_TYPES or
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
        if normalized_before != expected_before or normalized_after != expected_after:
            raise ValueError('Source/output SDF entries differ from the paired export counts')
        if dict(converted) != expected_reset or celltypes != expected_celltypes:
            raise ValueError('SDF cells or converted reset cells differ from the paired export')
        if (sram_instances != expected_sram_paths or cell_count != expected_leaves + 1):
            raise ValueError('SDF leaf count or SRAM paths differ from the paired export')
        if any(headers.get(key) != value for key, value in expected_headers.items()):
            raise ValueError('Source SDF headers differ from the paired export')
        if (source_balance or source_hash.digest() != reconstructed_hash.digest() or
                source_hash.hexdigest() != source_artifact['sha256']):
            raise ValueError('Cannot reconstruct the exact manifest-matched original SDF')
        if dict(negative_before) != expected_negative or negative_after != negative_before:
            raise ValueError('Negative timing-check limits were lost or changed')
        if _fingerprint(source) != source_before or (manifest_path is not None and _fingerprint(manifest_path) != manifest_state):
            raise RuntimeError('Source SDF or export manifest changed during preparation')
        record = {'source': str(source.resolve()), 'source_sha256': source_hash.hexdigest(),
                  'export_manifest': None if manifest_path is None else str(manifest_path.resolve()),
                  'export_manifest_sha256': None if manifest_path is None else sha(manifest_path),
                  'top': TOP, 'simulation_sdf': str(target.resolve()),
                  'simulation_sdf_sha256': output_hash.hexdigest(),
                  'source_bytes': source_bytes, 'simulation_sdf_bytes': output_bytes,
                  'source_lines': source_lines, 'simulation_sdf_lines': output_lines,
                  'source_sdfversion': 'OVI 2.1', 'simulation_sdfversion': '3.0',
                  'reset_pairs_converted': sum(converted.values()), 'celltype_counts': dict(converted),
                  'sdf_entries_before': normalized_before, 'sdf_entries_after': normalized_after,
                  'sdf_selection': 'MAXIMUM', 'expected_negative_limits': negative_after['maximum'],
                  'negative_limits_before': dict(negative_before), 'negative_limits_after': dict(negative_after),
                  'leaf_cells': expected_leaves, 'sram_instances': len(sram_instances),
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


def validate_axi_annotation(case, sdf_record):
    """Require VCS summary counts to match every exported path and check."""
    report = Path(case) / 'sdfAnnotateInfo'
    headings = {'Static entries from SDF files:': 'source',
                'Annotated entries in elaborated design:': 'applied',
                'Static entries in elaborated design:': 'library'}
    blocks = {name: {} for name in headings.values()}
    seen, mode = set(), None
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
        raise ValueError('VCS source-entry counts differ from the simulation SDF')
    limits = checks + entries['RECREM'] + entries['SETUPHOLD']
    expected_applied = {'IOPATH Delays': entries['IOPATH'],
                        'INTERCONNECT(MIPD)': entries['INTERCONNECT'],
                        'Timing Checks': limits,
                        'Negative Tchecks': sdf_record['expected_negative_limits'],
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
    parser.add_argument('export_manifest', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_axi_sdf(args.source, args.target, args.export_manifest), indent=2))


if __name__ == '__main__':
    main()
