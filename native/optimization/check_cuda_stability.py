"""Test repeatability of the supplied ONNX without tuning its reference labels.

Two independent sessions and three repeats use the existing documented CUDA
configuration. This is reference diagnosis, never RV32/accelerator validation.
"""
import argparse
import csv
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
sys.path.insert(0, str(ROOT / 'gpu-runtime'))
import numpy as np
import onnxruntime as ort
from PIL import __version__ as pillow_version

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT / 'work/cuda-stability-v1')
args = parser.parse_args()
case = args.output.resolve()
assert not case.exists(), 'Preserve existing diagnostic; choose a new output path'
competition = BASE / 'competition'
model = competition / 'models/swin_tiny_patch4_window7_224_imagenet1k_int8.onnx'
preprocess = competition / 'official_preprocess.py'
prior_dir = ROOT / 'work/cuda-reference'
prior = json.loads((prior_dir / 'summary.json').read_text())
assert ort.__version__ == prior['ort_version'] == '1.23.2'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert sha(model) == prior['model_sha256']
assert sha(preprocess) == prior['preprocess_sha256']
ort.preload_dlls(directory='')
spec = importlib.util.spec_from_file_location('unchanged_preprocess', preprocess)
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)
with (competition / 'reference_top1_5.csv').open(encoding='utf-8-sig') as stream:
    references = list(csv.DictReader(stream))
assert len(references) == 5
case.mkdir(parents=True)
inputs, old_outputs, frozen_inputs, files = {}, {}, {}, [
    Path(__file__), model, preprocess, competition / 'reference_top1_5.csv',
    competition / 'reference_top1_5.json', prior_dir / 'summary.json']
cpu_inputs = BASE / 'native/swin-tiny/work/reference.p75zvgn2'
for row in references:
    image = row['image']
    path = competition / 'images' / image
    tensor = pre.prepare_classification_input('swin_tiny_patch4_window7_224', path, False)
    assert tensor.dtype == np.float32 and tensor.shape == (1, 3, 224, 224)
    previous_input = cpu_inputs / (image + '.input.npy')
    assert tensor.tobytes() == np.load(previous_input).tobytes(), image
    inputs[image] = tensor
    frozen_inputs[image] = hashlib.sha256(tensor.tobytes()).hexdigest()
    old_path = prior_dir / (image + '.logits.npy')
    old_outputs[image] = np.load(old_path)
    np.save(case / (image + '.input.npy'), tensor)
    files.extend([path, previous_input, old_path])
hashes = {str(path): sha(path) for path in files}
(case / 'preparation.json').write_text(json.dumps({
    'status': 'REFERENCE_REPEATABILITY_DIAGNOSTIC_PREPARED_NOT_EXECUTED',
    'source_sha256': hashes, 'preprocessed_input_sha256': frozen_inputs,
    'input_matches_prior_CPU_reference_bytes': True,
    'independent_sessions': 2, 'repeats_per_session': 3,
    'scope': 'Unmodified ONNX/CUDA repeatability only; not RTL inference or official accuracy acceptance.'
}, indent=2) + '\n')
results, sessions = [], []
first_outputs = {}
for index in range(2):
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.enable_profiling = True
    options.profile_file_prefix = str(case / ('session%d-profile' % index))
    print('CUDA_STABILITY_SESSION_START', index, flush=True)
    start = time.monotonic()
    session = ort.InferenceSession(str(model), options, providers=[
        ('CUDAExecutionProvider', {'use_tf32': '0'}), 'CPUExecutionProvider'])
    assert 'CUDAExecutionProvider' in session.get_providers(), 'Refuse CPU-only fallback'
    session_meta = {'session': index, 'providers': session.get_providers(),
                    'provider_options': session.get_provider_options(),
                    'initialization_elapsed_s': time.monotonic() - start}
    assert session_meta['provider_options']['CUDAExecutionProvider']['use_tf32'] == '0'
    for repeat in range(3):
        for row in references:
            image = row['image']
            start = time.monotonic()
            logits = session.run(None, {'input': inputs[image]})[0]
            assert logits.dtype == np.float32 and logits.shape == (1, 1000)
            assert np.isfinite(logits).all()
            assert hashlib.sha256(inputs[image].tobytes()).hexdigest() == frozen_inputs[image]
            if image not in first_outputs:
                first_outputs[image] = logits.copy()
            target = case / ('session%d-repeat%d-' % (index, repeat) + image + '.logits.npy')
            np.save(target, logits)
            predicted, expected = int(logits.argmax()), int(row['Swin-Tiny'])
            result = {'session': index, 'repeat': repeat, 'image': image,
                      'Top1': predicted, 'official_Top1': expected,
                      'official_Top1_matches': predicted == expected,
                      'logits_raw_sha256': hashlib.sha256(logits.tobytes()).hexdigest(),
                      'matches_first_run_bytes': logits.tobytes() == first_outputs[image].tobytes(),
                      'matches_prior_CUDA_baseline_bytes': logits.tobytes() == old_outputs[image].tobytes(),
                      'max_abs_error_vs_first_run': float(np.max(np.abs(logits - first_outputs[image]))),
                      'max_abs_error_vs_prior_CUDA_baseline': float(np.max(np.abs(logits - old_outputs[image]))),
                      'output_file': str(target), 'elapsed_s': time.monotonic() - start}
            results.append(result)
            print('CUDA_STABILITY_SAMPLE', json.dumps(result), flush=True)
    profile = Path(session.end_profiling())
    counts = {}
    for event in json.loads(profile.read_text()):
        provider = event.get('args', {}).get('provider')
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
    assert counts.get('CUDAExecutionProvider', 0) > 0, 'No actual CUDA kernel events'
    session_meta.update(profile=str(profile), profile_sha256=sha(profile),
                        kernel_events_by_provider=counts)
    sessions.append(session_meta)
    del session
    gc.collect()

for name, wanted in hashes.items():
    assert sha(Path(name)) == wanted, name
assert len(results) == 30
stable = all(row['matches_first_run_bytes'] for row in results)
consistent = all(row['matches_prior_CUDA_baseline_bytes'] for row in results)
result = {
    'status': 'LOCAL_CUDA_REPEATABILITY_STABLE_OFFICIAL_ACCURACY_OPEN' if stable
              else 'LOCAL_CUDA_RUN_VARIATION_OBSERVED_OFFICIAL_ACCURACY_OPEN',
    'sessions': sessions, 'results': results,
    'same_outputs_across_sessions_and_repeats': stable,
    'all_outputs_match_previous_CUDA_baseline': consistent,
    'preprocessed_input_sha256': frozen_inputs,
    'source_sha256': hashes, 'ort_version': ort.__version__,
    'numpy_version': np.__version__, 'pillow_version': pillow_version,
    'GPU': subprocess.check_output(['nvidia-smi', '--query-gpu=name,compute_cap',
                                    '--format=csv,noheader'], text=True).strip(),
    'configuration': {'TF32': False, 'graph_optimization': 'ORT_DISABLE_ALL',
                      'execution': 'ORT_SEQUENTIAL', 'intra_op_threads': 1,
                      'inter_op_threads': 1},
    'official_accuracy_accepted': False,
    'scope': 'Actual original ONNX inference repeated on the local CUDA GPU with '
             'unchanged documented settings, exact prior preprocessed input '
             'bytes and raw-logit comparisons. No model/preprocess/label '
             'tuning, remote downloads, RV32/RTL proof, A100 reproduction, '
             'tolerance changes or official accuracy acceptance.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print('CUDA_STABILITY_COMPLETE', result['status'], flush=True)
