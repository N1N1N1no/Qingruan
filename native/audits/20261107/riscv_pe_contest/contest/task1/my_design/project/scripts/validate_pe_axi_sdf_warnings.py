#!/usr/bin/env python3
"""Review the precise VCS continuous-assignment SDF notices in this AXI map.

VCS explicitly says these INTERCONNECT delays are still annotated. This
validator does not suppress diagnostics or change any design/SDF file. It
checks each notice against its source SDF line and the pure wire alias in
the mapped netlist, and returns log views without only those reviewed
blocks for the caller's separate general-diagnostic checks. The original
warning summaries remain unchanged. Full annotation-count and observed
delay validation are still required by the simulation runner.
"""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import re


TOP = 'sram_multiplier_system_ID_WIDTH3'
SCOPE = 'tb_pe_axi_sdf.dut'
EXPECTED_BIT_COUNTS = {0: 2, 1: 2, 2: 2, 3: 78, 4: 10, 5: 9, 6: 12, 7: 8, 8: 6}
NOTICE = re.compile(
    r'(?m)^Warning-\[SDFCOM_IWSBA\] INTERCONNECT will still be annotated\n'
    r'(?P<sdf_name>[^\n,]+), (?P<sdf_line>\d+)\n'
    r'module: (?P<module>\w+), "instance: (?P<scope>[^"\n]+)"\n'
    r'  SDF Warning: INTERCONNECT from\s+(?P<source>\w+) to '
    r'(?P<destination>\w+)\.(?P<pin>\w+) has Continuous[ \t]*\n'
    r'  Assignment at[ \t]*\n'
    r'  (?P<netlist>[^\n]+):(?P<netlist_line>\d+),[ \t]*\n'
    r'  delay will still be annotated\.(?=\n|\Z)')
NUMERIC = r'[-+0-9.eE]+'
TRIPLET = r'\((?:' + NUMERIC + r':){2}' + NUMERIC + r'\)'
INTERCONNECT = re.compile(
    r'\(INTERCONNECT s_axi_araddr\[(?P<bit>\d+)\] '
    r'(?P<destination>\w+)/(?P<pin>\w+) '
    r'(?P<rise>' + TRIPLET + r') (?P<fall>' + TRIPLET + r')\)')


