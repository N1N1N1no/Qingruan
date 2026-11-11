"""Export immutable C/firmware/model-data components, never touching live cases."""
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
target = ROOT / ('work/inference-replay-preparation-' + args.revision)
assert not target.exists(), 'Preserve prior evidence'
package = target / 'inference-snapshot'
files = {Path(__file__), ROOT / 'inference_replay_template.py'}


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    files.add(path)
    return json.loads(path.read_text())


firmware = ROOT / 'work/complete-model-direct-build/R8_C16_L4'
build = read(firmware / 'manifest.json')
assert build['status'] == 'ALL8_RV32_FIRMWARE_BUILD_PASS_EXECUTION_PENDING'
for name, value in build['source_sha256'].items():
    assert sha(local(name)) == value, name
assert sha(firmware / 'program.elf') == build['firmware_sha256']
primary = ROOT / 'work/vecpipe-full-rv32-v1'
prep = read(primary / 'preparation.json')
suite = read(ROOT / 'work/vecpipe-image-suite-v1/summary.json')
assert suite['image_count'] == 20 and suite['public_count'] == 5 and suite['private_count'] == 15
assert suite['all_preprocessed_inputs_byte_equal_to_existing_fusion_proof']
assert suite['all_frozen_C_outputs_byte_equal_to_existing_fused_outputs']
assert (primary / 'program.bin').read_bytes() == (firmware / 'program.bin').read_bytes()
arguments = build['compiler_argv'][1:]
source_paths = {local(value).resolve() for value in arguments if value.startswith('/') and Path(value).suffix in ['.c', '.S']}
include_directories = [local(value[2:]).resolve() for value in arguments if value.startswith('-I/')]
link_token = next(value for value in arguments if value.startswith('-Wl,-T,'))
link_script = local(link_token[len('-Wl,-T,'):]).resolve()
source_paths.add(link_script)
for directory in include_directories:
    source_paths.update(path.resolve() for path in directory.glob('*.h'))
for path in source_paths:
    assert path.is_relative_to(BASE)
    for header in re.findall(r'^\s*#\s*include\s+"([^"]+)"', path.read_text(), re.M):
        candidates = [path.parent / header, *(directory / header for directory in include_directories)]
        actual = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
        assert actual in source_paths, (str(path), header)
package.mkdir(parents=True)
copied = {}
mapping = {}


def copy(path, destination):
    path = path.resolve()
    output = package / destination
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(path.read_bytes())
    assert sha(output) == sha(path)
    copied[destination] = sha(output)
    mapping[str(path)] = {'path': destination, 'sha256': sha(path)}
    files.add(path)
    return destination


for path in sorted(source_paths):
    copy(path, 'sources/' + str(path.relative_to(BASE)))


def rewrite(token):
    if token.startswith('-I/'):
        return '-I@ROOT@/sources/' + str(local(token[2:]).resolve().relative_to(BASE))
    if token.startswith('-Wl,-T,'):
        return '-Wl,-T,@ROOT@/' + mapping[str(link_script)]['path']
    if token.startswith('-Wl,-Map,'):
        return '-Wl,-Map,@OUT@/program.map'
    if token.startswith('/'):
        path = local(token).resolve()
        if path == firmware / 'program.elf':
            return '@OUT@/program.elf'
        return '@ROOT@/' + mapping[str(path)]['path']
    return token


compiler_arguments = [rewrite(value) for value in arguments]
firmware_paths = {name: copy(firmware / ('program.' + suffix), 'firmware/program.' + suffix)
                  for name, suffix in [('binary', 'bin'), ('elf', 'elf'), ('hex', 'hex')]}
section_paths = [firmware / 'program.bin', ROOT / 'work/fused-plan/graph.bin',
                 ROOT / 'work/fused-plan/gelu-fusions.bin', ROOT / 'work/fused-plan/constants.bin',
                 None, ROOT / 'work/lookup-tables/gelu.bin', ROOT / 'work/lookup-tables/quant-thresholds.bin', None]
static_sections = []
for index, source in enumerate(section_paths):
    if source is None:
        continue
    section = prep['sections'][index]
    assert sha(source) == section['sha256'] and source.stat().st_size == section['bytes']
    path = firmware_paths['binary'] if index == 0 else copy(source, 'model-data/' + source.name)
    static_sections.append({'address': int(section['address'], 16), 'bytes': section['bytes'],
                            'sha256': section['sha256'], 'path': path,
                            'role': 'firmware' if index == 0 else 'model_data'})
images = []
for image in suite['images']:
    name = image['image']
    raw = BASE / 'competition/images' / name
    inp = ROOT / 'work/fusion-proof' / (name + '.input')
    ref = ROOT / 'work/fusion-proof' / (name + '.baseline')
    assert sha(inp) == image['input_sha256'] and sha(ref) == image['frozen_C_logits_sha256']
    assert suite['source_sha256'][str(raw)] == sha(raw)
    sections = [{**section, 'address': int(section['address'], 16)} for section in image['sections']]
    assert len(sections) == 8 and [item['address'] for item in sections] == sorted(item['address'] for item in sections)
    assert all(0x80000000 <= item['address'] < item['address'] + item['bytes'] <= 0x84000000 for item in sections)
    assert all(a['address'] + a['bytes'] <= b['address'] for a, b in zip(sections, sections[1:]))
    images.append({'image': name, 'jpeg': copy(raw, 'images/' + name),
                   'input': copy(inp, 'inputs/' + name + '.input'),
                   'reference': copy(ref, 'frozen-C/' + name + '.bin'),
                   'input_sha256': sha(inp), 'reference_sha256': sha(ref),
                   'frozen_C_top1': image['frozen_C_top1'],
                   'public_reference_top1': image['public_reference_top1'],
                   'data_hex_sha256': image['data_hex_sha256'], 'sections': sections})
