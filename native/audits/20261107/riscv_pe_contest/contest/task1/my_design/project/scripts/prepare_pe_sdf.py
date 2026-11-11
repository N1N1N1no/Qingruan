#!/usr/bin/env python3
"""Adapt DC SDF 2.1 asynchronous reset checks to explicit SDF 3.0 RECREM.

DC 2016 emits a reset RECOVERY followed by HOLD. VCS does not map that
HOLD to the removal component of a model's $recrem. The simulation copy
combines the pair without changing its events, values, or other SDF arcs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re


VERSION = '(SDFVERSION "OVI 2.1")'
NEW_VERSION = '(SDFVERSION "3.0")'
RESET_PAIR = re.compile(
    r"(?m)^(?P<indent>[ \t]*)\(RECOVERY "
    r"(?P<reset>\(posedge RN\)) (?P<clock>\(posedge CK\)) "
    r"(?P<recovery>\([^()\n]+\))\)\n"
    r"(?P=indent)\(HOLD (?P=reset) (?P=clock) "
    r"(?P<removal>\([^()\n]+\))\)"
)
CHECK_TYPES = ('IOPATH', 'INTERCONNECT', 'SETUP', 'HOLD', 'SETUPHOLD',
               'RECOVERY', 'REMOVAL', 'RECREM', 'WIDTH', 'PERIOD')


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entries(text: str) -> dict:
    return {name: len(re.findall(r'\(' + name + r'\s', text)) for name in CHECK_TYPES}


def _triplet(value: str) -> list[Decimal]:
    pieces = value[1:-1].split(':')
    if len(pieces) != 3 or any(not part.strip() for part in pieces):
        raise ValueError('Expected a complete min:typ:max reset timing value')
    values = [Decimal(part.strip()) for part in pieces]
    if not all(value.is_finite() for value in values):
        raise ValueError('Non-finite reset timing value')
    return values


def _adapt(source: str) -> tuple[str, dict]:
    if source.count(VERSION) != 1 or '(RECREM ' in source:
        raise ValueError('Expected the original DC SDF 2.1 with separate reset checks')
    cells = list(re.finditer(r'(?m)^\(CELL\s*\n', source))
    if not cells:
        raise ValueError('No SDF cells found')
    changes = []
    pieces = [source[:cells[0].start()].replace(VERSION, NEW_VERSION)]
    counts = Counter()
    for index, cell in enumerate(cells):
        stop = cells[index + 1].start() if index + 1 < len(cells) else len(source)
        chunk = source[cell.start():stop]
        cell_type_match = re.search(r'\(CELLTYPE "([^"]+)"\)', chunk)
        instance_match = re.search(r'\(INSTANCE\s*([^\n()]*)\)', chunk)
        if not cell_type_match or not instance_match:
            raise ValueError('Malformed SDF cell header')
        cell_type = cell_type_match.group(1)
        if cell_type not in {'DFFR_X1', 'SDFFR_X1'}:
            pieces.append(chunk)
            continue
        matches = list(RESET_PAIR.finditer(chunk))
        if len(matches) != 1:
            raise ValueError('Expected one exact, unconditional reset pair in ' + instance_match.group(1))
        pair = matches[0]
        recovery = pair['recovery']
        removal = pair['removal']
        if any(a + b < 0 for a, b in zip(_triplet(recovery), _triplet(removal))):
            raise ValueError('Reset recovery/removal window has negative total width')
        combined = (pair['indent'] + '(RECREM ' + pair['reset'] + ' ' + pair['clock'] +
                    ' ' + recovery + ' ' + removal + ')')
        transformed = chunk[:pair.start()] + combined + chunk[pair.end():]
        # Reconstruct exactly to confirm that only the matched check pair changed.
        reconstructed = (transformed[:pair.start()] + pair.group(0) +
                         transformed[pair.start() + len(combined):])
        if reconstructed != chunk:
            raise ValueError('Unexpected change outside a reset check pair')
        pieces.append(transformed)
        counts[cell_type] += 1
        changes.append({'celltype': cell_type, 'instance': instance_match.group(1).strip(),
                        'reset_event': pair['reset'], 'clock_event': pair['clock'],
                        'recovery_triplet': recovery, 'removal_triplet': removal,
                        'original_sdf_checks': pair.group(0).strip(),
                        'simulation_sdf_check': combined.strip(),
                        'conditions': 'unconditional, verified before transformation'})
    output = ''.join(pieces)
    if dict(counts) != {'DFFR_X1': 238, 'SDFFR_X1': 58}:
        raise ValueError('Expected exactly 238 DFFR_X1 and 58 SDFFR_X1 reset pairs')
    before, after = _entries(source), _entries(output)
    expected = dict(before)
    expected['RECOVERY'] -= 296
    expected['HOLD'] -= 296
    expected['RECREM'] += 296
    if after != expected or after['RECOVERY'] or after['RECREM'] != 296:
        raise ValueError('Unexpected SDF entry count changes')
    for name in CHECK_TYPES:
        if name in {'RECOVERY', 'HOLD', 'RECREM'}:
            continue
        pattern = r'(?m)^.*\(' + name + r'\s.*$'
        if re.findall(pattern, source) != re.findall(pattern, output):
            raise ValueError('An unrelated SDF entry changed: ' + name)
    return output, {
        'source_sdfversion': 'OVI 2.1', 'simulation_sdfversion': '3.0',
        'reset_pairs_converted': len(changes), 'celltype_counts': dict(counts),
        'sdf_entries_before': before, 'sdf_entries_after': after,
        'value_triplets_preserved_exactly': True, 'other_cell_text_unchanged': True,
        'notes': ['SDF 2.1 reset HOLD is the removal constraint represented explicitly by RECREM.',
                  'The recovery value and removal value retain their original order and triplets.',
                  'All converted checks are unconditional; conditional pairs are rejected.',
                  'No check is suppressed and the source export is retained unchanged.'],
        'transformations': changes,
    }


def prepare_sdf(source: Path, target: Path) -> dict:
    """Create a simulation SDF and a detailed compatibility manifest."""
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError('The simulation SDF must be separate from the original export')
    source_bytes = source.read_bytes()
    output, record = _adapt(source_bytes.decode())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output)
    if source.read_bytes() != source_bytes:
        raise RuntimeError('The original SDF changed during compatibility preparation')
    manifest = target.with_suffix(target.suffix + '.compatibility.json')
    record.update({'source': str(source.resolve()), 'source_sha256': _sha(source_bytes),
                   'simulation_sdf': str(target.resolve()),
                   'simulation_sdf_sha256': _sha(target.read_bytes()),
                   'manifest': str(manifest.resolve())})
    manifest.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    record = prepare_sdf(args.source, args.target)
    print(json.dumps({key: value for key, value in record.items() if key != 'transformations'},
                     indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
