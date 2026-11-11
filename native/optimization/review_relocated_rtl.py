"""Verify a relocated VCS compilation consumed only the copied RTL closure."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
parser.add_argument('--dependency-revision', default='v1')
parser.add_argument('--package', default='rtl-replay-relocation-v1')
args = parser.parse_args()
assert all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in vars(args).values())
target = ROOT / ('work/rtl-replay-relocation-review-' + args.revision)
assert not target.exists(), 'Preserve previous evidence'
package = ROOT / 'work' / args.package
output = package / 'runs/compile-v1'
primary = ROOT / 'work/vecpipe-full-rv32-v1'
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


closure_path = ROOT / ('work/compile-dependency-closure-' + args.dependency_revision) / 'summary.json'
closure = read(closure_path)
assert closure['status'] == 'ACTUAL_VCS_DEPENDENCY_CURRENT_BYTE_IDENTITY_AND_RTL_SNAPSHOT_AUDIT_PASS'
verify(closure['source_sha256'])
manifest = read(package / 'manifest.json')
assert manifest['status'] == 'CANDIDATE_RTL_COMPILE_SNAPSHOT_PREPARED_NO_INFERENCE_ACCEPTANCE'
for name, value in manifest['files_sha256'].items():
    path = (package / name).resolve()
    assert path.is_relative_to(package) and sha(path) == value, name
    files.add(path)
for name, value in manifest['original_to_snapshot'].items():
    assert sha(local(name)) == value['sha256']
    assert sha(package / value['exported']) == value['sha256']
compile_summary = read(output / 'summary.json')
assert compile_summary['status'] == 'RELOCATED_RTL_SOURCE_COMPILE_PASS_NO_INFERENCE_ACCEPTANCE'
assert compile_summary['manifest_sha256'] == sha(package / 'manifest.json')
assert compile_summary['compiled_executable_sha256'] == sha(output / 'simv')
assert [item['phase'] for item in compile_summary['commands']] == ['compat', 'compile']
assert all(item['returncode'] == 0 for item in compile_summary['commands'])
prep = read(primary / 'preparation.json')
config = read(package / 'compile-tokens.json')
assert config['original_compile'] == prep['argv']['compile']
original_filelist = local(prep['argv']['compile'][prep['argv']['compile'].index('-f') + 1])
wrapper = local(prep['argv']['compile'][1])


def normalize(token):
    if token.startswith('+incdir+'):
        return '+incdir+' + normalize(token[len('+incdir+'):])
    if '=' in token:
        key, value = token.split('=', 1)
        if value.startswith('/'):
            return key + '=' + normalize(value)
    if not token.startswith('/'):
        return token
    path = local(token).resolve()
    if path.is_relative_to(package / 'sources'):
        return str(BASE / path.relative_to(package / 'sources'))
    if path == package / 'tools/vcs_vm.py':
        return str(wrapper)
    if path == output / 'cpu.f':
        return str(original_filelist)
    if path in [output / name for name in ['csrc', 'simv', 'compat.o']]:
        return str(primary / path.name)
    return str(path)


for item in compile_summary['commands']:
    expected = [normalize(value) for value in prep['argv'][item['phase']]]
    observed = [normalize(value) for value in item['argv']]
    assert observed == expected, (item['phase'], observed, expected)
original_tokens = shlex.split(original_filelist.read_text(), comments=True)
relocated_filelist = output / 'cpu.f'
relocated_tokens = shlex.split(relocated_filelist.read_text(), comments=True)
assert [normalize(value) for value in relocated_tokens] == [normalize(value) for value in original_tokens]
files.update([original_filelist, relocated_filelist, output / 'commands.json'])
pattern = r"Parsing (?:design|included) file '([^']+)'"
original_log = primary / 'compile.log'
relocated_log = output / 'compile.log'
original_paths = {local(value).resolve() for value in re.findall(pattern, original_log.read_text(errors='replace'))}
text = relocated_log.read_text(errors='replace')
assert not re.search(r'Error-\[|Fatal:', text)
relocated_paths = {local(value).resolve() for value in re.findall(pattern, text)}
assert len(original_paths) == len(relocated_paths) == manifest['actual_VCS_parsed_source_files'] == 106
assert all(path.is_relative_to(package / 'sources') for path in relocated_paths)
assert {Path(normalize(str(path))) for path in relocated_paths} == original_paths
for path in relocated_paths:
    assert path.read_bytes() == Path(normalize(str(path))).read_bytes()
files.update([original_log, relocated_log, output / 'compat.log', Path(__file__)])
target.mkdir()
(target / 'review_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'review_driver.py')
result = {'status': 'RELOCATED_ALL106_RTL_FILES_BYTE_EXACT_NO_ORIGINAL_SOURCE_READ_COMPILE_PASS',
          'observed_at': datetime.now(timezone.utc).isoformat(), 'package': str(package),
          'actual_VCS_parsed_files': len(relocated_paths), 'all_parsed_files_inside_relocated_copy': True,
          'every_source_and_header_byte_exact': True, 'compat_and_compile_argv_only_paths_changed': True,
          'CPU_filelist_order_defines_and_tokens_preserved': True,
          'previously_unlisted_dependencies_included': len(closure['supplemental_dependencies']),
          'source_sha256': {str(path): sha(path) for path in sorted(files)},
          'full_model_inference_accepted': False, 'official_accuracy_accepted': False,
          'final_delivery_accepted': False,
          'scope': 'Real VCS compile/elaboration from an independently moved RTL snapshot. All106 consumed files are inside the copy and byte-exact to current originals, original defines/filelist order/options preserved. No inference run, historical include-hash reconstruction, PPA or final delivery acceptance.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'actual_VCS_parsed_files',
      'previously_unlisted_dependencies_included']}), flush=True)