def _state(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _triplet(value):
    values = [Decimal(text) for text in value[1:-1].split(':')]
    if len(values) != 3 or any(not number.is_finite() or number <= 0 for number in values):
        raise ValueError('Reviewed address interconnects must retain positive finite triplets')
    return values


def _notices(log):
    matches = list(NOTICE.finditer(log))
    starts = re.findall(r'(?m)^Warning-\[SDFCOM_IWSBA\]', log)
    if len(matches) != len(starts):
        raise ValueError('Unexpected IWSBA diagnostic wording or format')
    return matches


def validate_iwsba(compile_log, annotation_log, netlist_path, sdf_path):
    """Return reviewed log views, exact warning metadata and one delay target.

    The caller must separately check full SDF annotation counts and normal
    runtime diagnostics. This function never rewrites warning summaries.
    """
    netlist_path, sdf_path = Path(netlist_path).resolve(), Path(sdf_path).resolve()
    before = {path: _state(path) for path in (netlist_path, sdf_path)}
    compile_matches = _notices(compile_log)
    annotation_matches = _notices(annotation_log)
    expected_count = sum(EXPECTED_BIT_COUNTS.values())
    if (len(annotation_matches) != expected_count or len(compile_matches) != expected_count or
            Counter(match[0] for match in compile_matches) != Counter(match[0] for match in annotation_matches)):
        raise ValueError('Compile/annotation logs must contain the same 129 reviewed IWSBA notices')
    errors = re.findall(r'Total errors:\s*(\d+)', annotation_log)
    warnings = re.findall(r'Total warnings:\s*(\d+)', annotation_log)
    codes = re.findall(r'(?m)^\s*\[([^]]+)\][ \t]+(\d+)[ \t]*$', annotation_log)
    if errors != ['0'] or warnings != [str(expected_count)] or codes != [('SDFCOM_IWSBA', str(expected_count))]:
        raise ValueError('SDF annotation has errors or additional/unaccounted warnings')
    clean_annotation = NOTICE.sub('', annotation_log)
    if re.search(r'(?mi)^\s*(?:Warning|Error|Fatal)(?:-|:)', clean_annotation):
        raise ValueError('Unreviewed SDF annotation diagnostics remain')
    netlist = netlist_path.read_text()
    netlist_lines = netlist.splitlines()
    top = re.findall(r'(?ms)^module\s+' + TOP + r'\s*\(.*?;(.*?)^endmodule\b', netlist)
    if len(top) != 1:
        raise ValueError('Expected exactly one mapped AXI top definition')
    instances = {}
    for match in re.finditer(r'(?ms)^\s*(\w+)\s+(\\\S+|\w+)\s*\((.*?)\)\s*;', top[0]):
        reference, name, pins = match.groups()
        if name in instances:
            raise ValueError('Duplicate mapped top instance: ' + name)
        instances[name] = (reference, pins)
    wanted_lines = set()
    for notice in annotation_matches:
        if (notice['sdf_name'] != sdf_path.name or notice['module'] != TOP or
                notice['scope'] != SCOPE or notice['source'] != 's_axi_araddr' or
                Path(notice['netlist']).resolve() != netlist_path):
            raise ValueError('IWSBA notice is outside the reviewed top address aliases')
        wanted_lines.add(int(notice['sdf_line']))
    if len(wanted_lines) != expected_count:
        raise ValueError('Repeated SDF line in reviewed IWSBA notices')
    sdf_lines = {}
    celltype = instance = None
    with sdf_path.open() as stream:
        for number, line in enumerate(stream, 1):
            cell = re.fullmatch(r'\s*\(CELLTYPE "([^"]+)"\)\s*', line)
            inst = re.fullmatch(r'\s*\(INSTANCE\s*([^()]*)\)\s*', line)
            if cell:
                celltype = cell[1]
            if inst:
                instance = inst[1].strip()
            if number in wanted_lines:
                if celltype != TOP or instance != '':
                    raise ValueError('Reviewed interconnect must be in the top SDF cell')
                sdf_lines[number] = line.strip()
            if number >= max(wanted_lines):
                break
    if set(sdf_lines) != wanted_lines:
        raise ValueError('Missing reviewed interconnect lines in simulation SDF')
    metadata, destinations = [], set()
    bit_counts, aliases = Counter(), {}
    representative = None
    for notice in annotation_matches:
        netlist_line = int(notice['netlist_line'])
        if not 1 <= netlist_line <= len(netlist_lines):
            raise ValueError('IWSBA notice has an invalid netlist line')
        alias = re.fullmatch(r'\s*assign (\w+) = s_axi_araddr\[(\d+)\];', netlist_lines[netlist_line - 1])
        if alias is None:
            raise ValueError('Reviewed continuous assignment must be a pure address-bit wire alias')
        wire, bit_text = alias.groups()
        bit = int(bit_text)
        sdf_line = int(notice['sdf_line'])
        interconnect = INTERCONNECT.fullmatch(sdf_lines[sdf_line])
        destination, pin = notice['destination'], notice['pin']
        if (interconnect is None or int(interconnect['bit']) != bit or
                interconnect['destination'] != destination or interconnect['pin'] != pin):
            raise ValueError('IWSBA notice does not match its exact SDF source/destination')
        if destination not in instances:
            raise ValueError('Reviewed interconnect destination is absent from mapped top')
        reference, pins = instances[destination]
        pin_connections = re.findall(r'\.' + re.escape(pin) + r'\s*\(\s*([^()]*)\s*\)', pins)
        if pin_connections != [wire]:
            raise ValueError('Reviewed destination pin is not connected to its recorded address alias')
        endpoint = (destination, pin)
        if endpoint in destinations:
            raise ValueError('Repeated reviewed interconnect destination')
        destinations.add(endpoint)
        if bit in aliases and aliases[bit] != (wire, netlist_line):
            raise ValueError('Multiple aliases for one reviewed address bit')
        aliases[bit] = (wire, netlist_line)
        bit_counts[bit] += 1
        rise, fall = _triplet(interconnect['rise']), _triplet(interconnect['fall'])
        item = {'sdf_line': sdf_line, 'netlist_line': netlist_line,
                'source': 's_axi_araddr[' + str(bit) + ']', 'alias': wire,
                'destination_instance': destination, 'destination_pin': pin,
                'destination_celltype': reference,
                'rise_triplet_ns': [str(value) for value in rise],
                'fall_triplet_ns': [str(value) for value in fall],
                'tool_says_delay_still_annotated': True}
        metadata.append(item)
        if endpoint == ('U27776', 'S'):
            if bit != 3 or reference != 'MUX2_X1':
                raise ValueError('The observed-delay target no longer matches the reviewed map')
            representative = {'instance': destination, 'pin': pin, 'source': item['source'],
                              'alias': wire, 'rise_ns': str(rise[2]), 'fall_ns': str(fall[2]),
                              'sdf_line': sdf_line, 'selection': 'MAXIMUM'}
    if dict(bit_counts) != EXPECTED_BIT_COUNTS or representative is None:
        raise ValueError('Reviewed IWSBA coverage differs from the known mapped address aliases')
    for path, state in before.items():
        if _state(path) != state:
            raise RuntimeError('Mapped input changed during IWSBA validation: ' + str(path))
    return {'sanitized_compile_log': NOTICE.sub('', compile_log),
            'sanitized_annotation_log': clean_annotation,
            'reviewed_warning_count': expected_count,
            'annotation_error_count': 0, 'annotation_warning_count': expected_count,
            'reviewed_warning_code': 'SDFCOM_IWSBA',
            'source_bit_counts': dict(sorted(bit_counts.items())),
            'reviewed_warnings': metadata,
            'representative_delay': representative,
            'requires_full_annotation_summary_validation': True,
            'meaning': 'Only these exact pure-alias INTERCONNECT notices were reviewed; '
                       'the tool explicitly retains their delays. Original logs and warning '
                       'summaries are unchanged; full annotation and observed delay checks remain required.'}
