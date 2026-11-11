"""Rank completed control measurements while keeping final selection gated."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
parser.add_argument('--measurement-revision', default='v7')
args = parser.parse_args()
assert all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in vars(args).values())
target = ROOT / ('work/complete-three-controls-' + args.revision)
assert not target.exists(), 'Preserve earlier evidence'
files = set()


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    files.add(path)
    return json.loads(path.read_text())


def verify(hashes):
    for name, value in hashes.items():
        assert sha(local(name)) == value, name


measurement = read(ROOT / ('work/full-model-measurements-' + args.measurement_revision) / 'summary.json')
verify(measurement['source_sha256'])
assert measurement['official_accuracy_accepted'] is False and measurement['final_ADP_selection_accepted'] is False
proofs = {'R8_C16_L4': 'v1', 'R8_C32_L4': 'v3', 'R16_C8_L4': 'v1'}
assert {row['geometry'] for row in measurement['candidates']} == set(proofs)
rows = []
control = next(row for row in measurement['candidates'] if row['geometry'] == 'R8_C16_L4')
for row in measurement['candidates']:
    assert row['complete_model_measurement'] and row['inference_ns'] == row['inference_cycles'] * 10
    proof = read(ROOT / 'work/completed-full-candidate' / row['geometry'] / proofs[row['geometry']] / 'summary.json')
    assert proof['status'] == 'COMPLETED_REAL_RV32_CANDIDATE_FROZEN_C_BYTE_EXACT_COMMON_PPA_REVIEW_PASS'
    verify(proof['source_sha256'])
    assert proof['simulator'] == 'vcs' and proof['full_model_executed'] and proof['clock_period_ns'] == 10
    assert proof['supplemental_current_actual_dependency_files_checked'] == 5
    assert row['inference_cycles'] == proof['inference_cycles'] and row['area'] == proof['complete_SoC_area']
    assert row['logits']['sha256'] == control['logits']['sha256'] == proof['logits']['sha256']
    assert row['logits']['byte_exact_to_frozen_C'] and row['setup_slack_ns'] >= 0 and row['hold_slack_ns'] >= 0
    ratio = row['area_times_full_inference_ns'] / control['area_times_full_inference_ns']
    assert abs(ratio - proof['comparison_to_completed_R8_C16_control']['ADP_ratio']) < 1e-12
    rows.append({'geometry': row['geometry'], 'inference_cycles': row['inference_cycles'],
                 'inference_seconds_at_10ns': row['inference_ns'] * 1e-9,
                 'complete_SoC_area': row['area'], 'area_times_inference_ns': row['area_times_full_inference_ns'],
                 'ADP_ratio_to_R8_C16_control': ratio,
                 'latency_ratio_to_R8_C16_control': row['inference_cycles'] / control['inference_cycles'],
                 'useful_MAC_utilization_during_GEMM_commands': row['useful_MAC_utilization_during_GEMM_commands'],
                 'useful_MAC_utilization_over_full_inference': row['useful_MAC_utilization_over_full_inference'],
                 'all8_engine_counters': row['engine_counters'], 'logits_byte_exact': True,
                 'full_independent_Verilator_cycle_equality': proof['VCS_full_model_crosscheck']['complete_cycle_equality_accepted']})
rows.sort(key=lambda row: row['area_times_inference_ns'])
assert rows[0]['geometry'] == 'R8_C16_L4'
target.mkdir()
(target / 'review_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'review_driver.py')
result = {'status': 'THREE_COMPLETE_SINGLE_IMAGE_CONTROL_MEASUREMENTS_FINAL_SELECTION_OPEN',
          'observed_at': datetime.now(timezone.utc).isoformat(), 'image': measurement['image'],
          'ranked_controls': rows, 'engineering_minimum_single_image_ADP': rows[0]['geometry'],
          'ranking_scope': 'Only these three completed scalar-vector controls,one same image,common reviewed full-SoC library/SDC/IO/memory conditions.',
          'final_selected_candidate': None, 'official_accuracy_accepted': False,
          'original_baseline_complete': measurement['original_baseline']['complete_model_measurement'],
          'new_vector_pipeline_complete': False, 'full20_images_accepted': False,
          'final_ADP_selection_accepted': False,
          'source_sha256': {str(path): sha(path) for path in sorted(files)},
          'scope': 'All three controls complete real-RV32+all8 VCS inference and1000 frozen-C byte equality. Rank measured area*complete time within this engineering set. New pipeline,original baseline,20 images,organizer accuracy,required gate and final delivery remain separate; no final optimum or original before-after result claimed.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'ranked_controls': [(row['geometry'], row['inference_cycles'],
      row['ADP_ratio_to_R8_C16_control']) for row in rows]}), flush=True)
