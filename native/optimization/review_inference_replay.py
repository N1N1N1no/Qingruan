"""Independently review relocated firmware, ELF load state and all20 data inputs."""
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
parser.add_argument('--package', default='inference-replay-relocation-v1')
parser.add_argument('--preparation-revision', default='v1')
args = parser.parse_args()
assert all(re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in vars(args).values())
target = ROOT / ('work/inference-replay-review-' + args.revision)
assert not target.exists(), 'Preserve earlier evidence'
package = ROOT / 'work' / args.package
output = package / 'runs'
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


preparation = read(ROOT / ('work/inference-replay-preparation-' + args.preparation_revision) / 'summary.json')
verify(preparation['source_sha256'])
manifest = read(package / 'manifest.json')
assert manifest['status'] == 'FROZEN_INFERENCE_PROGRAM_AND_DATA_COMPONENT_PREPARED_NOT_FINAL_DELIVERY'
for name, value in manifest['files_sha256'].items():
    path = (package / name).resolve()
    assert path.is_relative_to(package) and sha(path) == value, name
    files.add(path)
for name, value in manifest['original_to_snapshot'].items():
    assert sha(local(name)) == value['sha256'] == sha(package / value['path'])
config = read(package / 'replay-config.json')
build_dir = output / 'firmware-v1'
build = read(build_dir / 'summary.json')
assert build['status'] == 'RELOCATED_RV32_FIRMWARE_REBUILD_BINARY_HEX_AND_SYMBOLS_EXACT_PASS'
assert build['manifest_sha256'] == sha(package / 'manifest.json')
assert (build_dir / 'program.bin').read_bytes() == (package / config['firmware']['binary']).read_bytes()
assert (build_dir / 'program.hex').read_bytes() == (package / config['firmware']['hex']).read_bytes()
assert len((build_dir / 'program.bin').read_bytes()) == build['firmware_bytes'] == 52592
assert build['checked_symbol_count'] == len(config['symbol_names']) == 10
command = read(build_dir / 'compile-command.json')
assert command['returncode'] == 0
firmware_dir = ROOT / 'work/complete-model-direct-build/R8_C16_L4'


def normalize(token):
    for prefix in ['-Wl,-T,', '-Wl,-Map,', '-I']:
        if token.startswith(prefix) and token[len(prefix):].startswith('/'):
            return prefix + normalize(token[len(prefix):])
    if not token.startswith('/'):
        return token
    path = local(token).resolve()
    if path.is_relative_to(package / 'sources'):
        return str(BASE / path.relative_to(package / 'sources'))
    if path in [build_dir / name for name in ['program.elf', 'program.map']]:
        return str(firmware_dir / path.name)
    return str(path)


assert [normalize(value) for value in command['argv']] == [normalize(value) for value in config['original_compiler_argv']]
compiler = local(command['argv'][0]).resolve()
assert sha(compiler) == build['compiler_sha256']
files.add(compiler)
for library in build['linked_libraries'].values():
    path = local(library['path']).resolve()
    assert sha(path) == library['sha256']
    files.add(path)


def elf32(path):
    data = path.read_bytes()
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    assert header[0][:7] == b'\x7fELF\x01\x01\x01'
    assert header[1] == 2 and header[2] == 243 and header[8] == 52
    assert header[9] == 32 and header[11] == 40
    sections = [struct.unpack_from('<10I', data, header[6] + index * header[11]) for index in range(header[12])]
    names_header = sections[header[13]]
    names = data[names_header[4]:names_header[4] + names_header[5]]
    named = {}
    for section in sections:
        name = names[section[0]:].split(b'\0', 1)[0].decode()
        payload = b'' if section[1] == 8 else data[section[4]:section[4] + section[5]]
        assert name not in named
        named[name] = (section, payload)
    programs = [struct.unpack_from('<8I', data, header[5] + index * header[9]) for index in range(header[10])]
    symbol_header = next(section for section in sections if section[1] == 2)
    strings_header = sections[symbol_header[6]]
    strings = data[strings_header[4]:strings_header[4] + strings_header[5]]
    assert symbol_header[9] == 16
    symbols = []
    for index in range(0, symbol_header[5], 16):
        raw = struct.unpack_from('<IIIBBH', data, symbol_header[4] + index)
        name = strings[raw[0]:].split(b'\0', 1)[0].decode()
        symbols.append((raw, name))
    files.add(path)
    return data, header, named, programs, symbols


original_elf = elf32(package / config['firmware']['elf'])
rebuilt_elf = elf32(build_dir / 'program.elf')
old_data, old_header, old_sections, old_programs, old_symbols = original_elf
new_data, new_header, new_sections, new_programs, new_symbols = rebuilt_elf
assert old_header == new_header and old_programs == new_programs
assert old_sections.keys() == new_sections.keys()
differences = []
for name in old_sections:
    old, new = old_sections[name], new_sections[name]
    assert old[0] == new[0]
    if old[1] != new[1]:
        assert name == '.strtab' and not old[0][2] & 2
        differences.append(name)
for program in old_programs:
    if program[0] == 1:
        assert old_data[program[1]:program[1] + program[4]] == new_data[program[1]:program[1] + program[4]]
