"""Run unchanged model conversion scripts inside this isolated workspace."""
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / 'manifest.json').read_text())
assert not (ROOT / 'results').exists() and not (ROOT / 'native/swin-tiny/work').exists(), 'Use a fresh copy'
results = ROOT / 'results'
results.mkdir()
resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify():
    for name, value in manifest['files_sha256'].items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT) and sha(path) == value, name


verify()
sys.path.insert(0, str(ROOT / 'native/swin-tiny'))
commands = []
started = time.monotonic()
base = ROOT / 'native/swin-tiny/work/verify.ylnpqxth'
base.mkdir(parents=True)
with (results / 'base-plan.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
    import numpy as np
    import onnx
    import onnxruntime as ort
    from swin.model import Model
    from swin.plan import build
    from swin.compile import emit
    model = Model(ROOT / 'competition')
    assert sha(model.path) == manifest['model_sha256']
    plan = build(model, base)
    emit(plan, base)
commands.append({'phase': 'base-plan', 'implementation': 'unchanged swin.model.Model+swin.plan.build+swin.compile.emit',
                 'returncode': 0, 'elapsed_s': time.monotonic() - started})
for label, name in [('tables', 'compile_luts.py'), ('views', 'lower_views.py'), ('fused', 'lower_fused_plan.py')]:
    argv = [sys.executable, str(ROOT / 'native/optimization' / name)]
    started = time.monotonic()
    with (results / (label + '.log')).open('w') as log:
        child = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=False)
    commands.append({'phase': label, 'argv': argv, 'returncode': child.returncode,
                     'elapsed_s': time.monotonic() - started})
    (results / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    assert child.returncode == 0, str(results / (label + '.log'))
artifacts = []
for name, expected in manifest['expected_artifacts'].items():
    path = ROOT / name
    assert path.stat().st_size == expected['bytes'] and sha(path) == expected['sha256'], name
    artifacts.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path)})
plans = []
for stage, name in manifest['plan_paths'].items():
    generated = json.loads((ROOT / name).read_text())
    expected = json.loads((ROOT / 'expected' / (stage + '-plan.json')).read_text())
    old_path = expected.pop('model')
    new_path = generated.pop('model')
    assert Path(new_path) == model.path and Path(old_path).name == model.path.name
    assert generated == expected, stage
    plans.append({'stage': stage, 'path': name, 'only_model_path_changed': True,
                  'model_sha256': generated['model_sha256'], 'nodes': len(generated['schedule'])})
verify()
summary = {'status': 'RELOCATED_ORIGINAL_ONNX_TO_BASE_VIEW_FUSED_GRAPHS_WEIGHTS_AND_TABLES_EXACT_PASS',
           'observed_at': datetime.now(timezone.utc).isoformat(), 'commands': commands,
           'manifest_sha256': sha(ROOT / 'manifest.json'), 'model_sha256': manifest['model_sha256'],
           'numpy_version': np.__version__, 'onnx_version': onnx.__version__, 'onnxruntime_version': ort.__version__,
           'artifacts': artifacts, 'plans': plans, 'unchanged_original_scripts_and_modules': True,
           'actual_RV32_inference_executed': False, 'official_accuracy_accepted': False,
           'final_delivery_accepted': False,
           'scope': 'Original existing ONNX converted in an isolated relocated source workspace by unchanged Model/plan/compiler and original table/view/fusion scripts. All selected15 artifacts are byte-hash exact; all three full plans equal after removing only the ONNX path. No changed preprocessing,labels,tolerance,hardware inference or final delivery acceptance.'}
(results / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({'status': summary['status'], 'artifacts': len(artifacts), 'plans': len(plans)}), flush=True)
