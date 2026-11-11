"""Rebuild the frozen RV32 firmware and canonical model RAM images locally."""
import argparse
import binascii
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['verify', 'build', 'audit-data', 'preprocess'])
parser.add_argument('--output')
parser.add_argument('--cc', default='riscv-none-elf-gcc')
parser.add_argument('--firmware-build')
parser.add_argument('--materialize', help='One provided JPEG name; other images are hashed without writing their full hex files.')
args = parser.parse_args()
manifest = json.loads((ROOT / 'manifest.json').read_text())
config = json.loads((ROOT / 'replay-config.json').read_text())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify():
    for name, value in manifest['files_sha256'].items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT) and sha(path) == value, name


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


verify()
if args.action == 'verify':
    print(json.dumps({'status': 'FROZEN_INFERENCE_REPLAY_FILES_HASH_PASS',
                      'files': len(manifest['files_sha256']), 'hardware_inference_executed': False}), flush=True)
    raise SystemExit(0)
assert args.output, 'Supply a new runs/ output directory'
output = (ROOT / args.output).resolve()
assert output.is_relative_to(ROOT / 'runs') and not output.exists(), 'Preserve earlier runs'
output.mkdir(parents=True)
result = {'actual_RV32_inference_executed': False, 'official_accuracy_accepted': False,
          'model_conversion_reproduced': False, 'final_delivery_accepted': False}

if args.action == 'build':
    compiler = Path(shutil.which(args.cc) or args.cc).resolve()
    assert compiler.is_file()
    tools = {name: compiler.with_name(compiler.name[:-3] + name) for name in ['objcopy', 'nm']}
    assert all(path.is_file() for path in tools.values())
    def expand(value):
        return value.replace('@ROOT@', str(ROOT)).replace('@OUT@', str(output))
    argv = [str(compiler), *(expand(value) for value in config['compiler_arguments'])]
    started = time.monotonic()
    with (output / 'compile.log').open('w') as stream:
        child = subprocess.run(argv, cwd=output, stdout=stream, stderr=subprocess.STDOUT,
                               timeout=300, check=False)
    save(output / 'compile-command.json', {'argv': argv, 'returncode': child.returncode,
                                         'elapsed_s': time.monotonic() - started})
    assert child.returncode == 0, str(output / 'compile.log')
    subprocess.run([str(tools['objcopy']), '-O', 'binary', str(output / 'program.elf'),
                    str(output / 'program.bin')], check=True, timeout=120)
    data = (output / 'program.bin').read_bytes()
    assert data == (ROOT / config['firmware']['binary']).read_bytes()
    (output / 'program.hex').write_bytes(binascii.hexlify(data, b'\n', 1) + b'\n')
    assert sha(output / 'program.hex') == config['firmware']['hex_sha256']
    symbols = subprocess.check_output([str(tools['nm']), '-S', str(output / 'program.elf')], text=True)
    (output / 'symbols.txt').write_text(symbols)
    for key, name in config['symbol_names'].items():
        match = re.search(r'^([0-9a-f]+)\s+[0-9a-f]+\s+[BbDd]\s+' + name + r'$', symbols, re.M)
        assert match and int(match[1], 16) == int(config['parameters'][key], 16), name
    libraries = {}
    for name in ['libc.a', 'libm.a', 'libgcc.a']:
        library = Path(subprocess.check_output([str(compiler), '-print-file-name=' + name], text=True).strip()).resolve()
        assert library.is_file()
        libraries[name] = {'path': str(library), 'sha256': sha(library)}
    result.update(status='RELOCATED_RV32_FIRMWARE_REBUILD_BINARY_HEX_AND_SYMBOLS_EXACT_PASS',
                  firmware_bytes=len(data), binary_sha256=sha(output / 'program.bin'),
                  hex_sha256=sha(output / 'program.hex'),
                  ELF_byte_exact=sha(output / 'program.elf') == config['firmware']['elf_sha256'],
                  compiler_sha256=sha(compiler), compiler_version=subprocess.check_output([str(compiler), '--version'], text=True),
                  linked_libraries=libraries, checked_symbol_count=len(config['symbol_names']))