assert len(old_symbols) == len(new_symbols)
file_name_changes = []
allowed_string_bytes = set()
for (old, old_name), (new, new_name) in zip(old_symbols, new_symbols):
    assert old == new
    if old_name != new_name:
        assert old[3] & 15 == 4 and re.fullmatch(r'cc[A-Za-z0-9]+\.o', old_name)
        assert re.fullmatch(r'cc[A-Za-z0-9]+\.o', new_name) and len(old_name) == len(new_name)
        allowed_string_bytes.update(range(old[0], old[0] + len(old_name) + 1))
        file_name_changes.append({'original': old_name, 'rebuilt': new_name})
old_strings = old_sections['.strtab'][1]
new_strings = new_sections['.strtab'][1]
assert len(old_strings) == len(new_strings)
changed_positions = {index for index, (old, new) in enumerate(zip(old_strings, new_strings)) if old != new}
assert changed_positions <= allowed_string_bytes

suite = read(ROOT / 'work/vecpipe-image-suite-v1/summary.json')
provided = {image['image']: image for image in suite['images']}
data_review = read(output / 'images-v1/summary.json')
assert data_review['status'] == 'RELOCATED_ALL20_CANONICAL_RAM_HASHES_EXACT_USING_FROZEN_FIRMWARE_PASS'
assert data_review['used_rebuilt_firmware'] and data_review['firmware_sha256'] == build['binary_sha256']
preprocessing = read(output / 'preprocess-v1/summary.json')
assert preprocessing['status'] == 'RELOCATED_ALL20_ORIGINAL_PREPROCESS_INPUT_BYTES_PASS'
assert preprocessing['numpy_version'] == '1.26.4' and preprocessing['pillow_version'] == '11.1.0'
assert len(data_review['images']) == len(preprocessing['images']) == len(config['images']) == len(provided) == 20
assert {image['image'] for image in data_review['images']} == set(provided)
assert {image['image'] for image in preprocessing['images']} == set(provided)
for image in data_review['images']:
    assert image['sections'] == 8 and image['byte_exact_to_original_hash']
    assert image['data_hex_sha256'] == provided[image['image']]['data_hex_sha256']
for image in preprocessing['images']:
    assert image['byte_exact'] and image['input_sha256'] == provided[image['image']]['input_sha256']
for image in config['images']:
    expected = provided[image['image']]
    assert image['data_hex_sha256'] == expected['data_hex_sha256']
    assert image['input_sha256'] == expected['input_sha256'] and image['reference_sha256'] == expected['frozen_C_logits_sha256']
    assert image['public_reference_top1'] == expected['public_reference_top1']
    assert [(item['address'], item['bytes'], item['sha256']) for item in image['sections']] == [
        (int(item['address'], 16), item['bytes'], item['sha256']) for item in expected['sections']]
materialized = output / 'images-v1/data.hex'
assert sha(materialized) == provided[data_review['materialized_image']]['data_hex_sha256']
assert data_review['manifest_sha256'] == preprocessing['manifest_sha256'] == sha(package / 'manifest.json')
assert all(result['actual_RV32_inference_executed'] is False and result['official_accuracy_accepted'] is False
           for result in [build, preprocessing, data_review])
total_section_bytes = sum(section['bytes'] for image in config['images'] for section in image['sections'])
assert total_section_bytes == 730528720
files.add(materialized)
files.update(build_dir / name for name in ['program.bin', 'program.hex', 'symbols.txt', 'compile.log'])
target.mkdir()
(target / 'review_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'review_driver.py')
result = {
    'status': 'RELOCATED_FIRMWARE_ALL_ELF_LOAD_STATE_AND20_INPUT_RAM_REPRODUCTION_REVIEW_PASS',
    'observed_at': datetime.now(timezone.utc).isoformat(), 'package': str(package),
    'firmware_bytes': build['firmware_bytes'], 'firmware_binary_and_HEX_byte_exact': True,
    'compiler_options_and_source_order_preserved_only_paths_changed': True,
    'ELF_header_and_program_headers_identical': True,
    'ELF_all_LOAD_payloads_and_allocated_sections_identical': True,
    'ELF_all_symbol_records_identical_except_unused_FILE_names': True,
    'ELF_symbols_checked': len(old_symbols), 'ELF_different_sections': differences,
    'ELF_nonloaded_temporary_FILE_name_changes': file_name_changes,
    'ELF_byte_exact': old_data == new_data,
    'monitor_symbol_addresses_checked': len(config['symbol_names']),
    'original_preprocess_reproduced_images': 20, 'canonical_RAM_hashes_reproduced_images': 20,
    'canonical_RAM_sections': 160, 'canonical_RAM_payload_bytes': total_section_bytes,
    'materialized_image': data_review['materialized_image'],
    'materialized_image_hex_bytes': materialized.stat().st_size,
    'frozen_C_public_top1_matches': suite['frozen_C_public_matches'],
    'preprocessing_origin_certified': False, 'model_conversion_reproduced': False,
    'actual_RV32_inference_executed': False, 'official_accuracy_accepted': False,
    'final_delivery_accepted': False,
    'source_sha256': {str(path): sha(path) for path in sorted(files)},
    'scope': 'Independent relocation/rebuild review. Same actual loaded firmware bytes,complete ELF load state,monitor addresses,original20 preprocessing inputs and canonical RAM hashes. The sole permitted ELF difference is temporary cc*.o STT_FILE names in nonloaded.strtab. No changed model/preprocessing/labels/tolerance,hardware inference acceptance,original ONNX conversion reproduction or final delivery claim.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'firmware_bytes', 'ELF_symbols_checked',
      'ELF_nonloaded_temporary_FILE_name_changes', 'canonical_RAM_hashes_reproduced_images']}), flush=True)
