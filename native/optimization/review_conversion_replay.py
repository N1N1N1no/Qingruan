"""Review isolated original-model conversion and link its data to rebuilt FW."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
parser.add_argument('--package', default='conversion-replay-relocation-v1')
args = parser.parse_args()
assert all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in vars(args).values())
target = ROOT / ('work/conversion-replay-review-' + args.revision)
assert not target.exists(), 'Preserve earlier evidence'
package = ROOT / 'work' / args.package
files = set()


def local(name):
    if name.startswith('/workspace/'):
        return BASE / name[11:]
    if name.startswith('/home/zhz/'):
        return BASE / 'native/rootfs' / name[1:]
    return Path(name)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    files.add(path)
    return json.loads(path.read_text())


def verify(hashes):
    for name, value in hashes.items():
        assert sha(local(name)) == value, name


prepared = read(ROOT / 'work/conversion-replay-preparation-v1/summary.json')
verify(prepared['source_sha256'])
manifest = read(package / 'manifest.json')
for name, value in manifest['files_sha256'].items():
    path = (package / name).resolve()
    assert path.is_relative_to(package) and sha(path) == value, name
    files.add(path)
for name, value in manifest['original_to_snapshot'].items():
    assert sha(local(name)) == value['sha256'] == sha(package / value['path'])
summary = read(package / 'results/summary.json')
assert summary['status'] == 'RELOCATED_ORIGINAL_ONNX_TO_BASE_VIEW_FUSED_GRAPHS_WEIGHTS_AND_TABLES_EXACT_PASS'
assert summary['manifest_sha256'] == sha(package / 'manifest.json')
assert summary['model_sha256'] == manifest['model_sha256']
assert len(summary['artifacts']) == len(manifest['expected_artifacts']) == 15
assert len(summary['plans']) == len(manifest['plan_paths']) == 3
assert [command['phase'] for command in summary['commands']] == ['base-plan', 'tables', 'views', 'fused']
assert all(command['returncode'] == 0 for command in summary['commands'])
for command, script in zip(summary['commands'][1:], ['compile_luts.py', 'lower_views.py', 'lower_fused_plan.py']):
    assert len(command['argv']) == 2
    assert local(command['argv'][1]) == package / 'native/optimization' / script
for artifact in summary['artifacts']:
    name = artifact['path']
    assert name in manifest['expected_artifacts']
    original = BASE / name
    rebuilt = package / name
    assert sha(original) == sha(rebuilt) == artifact['sha256'] == manifest['expected_artifacts'][name]['sha256']
    assert original.stat().st_size == rebuilt.stat().st_size == artifact['bytes']
    files.update([original, rebuilt])
for stage, name in manifest['plan_paths'].items():
    original = read(BASE / name)
    rebuilt = read(package / name)
    assert original.pop('model').endswith('/competition/models/swin_tiny_patch4_window7_224_imagenet1k_int8.onnx')
    assert local(rebuilt.pop('model')) == package / 'competition/models/swin_tiny_patch4_window7_224_imagenet1k_int8.onnx'
    assert original == rebuilt and len(rebuilt['schedule']) == 1396, stage
    assert rebuilt['model_sha256'] == manifest['model_sha256']
firmware_review_path = ROOT / 'work/inference-replay-review-v1/summary.json'
firmware_review = read(firmware_review_path)
assert firmware_review['status'] == 'RELOCATED_FIRMWARE_ALL_ELF_LOAD_STATE_AND20_INPUT_RAM_REPRODUCTION_REVIEW_PASS'
verify(firmware_review['source_sha256'])
inference = local(firmware_review['package'])
links = []
for relative, converted in [
        ('graph.bin', 'fused-plan/graph.bin'),
        ('constants.bin', 'fused-plan/constants.bin'),
        ('gelu-fusions.bin', 'fused-plan/gelu-fusions.bin'),
        ('gelu.bin', 'lookup-tables/gelu.bin'),
        ('quant-thresholds.bin', 'lookup-tables/quant-thresholds.bin')]:
    payload = inference / 'model-data' / relative
    generated = package / 'native/optimization/work' / converted
    assert payload.read_bytes() == generated.read_bytes()
    files.update([payload, generated])
    links.append({'consumer_data': str(payload), 'reproduced_conversion': str(generated), 'sha256': sha(payload)})
graph = (inference / 'model-data/graph.bin').read_bytes()
header = struct.unpack_from('<16I', graph)
assert header[:2] == (0x4e495753, 1) and header[3:5] == (1396, 77) and header[14] == 1
files.update(package / 'results' / name for name in ['base-plan.log', 'tables.log', 'views.log', 'fused.log', 'commands.json'])
target.mkdir()
(target / 'review_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'review_driver.py')
result = {
    'status': 'ORIGINAL_ONNX_CONVERSION_REBUILT_FIRMWARE_AND20_RAM_DATA_CHAIN_REVIEW_PASS',
    'observed_at': datetime.now(timezone.utc).isoformat(), 'package': str(package),
    'original_model_sha256': manifest['model_sha256'], 'unchanged_original_conversion_sources': True,
    'byte_exact_artifacts': len(summary['artifacts']), 'full_plans_equal_except_model_path': 3,
    'consumer_payload_links': links, 'graph_nodes': 1396, 'MAC_jobs': 77,
    'source_to_converted_model_data_reproduced': True, 'firmware_rebuild_and20_input_RAM_review': str(firmware_review_path),
    'actual_RV32_inference_executed': False, 'official_accuracy_accepted': False,
    'final_delivery_accepted': False,
    'source_sha256': {str(path): sha(path) for path in sorted(files)},
    'scope': 'Existing original ONNX and unchanged model/table/view/fusion sources reproduce15 artifacts and3 complete plans. All5 payloads consumed by the relocated frozen firmware/RAM component are linked byte-exactly. This proves the conversion/rebuild/data reproduction chain,not model hardware inference,organizer accuracy,PPA or final submission.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'byte_exact_artifacts',
      'full_plans_equal_except_model_path', 'source_to_converted_model_data_reproduced']}), flush=True)