for name in ['official_preprocess.py', 'reference_top1_5.csv', 'reference_top1_5.json', 'SHA256SUMS']:
    copy(BASE / 'competition' / name, 'preprocessing/' + name)
copy(ROOT / 'inference_replay_template.py', 'inference_replay.py')
symbol_names = {'NODE_CYCLES': 'node_cycles', 'INFERENCE_CYCLES': 'inference_cycles',
    'ERROR_NODE': 'model_error_node', 'MAC_JOBS': 'model_mac_jobs', 'FUSIONS': 'model_gelu_fusions',
    'MAC_COMMANDS': 'model_mac_commands', 'QUANT_COMMANDS': 'model_quant_commands', 'TOP1': 'model_top1',
    'EXPECTED_TOP1': 'model_expected_top1', 'MAX_ERROR': 'model_max_error_bits'}
config = {'compiler_arguments': compiler_arguments, 'original_compiler_argv': build['compiler_argv'],
          'parameters': prep['parameters'], 'symbol_names': symbol_names,
          'firmware': {**firmware_paths, 'binary_sha256': sha(firmware / 'program.bin'),
                       'elf_sha256': sha(firmware / 'program.elf'), 'hex_sha256': sha(firmware / 'program.hex')},
          'model_name': 'swin_tiny_patch4_window7_224', 'static_sections': static_sections,
          'input_address': int(prep['sections'][4]['address'], 16),
          'reference_address': int(prep['sections'][7]['address'], 16), 'images': images}
(package / 'replay-config.json').write_text(json.dumps(config, indent=2) + '\n')
copied['replay-config.json'] = sha(package / 'replay-config.json')
(package / 'preprocessing/requirements.txt').write_text('numpy==1.26.4\nPillow==11.1.0\n')
copied['preprocessing/requirements.txt'] = sha(package / 'preprocessing/requirements.txt')
(package / 'README.md').write_text('''# 冻结推理程序与模型数据复现组件

这是当前8行候选共用的原固件、C源码/头文件/链接脚本、转换后的图/权重/查表、20张现有JPEG/输入及冻结C输出的独立副本。没有下载数据或修改预处理、标签、容差。文件名official_preprocess.py的来源仍未认证，公开参考仍只匹配3/5。

需要现有RISC-V GCC14.2.0工具链。先运行python3 inference_replay.py verify，再运行build --cc /已安装路径/riscv-none-elf-gcc --output runs/firmware-v1。该步骤核对程序二进制、HEX及10个监视符号地址。

随后运行python3 inference_replay.py audit-data --firmware-build runs/firmware-v1 --output runs/images-v1 --materialize ILSVRC2012_val_00000274.JPEG。全部20个原八段RAM镜像在内存中逐一生成并核对哈希，只保存指定一图，避免重复写入公共权重。

使用NumPy1.26.4/Pillow11.1.0环境运行python3 inference_replay.py preprocess --output runs/preprocess-v1，可用此副本中的原预处理/JPEG重建20个输入并核对全部字节。每个输出目录只能使用一次。

这是程序/数据复现组件，不会启动硬件仿真，也不是最终交付包。原始ONNX到最终优化图/查表的完整转换流程仍需与原工作区脚本及其来源一起交付；本组件不能验收20图硬件推理、官方精度、最终选型/ADP或门级结果。
''')
copied['README.md'] = sha(package / 'README.md')
manifest = {'status': 'FROZEN_INFERENCE_PROGRAM_AND_DATA_COMPONENT_PREPARED_NOT_FINAL_DELIVERY',
            'files_sha256': copied, 'original_to_snapshot': mapping,
            'model_conversion_reproduced': False, 'hardware_inference_executed': False,
            'official_accuracy_accepted': False, 'final_delivery_accepted': False}
(package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
files.add(package / 'manifest.json')
(target / 'prepare_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'prepare_driver.py')
result = {'status': manifest['status'], 'observed_at': datetime.now(timezone.utc).isoformat(),
          'package': str(package), 'package_files': len(copied),
          'package_bytes': sum((package / name).stat().st_size for name in copied),
          'C_build_source_and_link_files': len(source_paths), 'provided_images': len(images),
          'firmware_bytes': (firmware / 'program.bin').stat().st_size,
          'source_sha256': {str(path): sha(path) for path in sorted(files)},
          'rebuild_accepted': False, 'canonical_RAM_reproduction_accepted': False,
          'hardware_inference_executed': False, 'official_accuracy_accepted': False,
          'final_delivery_accepted': False}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'package', 'package_files', 'package_bytes']}), flush=True)