elif args.action == 'preprocess':
    import numpy as np
    import PIL
    module_path = ROOT / 'preprocessing/official_preprocess.py'
    spec = importlib.util.spec_from_file_location('frozen_preprocessing', module_path)
    preprocessing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preprocessing)
    checks = []
    for image in config['images']:
        data = preprocessing.prepare_classification_input(config['model_name'], ROOT / image['jpeg'], False)
        assert data.shape == (1, 3, 224, 224) and data.dtype == np.float32
        assert np.isfinite(data).all()
        payload = data.tobytes(order='C')
        assert payload == (ROOT / image['input']).read_bytes(), image['image']
        checks.append({'image': image['image'], 'input_sha256': hashlib.sha256(payload).hexdigest(),
                       'byte_exact': True})
    result.update(status='RELOCATED_ALL20_ORIGINAL_PREPROCESS_INPUT_BYTES_PASS',
                  numpy_version=np.__version__, pillow_version=PIL.__version__, images=checks,
                  preprocessing_origin_certified=False)
elif args.action == 'audit-data':
    assert not args.materialize or args.materialize in {image['image'] for image in config['images']}
    firmware = ROOT / config['firmware']['binary']
    if args.firmware_build:
        build = (ROOT / args.firmware_build).resolve()
        assert build.is_relative_to(ROOT / 'runs')
        built = json.loads((build / 'summary.json').read_text())
        assert built['status'] == 'RELOCATED_RV32_FIRMWARE_REBUILD_BINARY_HEX_AND_SYMBOLS_EXACT_PASS'
        firmware = build / 'program.bin'
        assert sha(firmware) == built['binary_sha256'] == config['firmware']['binary_sha256']
    cache = {}
    for section in config['static_sections']:
        path = firmware if section['role'] == 'firmware' else ROOT / section['path']
        payload = path.read_bytes()
        assert len(payload) == section['bytes'] and hashlib.sha256(payload).hexdigest() == section['sha256']
        cache[section['address']] = [binascii.hexlify(payload[index:index + 65536], b'\n', 1) + b'\n'
                                   for index in range(0, len(payload), 65536)]
    checks = []
    for image in config['images']:
        dynamic = {config['input_address']: ROOT / image['input'],
                   config['reference_address']: ROOT / image['reference']}
        for role in ['input', 'reference']:
            payload = (ROOT / image[role]).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == image[role + '_sha256']
            if role == 'reference':
                assert len(payload) == 4000 and all(math.isfinite(value) for value in struct.unpack('<1000f', payload))
        digest = hashlib.sha256()
        count = 0
        stream = (output / 'data.hex').open('wb') if image['image'] == args.materialize else None
        try:
            for section in image['sections']:
                address = section['address']
                chunks = cache.get(address)
                if chunks is None:
                    payload = dynamic[address].read_bytes()
                    assert len(payload) == section['bytes'] and hashlib.sha256(payload).hexdigest() == section['sha256']
                    chunks = [binascii.hexlify(payload[index:index + 65536], b'\n', 1) + b'\n'
                              for index in range(0, len(payload), 65536)]
                marker = ('@%08x\n' % (address - 0x80000000)).encode('ascii')
                for part in [marker, *chunks]:
                    digest.update(part)
                    count += len(part)
                    if stream is not None:
                        stream.write(part)
        finally:
            if stream is not None:
                stream.close()
        assert digest.hexdigest() == image['data_hex_sha256'], image['image']
        checks.append({'image': image['image'], 'data_hex_sha256': digest.hexdigest(),
                       'hex_bytes': count, 'sections': len(image['sections']), 'byte_exact_to_original_hash': True})
    result.update(status='RELOCATED_ALL20_CANONICAL_RAM_HASHES_EXACT_USING_FROZEN_FIRMWARE_PASS',
                  images=checks, materialized_image=args.materialize,
                  used_rebuilt_firmware=bool(args.firmware_build), firmware_sha256=sha(firmware),
                  scope='Generate original8-section byte-addressed RAM text with unchanged addresses,firmware,graph,weights,tables,input and frozen C logits. Hash all20 without writing20 repeated static copies; optional one image output. This is not hardware execution or official accuracy.')
verify()
result['manifest_sha256'] = sha(ROOT / 'manifest.json')
save(output / 'summary.json', result)
print(json.dumps({'status': result['status'], 'output': str(output)}), flush=True)
