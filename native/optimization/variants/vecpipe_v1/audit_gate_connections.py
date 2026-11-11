"""Use the unchanged qualified physical wiring/unused-port auditors."""
import argparse
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from gate_connectivity_audit import audit_wires, audit_unused_outputs, sha

parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
parser.add_argument('--kind', choices=['wires', 'ports'], required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / 'work/vecpipe-full-soc-gate' / args.revision
output = case / ('wire-connectivity-review.json' if args.kind == 'wires' else 'unused-output-review.json')
assert not output.exists(), 'Preserve previous audit'
result = audit_wires(case) if args.kind == 'wires' else audit_unused_outputs(case)
if args.kind == 'wires':
    # All notices have already been individually proved through bit wiring.
    # Select the exact two physical endpoint pairs instrumented by the observer,
    # rather than assuming notice ordering is unchanged in the new netlist.
    endpoints = {
        'SDFCOM_SWC': ('u_backend/u_reorder/writer/U1511/ZN', 'u_backend/u_reorder/writer/sll_66/U320/A1'),
        'SDFCOM_IWSBA': ('u_backend/u_reorder/writer/sll_66/U224/ZN', 'u_backend/u_reorder/writer/U1268/A1')}
    for code, (source, destination) in endpoints.items():
        matches = [notice for notice in result['locations'] if notice['code'] == code
                   and notice['source_path'] == source.split('/') and notice['destination_path'] == destination.split('/')]
        assert len(matches) == 1, 'Actual observer endpoint must be a source-proved notice of the required class'
        result['representatives'][code] = matches[0]
    result['representative_selection_policy'] = 'Exact current SDF endpoint pairs present in the observer; every notice separately source-proved by unchanged qualified auditor.'
result['source_sha256'][str(Path(__file__))] = sha(Path(__file__))
output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: value for key, value in result.items()
                  if key not in ['locations', 'source_sha256', 'omitted_output_counts', 'representatives']}), flush=True)
