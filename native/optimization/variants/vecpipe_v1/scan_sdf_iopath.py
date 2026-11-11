"""Read-only, complete selected-MAXIMUM cell IOPATH audit.

No SDF values are edited. Negative setup/hold/recovery/removal timing limits
are outside this cell-delay audit and are never clamped or rejected here.
The source hash, all arcs and any negative target pins are preserved as proof.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import time

parser = argparse.ArgumentParser()
parser.add_argument('--input', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--require-nonnegative', action='store_true')
args = parser.parse_args()
source = args.input.resolve()
output = args.output.resolve()
assert source.is_file() and not output.exists(), 'Preserve input and prior audit'
assert source != output
output.parent.mkdir(parents=True, exist_ok=True)
before = source.stat()
number = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
values_pattern = re.compile(r'\(\s*(' + number + r'(?::' + number + r'){0,2})\s*\)')
cell_pattern = re.compile(r'\(CELLTYPE\s+"([^"]+)"\)')
instance_pattern = re.compile(r'\(INSTANCE\s*([^)]*)\)')
ports_pattern = re.compile(r'\(IOPATH\s+(\([^)]*\)|[^\s()]+)\s+([^\s()]+)\s+')
scale_pattern = re.compile(r'\(TIMESCALE\s+([^)]*)\)')
divider_pattern = re.compile(r'\(DIVIDER\s+([^)]*)\)')
celltype = instance = timescale = divider = None
cells = Counter()
arcs_by_type = Counter()
negative = []
iopaths = negative_any = lines = 0
digest = hashlib.sha256()
started = time.monotonic()
pending = None


def check_arc(text, start_line, celltype, instance):
    assert celltype is not None and instance is not None, (start_line, 'Missing cell context')
    assert text.count('(IOPATH ') == 1, (start_line, 'Expected one complete arc per expression')
    ports = ports_pattern.search(text)
    assert ports, (start_line, text)
    values = [item.split(':') for item in values_pattern.findall(text[ports.end():])]
    assert values and all(len(item) in [1, 3] for item in values), (start_line, text)
    any_negative = any(float(value) < 0 for item in values for value in item)
    selected_negative = any(float(item[-1]) < 0 for item in values)
    if selected_negative:
        negative.append({'line': start_line, 'celltype': celltype, 'instance': instance,
                         'input_port': ports[1], 'output_port': ports[2], 'iopath': text.strip()})
    return int(any_negative)


with source.open('rb') as stream:
    for lines, raw in enumerate(stream, 1):
        digest.update(raw)
        text = raw.decode('utf-8')
        if lines % 2000000 == 0:
            print('SDF_IOPATH_AUDIT lines=%d arcs=%d' % (lines, iopaths), flush=True)
        if pending is not None:
            pending['text'] += text
            pending['balance'] += text.count('(') - text.count(')')
            assert pending['balance'] >= 0, lines
            if pending['balance'] == 0:
                negative_any += check_arc(pending['text'], pending['line'], pending['celltype'], pending['instance'])
                iopaths += 1
                arcs_by_type[pending['celltype']] += 1
                pending = None
            continue
        match = cell_pattern.search(text)
        if match:
            celltype = match[1]
            instance = None
            cells[celltype] += 1
        match = instance_pattern.search(text)
        if match:
            instance = match[1].strip()
        match = scale_pattern.search(text)
        if match:
            assert timescale is None
            timescale = match[1].strip()
        match = divider_pattern.search(text)
        if match:
            assert divider is None
            divider = match[1].strip()
        if '(IOPATH ' not in text:
            continue
        # DC exports one arc expression per logical record. Multi-line arcs
        # are accumulated; an unexpected combined closing context is rejected.
        balance = text.count('(') - text.count(')')
        assert balance >= 0, (lines, text)
        if balance:
            pending = {'text': text, 'balance': balance, 'line': lines,
                       'celltype': celltype, 'instance': instance}
        else:
            negative_any += check_arc(text, lines, celltype, instance)
            iopaths += 1
            arcs_by_type[celltype] += 1
assert pending is None and iopaths > 0 and timescale is not None and divider == '/'
after = source.stat()
assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'Source changed during audit'
targets = sorted({(item['instance'], item['celltype'], item['output_port']) for item in negative})
driver = Path(__file__).resolve()
driver_copy = output.with_name(output.stem + '.driver.py')
assert not driver_copy.exists()
driver_copy.write_bytes(driver.read_bytes())
result = {'status': 'SELECTED_MAXIMUM_IOPATH_NONNEGATIVE' if not negative else 'NEGATIVE_MAXIMUM_IOPATH_REMAINS',
          'source': str(source), 'source_sha256': digest.hexdigest(), 'source_bytes': before.st_size,
          'driver_sha256': hashlib.sha256(driver.read_bytes()).hexdigest(), 'archived_driver': str(driver_copy),
          'timescale': timescale, 'divider': divider, 'lines_scanned': lines,
          'IOPATH_entries_scanned': iopaths, 'negative_MAXIMUM_IOPATH_entries': len(negative),
          'negative_IOPATH_entries_any_triplet_field': negative_any, 'negative_arcs': negative,
          'negative_output_targets': [{'instance': instance, 'celltype': cell, 'output_pin': pin}
                                      for instance, cell, pin in targets],
          'SDF_cell_histogram_including_top_and_wiring_scopes': dict(sorted(cells.items())),
          'IOPATH_count_by_celltype': dict(sorted(arcs_by_type.items())),
          'elapsed_s': time.monotonic() - started,
          'scope': 'Full read-only cell-delay MAXIMUM-field audit. Does not prove physical leaf identity, STA, annotation, timing-limit coverage, gate function, model accuracy or ADP. Negative timing-check limits are preserved and outside this audit.'}
output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'IOPATH_entries_scanned', 'negative_MAXIMUM_IOPATH_entries', 'elapsed_s']}), flush=True)
if args.require_nonnegative:
    assert not negative, 'Mapped selected cell delay is negative; no gate annotation acceptance'
