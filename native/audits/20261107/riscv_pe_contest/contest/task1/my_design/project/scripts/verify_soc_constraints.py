"""Verify actual DC port reports and preserve all exported Verilog tokens."""
from decimal import Decimal
import hashlib
from pathlib import Path
import re
import subprocess


VERILOG_TOKENS = re.compile(
    r'(?P<ignore>\s+|//[^\n]*|/\*[\s\S]*?\*/)|'
    r'\\[^\s]+|"(?:\\.|[^"\\])*"|[A-Za-z_$][A-Za-z0-9_$]*|'
    r"\d+(?:'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+)?|'[01xXzZ]|"
    r'===|!==|<<<|>>>|==|!=|<=|>=|&&|\|\||<<|>>|\*\*|\+\+|--|.'
)


def verilog_token_hash(path):
    """Hash lexical tokens, keeping escaped identifiers and strings intact."""
    text = Path(path).read_text()
    digest = hashlib.sha256()
    count = 0
    for match in VERILOG_TOKENS.finditer(text):
        if match.lastgroup == 'ignore':
            continue
        digest.update(match.group().encode())
        digest.update(b'\0')
        count += 1
    return {'sha256': digest.hexdigest(), 'tokens': count}


def table(text, marker):
    match = re.search(marker, text)
    if match is None:
        raise RuntimeError('Missing port report section: ' + marker)
    lines = text[match.end():].splitlines()
    divider = next(i for i, line in enumerate(lines) if re.fullmatch(r'-+', line.strip()))
    result = {}
    for line in lines[divider+1:]:
        if not line.strip() or re.fullmatch(r'-+', line.strip()):
            break
        fields = line.split()
        if not fields or fields[0] in result:
            raise RuntimeError('Malformed or duplicate port report row')
        result[fields[0]] = fields[1:]
    return result


def decimal_values(values):
    return tuple(Decimal(value) for value in values)


def verify_port_report(path, original_ports):
    text = Path(path).read_text()
    inputs = {name for name, direction in original_ports if direction == 'in'}
    outputs = {name for name, direction in original_ports if direction == 'out'}
    if (len(inputs), len(outputs)) != (154, 224):
        raise RuntimeError('Unexpected original SoC bit interface')
    data = inputs - {'clk', 'rst_n'}
    loads = table(text, r'\bPort\s+Dir\b')
    # Name column padding varies with the longest bit port name.
    if set(loads) != inputs | outputs:
        raise RuntimeError('Port loads table is incomplete')
    for name, fields in loads.items():
        direction = 'in' if name in inputs else 'out'
        if fields[0] != direction or decimal_values(fields[1:3]) != (
                Decimal(0 if direction == 'in' else 5), Decimal(0)):
            raise RuntimeError('Unexpected pin/wire load: ' + name)
    incoming = table(text, 'Input Delay')
    outgoing = table(text, 'Output Delay')
    slews = table(text, r'Max Tran\s+Min Tran')
    if set(incoming) != inputs or set(outgoing) != outputs or set(slews) != inputs:
        raise RuntimeError('Delay/transition report misses ports or has extra rows')
    for name in data:
        fields = incoming[name]
        if decimal_values(fields[:4]) != (Decimal(0), Decimal(0), Decimal(1), Decimal(1)) or fields[4] != 'core_clk':
            raise RuntimeError('Incorrect input delay/edge: ' + name)
    fields = incoming['rst_n']
    if decimal_values(fields[:4]) != (Decimal(0),) * 4 or fields[4] != 'core_clk(f)':
        raise RuntimeError('Reset must reference the clock falling edge')
    if incoming['clk'][:5] != ['--'] * 5 or slews['clk'] != ['--'] * 4:
        raise RuntimeError('The physical clock unexpectedly has a data I/O constraint')
    for name in data | {'rst_n'}:
        if decimal_values(slews[name]) != (Decimal('0.02'),) * 4:
            raise RuntimeError('Incorrect min/max rise/fall input transition: ' + name)
    for name in outputs:
        fields = outgoing[name]
        if decimal_values(fields[:4]) != (Decimal(0), Decimal(0), Decimal(1), Decimal(1)) or fields[4] != 'core_clk':
            raise RuntimeError('Incorrect output delay/edge: ' + name)
    return {'status': 'PASS', 'data_input_bits': len(data), 'reset_bits': 1,
            'output_bits': len(outputs), 'input_min_ns': 0, 'input_max_ns': 1,
            'reset_clock_edge': 'falling', 'input_transition_ns': 0.02,
            'output_min_ns': 0, 'output_max_ns': 1, 'output_pin_load_ff': 5,
            'actual_dc_port_report_checked': True}


def verify_saved_clock_sdc(sdc_path, output_dir):
    """Interpret the generated SDC in Tcl, with only expected commands mocked.

    DC's clock report rounds 0.005 ns to two decimals. The saved SDC provides
    the actual min/max rise/fall values, independent of the source assumptions.
    """
    script = Path(output_dir) / 'verify_saved_sdc.tcl'
    body = r'''
proc get_ports {args} {return [lindex $args end]}
proc get_clocks {args} {return [lindex $args end]}
foreach name {set_units create_clock set_clock_uncertainty set_clock_transition} {
    proc $name {args} [format {puts [join [linsert $args 0 %s] "\t"]} $name]
}
foreach name {set_load set_input_delay set_input_transition set_output_delay} {
    proc $name {args} {return}
}
source [lindex $argv 0]
'''
    script.write_text(body)
    process = subprocess.run(['tclsh', str(script), str(sdc_path)],
                             stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=15, check=True)
    if process.stderr.strip():
        raise RuntimeError('Unexpected saved SDC verification diagnostic')
    (Path(output_dir) / 'saved_sdc_clock_values.tsv').write_text(process.stdout)
    entries = [line.split('\t') for line in process.stdout.splitlines()]
    grouped = {}
    for name, *args in entries:
        grouped.setdefault(name, []).append(args)
    if {name: len(rows) for name, rows in grouped.items()} != {
            'set_units': 1, 'create_clock': 1,
            'set_clock_uncertainty': 1, 'set_clock_transition': 4}:
        raise RuntimeError('Unexpected clock/unit commands in actual saved SDC')
    units = grouped['set_units'][0]
    units = dict(zip(units[::2], units[1::2]))
    if units.get('-time') != 'ns' or units.get('-capacitance') != 'fF':
        raise RuntimeError('Unexpected actual SDC units')
    clock = grouped['create_clock'][0]
    if clock != ['clk', '-name', 'core_clk', '-period', '10', '-waveform', '0 5']:
        raise RuntimeError('Unexpected actual SDC core clock: ' + str(clock))
    uncertainty = grouped['set_clock_uncertainty'][0]
    if uncertainty != ['0.1', 'core_clk']:
        raise RuntimeError('Unexpected actual SDC clock uncertainty')
    transitions = set()
    for row in grouped['set_clock_transition']:
        if len(row) != 4 or row[3] != 'core_clk' or Decimal(row[2]) != Decimal('0.005'):
            raise RuntimeError('Unexpected actual SDC clock transition')
        transitions.add(tuple(row[:2]))
    if transitions != {('-min', '-rise'), ('-min', '-fall'), ('-max', '-rise'), ('-max', '-fall')}:
        raise RuntimeError('Missing actual SDC min/max rise/fall clock transition')
    return {'status': 'PASS', 'clock_name': 'core_clk', 'period_ns': 10,
            'waveform_ns': [0, 5], 'uncertainty_ns': 0.1,
            'all_clock_transitions_ns': 0.005, 'units': units}
