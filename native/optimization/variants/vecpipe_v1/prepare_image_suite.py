"""Freeze all20 existing inputs/references as canonical full-SoC RAM images.

No model inference or hardware result is claimed by preparation. The original
eight sorted sections are preserved; only each image's input/reference bytes
change. No overlay/parser adaptation and no repeated download are needed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
sys.path.insert(0, str(ROOT / 'gpu-runtime'))
import numpy as np
import PIL

parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / ('work/vecpipe-image-suite-' + args.revision)
assert not case.exists(), 'Preserve previous suite'
fixture = ROOT / 'work/vecpipe-full-rv32-v1'
competition = BASE / 'competition'
fusion = ROOT / 'work/fusion-proof'
started = time.monotonic()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


prep = json.loads((fixture / 'preparation.json').read_text())
assert prep['geometry'] == 'R8_C16_L4'
for name, expected in prep['source_sha256'].items():
    assert sha(local(name)) == expected, name
fusion_proof = json.loads((fusion / 'summary.json').read_text())
assert fusion_proof['status'] == 'PASS' and fusion_proof['full_model_samples'] == 20
images = sorted((competition / 'images').glob('*.JPEG'))
assert len(images) == 20 and all(re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG', path.name) for path in images)
samples = {item['image']: item for item in fusion_proof['samples']}
assert set(samples) == {path.name for path in images}
references = json.loads((competition / 'reference_top1_5.json').read_text())
public = {item['file']: item['top1']['Swin-Tiny']['index'] for item in references['images']}
assert len(public) == 5 and references['class_index_base'] == 0
manifest = {}
for line in (competition / 'SHA256SUMS').read_text().splitlines():
    expected, name = line.split(maxsplit=1)
    manifest[name.strip().removeprefix('*')] = expected
model = competition / 'models/swin_tiny_patch4_window7_224_imagenet1k_int8.onnx'
assert sha(model) == manifest[str(model.relative_to(competition))]
for name in ['reference_top1_5.json', 'reference_top1_5.csv']:
    assert sha(competition / name) == manifest[name]
preprocess = competition / 'official_preprocess.py'
cuda = json.loads((ROOT / 'work/cuda-reference/summary.json').read_text())
assert sha(preprocess) == cuda['preprocess_sha256'] and sha(model) == cuda['model_sha256']
spec = importlib.util.spec_from_file_location('unchanged_official_preprocess', preprocess)
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)

sections = prep['sections']
input_address = 0x81e6e980
expected_address = 0x83fff000
assert sum(item['bytes'] == 602112 and int(item['address'], 16) == input_address for item in sections) == 1
assert sum(item['bytes'] == 4000 and int(item['address'], 16) == expected_address for item in sections) == 1
encoded_sections = []
base_digest = hashlib.sha256()
with (fixture / 'data.hex').open('rb') as stream:
    for item in sections:
        address = int(item['address'], 16)
        header = stream.read(10)
        assert header == ('@%08x\n' % (address - 0x80000000)).encode()
        encoded = stream.read(3 * item['bytes'])
        assert len(encoded) == 3 * item['bytes'] and encoded[2::3] == b'\n' * item['bytes']
        raw = bytes.fromhex(encoded.decode('ascii'))
        assert len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256']
        base_digest.update(header)
        base_digest.update(encoded)
        encoded_sections.append((address, header, encoded, item))
    assert not stream.read(1)
base_sha = base_digest.hexdigest()
assert base_sha == sha(fixture / 'data.hex')
case.mkdir()
(case / 'images').mkdir()
sources = [Path(__file__), preprocess, model, competition / 'SHA256SUMS',
           competition / 'reference_top1_5.json', competition / 'reference_top1_5.csv',
           ROOT / 'requirements.json', BASE / 'native/audits/20261107/requirements.json',
           fixture / 'preparation.json', fixture / 'data.hex', fixture / 'program.bin',
           fusion / 'summary.json', ROOT / 'work/cuda-reference/summary.json']
hashes = {str(path): sha(path) for path in sources}
records = []
for image in images:
    print('IMAGE_SUITE_PREPARE ' + image.name, flush=True)
    assert sha(image) == manifest[str(image.relative_to(competition))]
    tensor = pre.prepare_classification_input('swin_tiny_patch4_window7_224', image, False)
    assert tensor.shape == (1, 3, 224, 224) and tensor.dtype == np.float32
    input_bytes = tensor.tobytes()
    input_file = fusion / (image.name + '.input')
    expected_file = fusion / (image.name + '.baseline')
    fused_file = fusion / (image.name + '.fused')
    assert input_file.read_bytes() == input_bytes, image.name
    expected_bytes = expected_file.read_bytes()
    assert len(input_bytes) == 602112 and len(expected_bytes) == 4000
    assert expected_bytes == fused_file.read_bytes() and samples[image.name]['bit_exact_logits']
    expected_floats = struct.unpack('<1000f', expected_bytes)
    assert np.isfinite(expected_floats).all()
    top = max(range(1000), key=expected_floats.__getitem__)
    assert top == samples[image.name]['top1']
    hashes.update({str(path): sha(path) for path in [image, input_file, expected_file, fused_file]})
    directory = case / 'images' / image.name
    directory.mkdir()
    overrides = {input_address: input_bytes, expected_address: expected_bytes}
    new_sections = []
    if image.name == prep['image']:
        datafile = fixture / 'data.hex'
        data_sha = base_sha
        assert hashlib.sha256(input_bytes).hexdigest() == next(item['sha256'] for item in sections if int(item['address'],16) == input_address)
        assert hashlib.sha256(expected_bytes).hexdigest() == prep['expected_logits_sha256']
    else:
        datafile = directory / 'data.hex'
        h = hashlib.sha256()
        with datafile.open('wb') as stream:
            for address, header, original_encoded, item in encoded_sections:
                raw = overrides.get(address)
                encoded = (raw.hex('\n') + '\n').encode() if raw is not None else original_encoded
                assert len(encoded) == 3 * item['bytes']
                stream.write(header)
                stream.write(encoded)
                h.update(header)
                h.update(encoded)
        data_sha = h.hexdigest()
        assert datafile.stat().st_size == (fixture / 'data.hex').stat().st_size
    for address, header, encoded, item in encoded_sections:
        raw = overrides.get(address)
        new_sections.append({**item, 'sha256': hashlib.sha256(raw).hexdigest() if raw is not None else item['sha256'],
                             'same_as_original_first_image': raw is None or hashlib.sha256(raw).hexdigest() == item['sha256']})
    record = {'image': image.name, 'data_hex': str(datafile), 'data_hex_sha256': data_sha,
              'input_sha256': hashlib.sha256(input_bytes).hexdigest(),
              'frozen_C_logits_sha256': hashlib.sha256(expected_bytes).hexdigest(),
              'frozen_C_top1': top, 'public_reference_top1': public.get(image.name),
              'frozen_C_matches_public_top1': top == public[image.name] if image.name in public else None,
              'sections': new_sections, 'first_image_existing_RAM_reused': image.name == prep['image'],
              'parameters': prep['parameters'], 'hardware_executed_by_this_preparation': False}
    (directory / 'preparation.json').write_text(json.dumps(record, indent=2) + '\n')
    records.append(record)
public_records = [item for item in records if item['public_reference_top1'] is not None]
assert len(public_records) == 5
for name, expected in hashes.items():
    assert sha(Path(name)) == expected, name
summary = {'status': 'ALL20_EXISTING_IMAGES_PREPROCESS_REFERENCE_AND_CANONICAL_RAM_IMAGES_PREPARED_NO_HARDWARE_ACCEPTANCE',
           'images': records, 'image_count': 20, 'public_count': 5, 'private_count': 15,
           'original_first_image_RAM_reused': True, 'new_canonical_RAM_images': 19,
           'frozen_C_public_matches': sum(item['frozen_C_matches_public_top1'] for item in public_records),
           'all_preprocessed_inputs_byte_equal_to_existing_fusion_proof': True,
           'all_frozen_C_outputs_byte_equal_to_existing_fused_outputs': True,
           'same_firmware_model_graph_weights_tables_memory_addresses_and_monitor_parameters': True,
           'source_sha256': hashes, 'numpy_version': np.__version__, 'pillow_version': PIL.__version__,
           'python_version': sys.version, 'elapsed_s': time.monotonic() - started,
           'hardware_images_accepted': 0, 'official_accuracy_accepted': False, 'final_ADP_accepted': False,
           'scope': 'Materializes all20 existing local images as original eight-section canonical RAM files, with unchanged official preprocessing and existing full1000 frozen C references. No model/preprocess/label/tolerance changes and no inference/hardware result from preparation. Private organizer labels are not supplied.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(summary['status'], flush=True)
