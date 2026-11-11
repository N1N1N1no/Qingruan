"""Audit complete mapped candidate boundaries, libraries and common SDC.

STA/area acceptance is deliberately separate from actual inference, SDF
function and organizer accuracy. No optimum is inferred from these areas.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from gate_connectivity_audit import INST, DECL, name

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
CASES = {'R8_C16_L4': 'v8', 'R8_C32_L4': 'v1', 'R16_C8_L4': 'v1'}
MACRO = 'SRAM_6T_CORE_16x8_MC_TB'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def canonical_sdc(path):
    commands = [' '.join(line.split()) for line in path.read_text().replace('\\\n', ' ').splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
    expected = {f'set_clock_transition -{corner} -{edge} 0.005 [get_clocks core_clk]'
                for corner in ['min', 'max'] for edge in ['rise', 'fall']}
    indices = [i for i, command in enumerate(commands) if command.startswith('set_clock_transition ')]
    assert len(indices) == 4 and indices == list(range(indices[0], indices[0] + 4))
    assert {commands[i] for i in indices} == expected
    commands[indices[0]:indices[-1] + 1] = sorted(expected)
    return commands


def inspect_netlist(path, top):
    text = path.read_text()
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
    modules = dict(re.findall(r'(?ms)^[ \t]*module[ \t]+(\w+)[ \t]*\(.*?;(.*?)^[ \t]*endmodule\b', text))
    assert top in modules
    references = {module: Counter(match[1] for match in INST.finditer(body)) for module, body in modules.items()}
    cache = {}

    def count(module, active):
        if module == MACRO:
            return 1
        if module not in modules:
            return 0
        if module in cache:
            return cache[module]
        assert module not in active, 'recursive mapped hierarchy'
        result = sum(multiplicity * count(ref, active | {module}) for ref, multiplicity in references[module].items())
        cache[module] = result
        return result

    ports = {}
    for direction, high, low, names in DECL.findall(modules[top]):
        if direction == 'wire':
            continue
        for signal in names.split(','):
            signal = name(signal.strip())
            assert signal not in ports
            ports[signal] = {'direction': direction, 'range': [int(high), int(low)] if high else None}
    return {'physical_SRAM_instances': count(top, set()), 'top_ports': ports,
            'mapped_module_definitions': len(modules)}


parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT / 'work/full-soc-conditions-v1/summary.json')
args = parser.parse_args()
assert not args.output.exists(), 'Preserve previous audit'
std, sram = [PROJECT / 'build/dc_libraries' / item for item in ['standard.db', 'sram.db']]
libraries = {path.name: sha(path) for path in [std, sram]}
records, frozen = {}, [Path(__file__), ROOT / 'gate_connectivity_audit.py', std, sram]
common_sdc = common_ports = None
for geometry, revision in CASES.items():
    case = ROOT / 'work/full-soc-timing' / geometry / 'shared' / revision
    proof = json.loads((case / 'summary.json').read_text())
    assert proof['status'] == 'FULL_ALL8_SOC_MAPPED_TIMING_PASS'
    assert proof['clock_period_ns'] == 10 and min(proof['setup_slack_ns'], proof['hold_slack_ns']) >= 0
    for item, expected in proof['artifact_sha256'].items():
        assert sha(case / item) == expected, str(case / item)
    for library, value in libraries.items():
        assert any(Path(item).name == library and expected == value for item, expected in proof['inputs_sha256'].items())
    constraint = canonical_sdc(case / 'constraints.sdc')
    mapped = inspect_netlist(case / 'mapped.v', 'soc_accel_top')
    rows, columns, _ = map(int, re.fullmatch(r'R(\d+)_C(\d+)_L(\d+)', geometry).groups())
    assert mapped['physical_SRAM_instances'] == proof['SRAMs'] == rows * columns
    if common_sdc is None:
        common_sdc, common_ports = constraint, mapped['top_ports']
    assert constraint == common_sdc and mapped['top_ports'] == common_ports
    violations = (case / 'violations.rpt').read_text()
    assert 'This design has no violated constraints.' in violations
    timing = (case / 'timing_check.rpt').read_text()
    assert all('Checking ' + check in timing for check in ['loops', 'no_input_delay', 'unconstrained_endpoints', 'partial_input_delay'])
    assert not re.search(r'(?im)^\s*(?:Warning|Error):', timing)
    records[geometry] = {'case': str(case), 'area': proof['area'],
                         'setup_slack_ns': proof['setup_slack_ns'], 'hold_slack_ns': proof['hold_slack_ns'],
                         **mapped, 'exported_SDC_common_commands_equal': True,
                         'report_constraint_has_no_violators': True,
                         'timing_check_selected_checks_have_no_warning': True,
                         'gate_SDF_function_accepted': False, 'official_accuracy_accepted': False}
    frozen.extend(case / item for item in ['summary.json', 'mapped.v', 'constraints.sdc', 'violations.rpt', 'timing_check.rpt'])
baseline = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/work/soc_sdf.u63n3r7w/inputs/soc_mapped.v'
baseline_map = inspect_netlist(baseline, 'soc_top')
assert baseline_map['physical_SRAM_instances'] == 256
assert baseline_map['top_ports'] == common_ports, 'complete SoC IO boundary differs from original baseline'
frozen.append(baseline)
result = {'status': 'THREE_COMPLETE_SOC_COMMON_BOUNDARY_LIBRARY_SDC_AND_STA_AUDIT_PASS',
          'clock_period_ns': 10, 'candidates': records, 'libraries_sha256': libraries,
          'canonical_SDC_commands_sha256': hashlib.sha256(('\n'.join(common_sdc) + '\n').encode()).hexdigest(),
          'SDC_comparison_rule': 'Only the four contiguous, independent min/max rise/fall transition0.005 attributes are order-canonicalized; every other ordered command/value is equal.',
          'original_baseline_top_port_directions_and_widths_equal': True,
          'original_baseline_physical_SRAMs': 256, 'source_sha256': {str(path): sha(path) for path in frozen},
          'full_inference_accepted': False, 'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'All three complete CPU/router/MMIO/arbiter/all8 mapped SoCs: source artifacts, identical library hashes, identical exported timing constraints, exact original-baseline IO boundary, independent hierarchical physical SRAM count and nonnegative STA with no reported constraint violators. External RAM remains outside this boundary. This does not establish original-baseline model latency, full inference success, complete SDF function, official accuracy or an ADP optimum.'}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'candidates': {g: {key: record[key] for key in ['area', 'setup_slack_ns', 'hold_slack_ns', 'physical_SRAM_instances']} for g, record in records.items()}}), flush=True)
