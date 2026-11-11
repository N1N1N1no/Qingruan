"""Audit actual VCS parse dependencies and export a self-contained RTL snapshot.

Original live guards are unchanged. Previously unlisted dependencies are linked
to existing local vendor/archive evidence, never backdated into old manifests.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import zipfile

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
target = ROOT / ('work/compile-dependency-closure-' + args.revision)
assert not target.exists(), 'Preserve previous evidence'
files = set()


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    files.add(path)
    return json.loads(path.read_text())


def guards(data):
    result = {local(name).resolve(): value for name, value in data.get('source_sha256', {}).items()}
    for path, value in result.items():
        assert sha(path) == value, str(path)
    return result


vendor = ROOT / 'vendor'
provenance = read(vendor / 'hardfloat-provenance.json')
archive_path = vendor / 'HardFloat-1.zip'
assert sha(archive_path) == provenance['sha256']
files.add(archive_path)
hardfloat = [vendor / 'HardFloat-1/source' / name for name in
             ['HardFloat_consts.vi', 'HardFloat_localFuncs.vi', 'RISCV/HardFloat_specialize.vi']]
original_gate_path = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/work/soc_sdf.u63n3r7w/summary.json'
original_gate = read(original_gate_path)
assert original_gate['status'] == 'PASS' and original_gate['output_traces_match']
task_project = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
original_files = [task_project / 'snapshots/soc/pic_map_auto.h', task_project / 'tb/sram_reference_model.v']
supplemental = {}
with zipfile.ZipFile(archive_path) as archive:
    for path in hardfloat:
        member = str(path.relative_to(vendor))
        assert archive.read(member) == path.read_bytes(), member
        supplemental[path.resolve()] = {'sha256': sha(path), 'anchor': 'local_original_HardFloat_archive',
                                       'archive_member': member, 'byte_exact_to_anchor': True}


def anchor_hashes(value, wanted):
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.startswith(('/', 'project/')) and key.endswith(wanted) and isinstance(item, str) and re.fullmatch(r'[0-9a-f]{64}', item):
                result.append(item)
            result.extend(anchor_hashes(item, wanted))
    elif isinstance(value, list):
        for item in value:
            result.extend(anchor_hashes(item, wanted))
    return result


for path in original_files:
    hashes = anchor_hashes(original_gate, '/' + path.name)
    assert hashes and set(hashes) == {sha(path)}, str(path)
    supplemental[path.resolve()] = {'sha256': sha(path), 'anchor': str(original_gate_path),
                                    'byte_exact_to_anchor': True}

image = 'ILSVRC2012_val_00000274.JPEG'
cases = [ROOT / 'work/full-rv32' / geometry / image for geometry in
         ['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4']]
primary = ROOT / 'work/vecpipe-full-rv32-v1'
batch = ROOT / 'work/vecpipe-images-rv32-v1/runtime'
cases.extend([primary, batch])
records = []
parsed_by_case = {}
for case in cases:
    prep_path = case / 'preparation.json'
    if case == batch:
        data = read(case.parent / 'preparation.json')
        compiled = read(case / 'compile-summary.json')
        existing = guards(data)
        existing.update(guards(compiled))
    else:
        data = read(prep_path)
        existing = guards(data)
    log_path = case / 'compile.log'
    log = log_path.read_text(errors='replace')
    assert not re.search(r'Error-\[|Fatal:', log)
    paths = {local(name).resolve() for name in re.findall(r"Parsing (?:design|included) file '([^']+)'", log)}
    assert len(paths) >= 100 and all(path.is_relative_to(BASE) and path.is_file() for path in paths)
    unlisted = paths - set(existing)
    assert unlisted == set(supplemental), [str(path) for path in unlisted]
    for path in paths:
        expected = existing.get(path, supplemental.get(path, {}).get('sha256'))
        assert expected == sha(path), str(path)
    parsed_by_case[case] = paths
    files.add(log_path)
    records.append({'case': str(case), 'actually_parsed_files': len(paths),
                    'previous_guarded_entries': len(existing),
                    'previously_unlisted_actual_files': [str(path) for path in sorted(unlisted)],
                    'actual_compile_log_sha256': sha(log_path),
                    'actual_parse_dependency_sha256': {str(path): sha(path) for path in sorted(paths)}})

primary_prep = read(primary / 'preparation.json')
original_compile = primary_prep['argv']['compile']
cpu_path = local(original_compile[original_compile.index('-f') + 1])
cpu_tokens = shlex.split(cpu_path.read_text(), comments=True)
assert all('\n' not in token and not token.startswith(('-f', '-F')) for token in cpu_tokens)
files.add(cpu_path)
target.mkdir()
snapshot = target / 'rtl-snapshot'
snapshot.mkdir()
source_map = {}
exported = {}


def copy(path, relative=None):
    path = path.resolve()
    relative = relative or 'sources/' + str(path.relative_to(BASE))
    destination = snapshot / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(path.read_bytes())
    assert sha(destination) == sha(path)
    exported[relative] = sha(destination)
    source_map[str(path)] = {'exported': relative, 'sha256': sha(path)}
    files.add(path)
    return relative


for path in sorted(parsed_by_case[primary]):
    copy(path)
compat_source = local(primary_prep['argv']['compat'][3])
copy(compat_source)
wrapper = local(original_compile[1])
copy(wrapper, 'tools/vcs_vm.py')
for path, name in [(vendor / 'HardFloat-1/COPYING.txt', 'licenses/HardFloat-COPYING.txt'),
                   (task_project.parent / 'vendor/Cores-VeeR-EL2/LICENSE', 'licenses/VeeR-EL2-LICENSE')]:
    copy(path, name)


def rewrite(token):
    if token == original_compile[0]:
        return '@PYTHON@'
    if token == original_compile[1]:
        return '@ROOT@/tools/vcs_vm.py'
    if token == str(cpu_path):
        return '@OUT@/cpu.f'
    if token.startswith('+incdir+'):
        path = local(token[len('+incdir+'):]).resolve()
        assert path.is_relative_to(BASE)
        return '+incdir+@ROOT@/sources/' + str(path.relative_to(BASE))
    prefix, value = token.split('=', 1) if '=' in token else ('', token)
    if value.startswith(('/workspace/', str(BASE) + '/')):
        path = local(value).resolve()
        if path in [primary / 'csrc', primary / 'compat.o', primary / 'simv']:
            converted = '@OUT@/' + path.name
        else:
            assert str(path) in source_map, str(path)
            converted = '@ROOT@/' + source_map[str(path)]['exported']
        return prefix + '=' + converted if prefix else converted
    return token


config = {label: [rewrite(token) for token in primary_prep['argv'][label]] for label in ['compat', 'compile']}
config['cpu_filelist'] = [rewrite(token) for token in cpu_tokens]
config['original_compile'] = original_compile
config['only_compile_path_relocation'] = True
(snapshot / 'compile-tokens.json').write_text(json.dumps(config, indent=2) + '\n')
exported['compile-tokens.json'] = sha(snapshot / 'compile-tokens.json')
copy(ROOT / 'replay_compile_template.py', 'replay_compile.py')
readme = snapshot / 'README.md'
readme.write_text('''# 当前流水候选的独立 RTL 编译副本

此目录保存实际VCS编译日志中所有CPU、八引擎、测试平台及包含文件，并保留HardFloat/VeeR许可。文件由本地现有工程复制，字节未改动；文件路径由脚本在编译时重建，目录可移动。

在已配置VCS、GCC及许可证的Linux环境中运行：

    python3 replay_compile.py verify
    python3 replay_compile.py compile --output runs/compile-v1

本机可通过工作区的run-native.sh调用相同脚本进入既有工具环境。每次编译使用新的runs目录，不覆盖已有结果。

此副本只提供当前候选的RTL编译复现。模型固件、权重和最终综合/门级/20图结果仍在原工作区保存；最终选型、官方精度及完整交付未验收。脚本不会启动推理仿真。原测试清单没有被追改，当前补充哈希不作为历史编译时哈希的证明。
''')
exported['README.md'] = sha(readme)
manifest = {'status': 'CANDIDATE_RTL_COMPILE_SNAPSHOT_PREPARED_NO_INFERENCE_ACCEPTANCE',
            'source_case': str(primary), 'actual_VCS_parsed_source_files': len(parsed_by_case[primary]),
            'files_sha256': exported, 'original_to_snapshot': source_map,
            'previously_unlisted_files': {str(path): value for path, value in supplemental.items()},
            'retroactive_compile_time_hash_claimed': False, 'official_accuracy_accepted': False,
            'final_delivery_accepted': False}
(snapshot / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
files.update([Path(__file__), ROOT / 'replay_compile_template.py', snapshot / 'manifest.json'])
(target / 'audit_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'audit_driver.py')
result = {'status': 'ACTUAL_VCS_DEPENDENCY_CURRENT_BYTE_IDENTITY_AND_RTL_SNAPSHOT_AUDIT_PASS',
          'observed_at': datetime.now(timezone.utc).isoformat(), 'cases': records,
          'supplemental_dependencies': {str(path): value for path, value in supplemental.items()},
          'snapshot': str(snapshot), 'snapshot_files': len(exported),
          'snapshot_bytes': sum((snapshot / name).stat().st_size for name in exported),
          'old_guards_unchanged': True, 'retroactive_compile_time_hash_claimed': False,
          'RTL_relocated_compile_accepted': False, 'full_model_inference_accepted': False,
          'official_accuracy_accepted': False, 'final_delivery_accepted': False,
          'source_sha256': {str(path): sha(path) for path in sorted(files)},
          'scope': 'Actual parse dependencies of five completed VCS compilations, current-byte checks against preserved guards plus original local HardFloat archive and original task1 evidence. New immutable source snapshot only; no backdated guards, historical include-content reconstruction, model/gate reexecution or final delivery acceptance.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'snapshot', 'snapshot_files', 'snapshot_bytes']}), flush=True)
