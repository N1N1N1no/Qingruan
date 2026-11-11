"""Compile a relocated RTL snapshot; no model execution or accuracy acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['verify', 'compile'])
parser.add_argument('--output', default='runs/compile-v1')
args = parser.parse_args()
manifest = json.loads((ROOT / 'manifest.json').read_text())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify():
    for name, value in manifest['files_sha256'].items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT) and sha(path) == value, name


verify()
if args.action == 'verify':
    print(json.dumps({'status': 'RELOCATED_RTL_SNAPSHOT_HASHES_PASS',
                      'files': len(manifest['files_sha256']),
                      'inference_executed': False}), flush=True)
    raise SystemExit(0)
output = (ROOT / args.output).resolve()
assert output.is_relative_to(ROOT / 'runs') and not output.exists(), 'Use a new runs/ output directory'
output.mkdir(parents=True)
config = json.loads((ROOT / 'compile-tokens.json').read_text())


def expand(value):
    return value.replace('@ROOT@', str(ROOT)).replace('@OUT@', str(output))


cpu_filelist = output / 'cpu.f'
cpu_filelist.write_text('\n'.join(expand(token) for token in config['cpu_filelist']) + '\n')
commands = []
for label in ['compat', 'compile']:
    argv = [expand(value) for value in config[label]]
    if label == 'compile':
        assert argv[0] == '@PYTHON@'
        argv[0] = sys.executable
    started = time.monotonic()
    with (output / (label + '.log')).open('w') as stream:
        child = subprocess.run(argv, cwd=output, stdout=stream, stderr=subprocess.STDOUT,
                               timeout=1800, check=False)
    commands.append({'phase': label, 'argv': argv, 'returncode': child.returncode,
                     'elapsed_s': time.monotonic() - started})
    (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    assert child.returncode == 0, str(output / (label + '.log'))
verify()
result = {'status': 'RELOCATED_RTL_SOURCE_COMPILE_PASS_NO_INFERENCE_ACCEPTANCE',
          'manifest_sha256': sha(ROOT / 'manifest.json'), 'commands': commands,
          'compiled_executable_sha256': sha(output / 'simv'),
          'actual_RV32_inference_executed': False, 'official_accuracy_accepted': False,
          'final_delivery_accepted': False,
          'scope': 'Compile and elaborate the same CPU,all8 accelerators,full-model monitor and original defines from copied sources/headers with relocated paths. No firmware execution or full-model/gate/accuracy acceptance.'}
(output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'output': str(output)}), flush=True)
