"""Compare the actual streaming SoC with the previously audited SoC boundary.

Use the established canonical SDC/hierarchy inspectors without executing the
old audit's main program. Every mapped artifact and library hash is checked.
This accepts only area/STA/common conditions, never inference or gate function.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
from gate_connectivity_audit import INST, DECL, name

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, default=ROOT / 'work/vecpipe-full-soc-timing-v1')
parser.add_argument('--revision', required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
source = args.source.resolve()
case = ROOT / ('work/vecpipe-soc-conditions-' + args.revision)
assert not case.exists(), 'Preserve previous audit'


def local(path):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if path.startswith(prefix):
            return BASE / path[len(prefix):]
    return Path(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


old_audit_path = ROOT / 'work/full-soc-conditions-v1/summary.json'
old = json.loads(old_audit_path.read_text())
assert old['status'] == 'THREE_COMPLETE_SOC_COMMON_BOUNDARY_LIBRARY_SDC_AND_STA_AUDIT_PASS'
for path, expected in old['source_sha256'].items():
    assert sha(local(path)) == expected, path
reference = old['candidates']['R8_C16_L4']
proof = json.loads((source / 'summary.json').read_text())
assert proof['status'] in ['VECPIPE_FULL_ALL8_SOC_CLOSED_TIMING_PASS', 'VECPIPE_FULL_ALL8_SOC_SDF_LOAD_REPAIR_PASS']
assert proof['clock_period_ns'] == 10 and proof['SRAMs'] == 128
assert min(proof['setup_slack_ns'], proof['hold_slack_ns']) >= 0
for path, expected in proof['source_sha256'].items():
    assert sha(local(path)) == expected, path
for path, expected in proof['artifact_sha256'].items():
    assert sha(source / path) == expected, path
template_path = ROOT / 'audit_full_soc_conditions.py'
tree = ast.parse(template_path.read_text())
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ['canonical_sdc', 'inspect_netlist']]
assert len(functions) == 2
namespace = {'re': re, 'Counter': Counter, 'INST': INST, 'DECL': DECL, 'name': name, 'MACRO': 'SRAM_6T_CORE_16x8_MC_TB'}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(template_path), 'exec'), namespace)
constraints = namespace['canonical_sdc'](source / 'constraints.sdc')
assert hashlib.sha256(('\n'.join(constraints) + '\n').encode()).hexdigest() == old['canonical_SDC_commands_sha256']
mapped = namespace['inspect_netlist'](source / 'mapped.v', 'soc_accel_top')
assert mapped['physical_SRAM_instances'] == 128 and mapped['top_ports'] == reference['top_ports']
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
libraries = {}
for filename in ['standard.db', 'sram.db']:
    path = PROJECT / 'build/dc_libraries' / filename
    libraries[filename] = sha(path)
    assert libraries[filename] == old['libraries_sha256'][filename]
    assert any(Path(name).name == filename and value == libraries[filename] for name, value in proof['source_sha256'].items())
assert 'This design has no violated constraints.' in (source / 'violations.rpt').read_text()
timing = (source / 'timing_check.rpt').read_text()
assert all('Checking ' + item in timing for item in ['loops', 'no_input_delay', 'unconstrained_endpoints', 'partial_input_delay'])
assert not re.search(r'(?im)^\s*(?:Warning|Error):', timing)
area = float(re.search(r'Total cell area:\s+([\d.]+)', (source / 'area.rpt').read_text())[1])
assert area == proof['area']
files = [Path(__file__), template_path, ROOT / 'gate_connectivity_audit.py', old_audit_path,
         source / 'summary.json', *[source / item for item in proof['artifact_sha256']],
         *[PROJECT / 'build/dc_libraries' / item for item in libraries]]
case.mkdir()
result = {'status': 'VECPIPE_COMPLETE_SOC_BOUNDARY_LIBRARY_SDC_PHYSICAL_SRAM_AND_STA_AUDIT_PASS',
          'source_case': str(source), 'clock_period_ns': 10, 'area': area,
          'setup_slack_ns': proof['setup_slack_ns'], 'hold_slack_ns': proof['hold_slack_ns'],
          'same_libraries_as_all_three_original_candidates': True, 'libraries_sha256': libraries,
          'same_complete_ordered_SDC_commands_except_four_independent_transitions': True,
          'same_original_baseline_top_port_directions_widths': True,
          'physical_SRAMs': mapped['physical_SRAM_instances'], 'top_ports': mapped['top_ports'],
          'report_constraint_has_no_violators': True, 'selected_check_timing_checks_without_warnings': True,
          'scalar_primary_closed_area': reference['area'], 'area_ratio_to_scalar_primary': area / reference['area'],
          'source_sha256': {str(path): sha(path) for path in files},
          'gate_SDF_function_accepted': False, 'full_inference_accepted': False,
          'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Complete CPU/router/MMIO/arbiter/eight engines/one array and128 SRAMs at the exact existing synthesis IO boundary, libraries and exported SDC. External RAM remains outside the common boundary. Mapped area and STA only; SDF numeric paths/annotation/gate function and complete-model correctness/performance remain separate gates.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'area', 'setup_slack_ns', 'hold_slack_ns', 'physical_SRAMs', 'area_ratio_to_scalar_primary']}), flush=True)
