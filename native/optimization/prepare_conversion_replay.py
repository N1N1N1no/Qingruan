"""Copy existing converter sources/model into an isolated replay workspace."""
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
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
target = ROOT / ('work/conversion-replay-preparation-' + args.revision)
assert not target.exists(), 'Preserve earlier evidence'
package = target / 'conversion-snapshot'
package.mkdir(parents=True)
files = {Path(__file__), ROOT / 'conversion_replay_template.py'}
copied = {}
mapping = {}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy(path, name):
    path = path.resolve()
    destination = package / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(path.read_bytes())
    assert sha(destination) == sha(path)
    copied[name] = sha(destination)
    mapping[str(path)] = {'path': name, 'sha256': sha(path)}
    files.add(path)


model = BASE / 'competition/models/swin_tiny_patch4_window7_224_imagenet1k_int8.onnx'
suite_path = ROOT / 'work/vecpipe-image-suite-v1/summary.json'
suite = json.loads(suite_path.read_text())
assert suite['source_sha256'][str(model)] == sha(model)
files.add(suite_path)
copy(model, str(model.relative_to(BASE)))
for name in ['__init__.py', 'model.py', 'plan.py', 'compile.py']:
    path = BASE / 'native/swin-tiny/swin' / name
    copy(path, str(path.relative_to(BASE)))
for name in ['compile_luts.py', 'lower_views.py', 'lower_fused_plan.py', 'c/gelu_tables.c']:
    path = ROOT / name
    copy(path, str(path.relative_to(BASE)))
copy(ROOT / 'conversion_replay_template.py', 'replay_conversion.py')
artifact_names = [
    'native/swin-tiny/work/verify.ylnpqxth/constants.bin',
    'native/swin-tiny/work/verify.ylnpqxth/graph.bin',
    'native/swin-tiny/work/verify.ylnpqxth/graph_manifest.json',
    'native/optimization/work/view-plan/constants.bin',
    'native/optimization/work/view-plan/graph.bin',
    'native/optimization/work/view-plan/graph_manifest.json',
    'native/optimization/work/lookup-tables/gelu.bin',
    'native/optimization/work/lookup-tables/gelu-fusions.bin',
    'native/optimization/work/lookup-tables/gelu-fusions.json',
    'native/optimization/work/lookup-tables/quant-thresholds.bin',
    'native/optimization/work/lookup-tables/quant-thresholds.json',
    'native/optimization/work/fused-plan/constants.bin',
    'native/optimization/work/fused-plan/graph.bin',
    'native/optimization/work/fused-plan/gelu-fusions.bin',
    'native/optimization/work/fused-plan/graph_manifest.json']
artifacts = {}
for name in artifact_names:
    path = BASE / name
    artifacts[name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    files.add(path)
plan_paths = {'base': 'native/swin-tiny/work/verify.ylnpqxth/plan.json',
              'views': 'native/optimization/work/view-plan/plan.json',
              'fused': 'native/optimization/work/fused-plan/plan.json'}
for stage, name in plan_paths.items():
    copy(BASE / name, 'expected/' + stage + '-plan.json')
readme = package / 'README.md'
readme.write_text('''# 原始模型转换复现组件

此目录含当前本地ONNX及未改动的模型读取/计划/序列化、GELU与量化查表、视图及融合脚本，以及原有15项输出的哈希和3份预期计划。

在现有Python3.11、NumPy1.26.4、ONNX1.19.1、ONNX Runtime1.23.2及GCC环境运行python3 -B replay_conversion.py。该脚本只在本目录生成native/.../work和results，不会覆盖原工作区。每次复现需要新的目录副本。

脚本从现有ONNX重新生成常量、原图、视图图、融合图和GELU/量化查表，要求全部选定产物与原哈希一致，三个完整计划只允许ONNX路径变化。不会下载模型、调用推理仿真、修改预处理/标签/容差，也不验收官方精度、最终选型或最终交付。
''')
copied['README.md'] = sha(readme)
manifest = {'status': 'UNCHANGED_MODEL_CONVERSION_SOURCE_COMPONENT_PREPARED_EXECUTION_PENDING',
            'model_sha256': sha(model), 'files_sha256': copied, 'original_to_snapshot': mapping,
            'expected_artifacts': artifacts, 'plan_paths': plan_paths,
            'actual_RV32_inference_executed': False, 'official_accuracy_accepted': False,
            'final_delivery_accepted': False}
(package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
files.add(package / 'manifest.json')
(target / 'prepare_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'prepare_driver.py')
summary = {'status': manifest['status'], 'observed_at': datetime.now(timezone.utc).isoformat(),
           'package': str(package), 'package_files': len(copied),
           'package_bytes': sum((package / name).stat().st_size for name in copied),
           'expected_artifacts': len(artifacts), 'expected_full_plans': len(plan_paths),
           'source_sha256': {str(path): sha(path) for path in sorted(files)},
           'actual_RV32_inference_executed': False, 'official_accuracy_accepted': False,
           'final_delivery_accepted': False}
(target / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({key: summary[key] for key in ['status', 'package', 'package_files', 'package_bytes']}), flush=True)
