"""Load and byte-check all20 prepared images using actual VCS $readmemh."""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
suite_case = ROOT / 'work/vecpipe-image-suite-v1'
case = ROOT / 'work/vecpipe-image-loader-v1'
assert not case.exists(), 'Preserve prior loader qualification'


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


suite = json.loads((suite_case / 'summary.json').read_text())
assert suite['status'] == 'ALL20_EXISTING_IMAGES_PREPROCESS_REFERENCE_AND_CANONICAL_RAM_IMAGES_PREPARED_NO_HARDWARE_ACCEPTANCE'
assert suite['image_count'] == 20 and suite['new_canonical_RAM_images'] == 19
for name, expected in suite['source_sha256'].items():
    assert sha(local(name)) == expected, name
case.mkdir()
fixed = {0x80000000: ROOT / 'work/vecpipe-full-rv32-v1/program.bin',
         0x80040000: ROOT / 'work/fused-plan/graph.bin',
         0x80089cc0: ROOT / 'work/fused-plan/gelu-fusions.bin',
         0x80100000: ROOT / 'work/fused-plan/constants.bin',
         0x8297d540: ROOT / 'work/lookup-tables/gelu.bin',
         0x82dcd540: ROOT / 'work/lookup-tables/quant-thresholds.bin'}
hashes = {str(path): sha(path) for path in [Path(__file__), HERE / 'tb_image_loader.sv', suite_case / 'summary.json', *fixed.values()]}
records = []
lines = []
total = 0
for image in suite['images']:
    datafile = local(image['data_hex'])
    assert sha(datafile) == image['data_hex_sha256']
    hashes[str(datafile)] = image['data_hex_sha256']
    paths = {**fixed, 0x81e6e980: ROOT / 'work/fusion-proof' / (image['image'] + '.input'),
             0x83fff000: ROOT / 'work/fusion-proof' / (image['image'] + '.baseline')}
    lines.append('%s %s 8\n' % (image['image'], datafile))
    assert len(image['sections']) == 8
    for section in image['sections']:
        address = int(section['address'], 16)
        path = paths[address]
        assert path.stat().st_size == section['bytes'] and sha(path) == section['sha256']
        hashes[str(path)] = section['sha256']
        total += section['bytes']
        lines.append('%08x %d %s\n' % (address, section['bytes'], path))
assert total == 730528720
manifest = case / 'images.txt'
manifest.write_text(''.join(lines))
hashes[str(manifest)] = sha(manifest)


def run(argv, label, limit):
    argv = list(map(str, argv))
    started = time.monotonic()
    print('IMAGE_LOADER_PHASE ' + label, flush=True)
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    with (case / (label + '.log')).open('w') as log:
        child = subprocess.Popen(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=limits)
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('IMAGE_LOADER_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > limit:
                        raise TimeoutError(label)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
    records.append({'label': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    assert code == 0, str(case / (label + '.log'))
    return (case / (label + '.log')).read_text(errors='replace')


run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c', '-o', case / 'compat.o'], 'compat', 120)
run(['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py', '-full64', '-sverilog',
     '-timescale=1ns/1ps', HERE / 'tb_image_loader.sv', '-top', 'tb_image_loader',
     '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv', '-LDFLAGS', case / 'compat.o'], 'compile', 600)
simulator = sha(case / 'simv')
text = run([case / 'simv', '+MANIFEST=' + str(manifest)], 'normal', 1800)
assert text.count('ALL20_CANONICAL_RAM_VCS_LOADER_PASS images=20 sections=160 checked_bytes=730528720') == 1
assert len(re.findall(r'IMAGE_LOADER_CASE image=', text)) == 20
assert not re.search(r'Fatal:|Error-\[|IMAGE_LOADER_FAIL|Warning-', text)
for name, expected in hashes.items():
    assert sha(Path(name)) == expected, name
assert sha(case / 'simv') == simulator
result = {'status': 'ALL20_CANONICAL_RAM_ACTUAL_VCS_LOADER_ALL_SECTION_BYTES_PASS',
          'images': 20, 'sections': 160, 'bytes_checked': total,
          'source_sha256': hashes, 'simulator_sha256': simulator, 'commands': records,
          'hardware_inference_accepted': False, 'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Actual VCS $readmemh loaded all20 complete canonical RAM files; every byte in all eight sections per image compared to original firmware, graph, constants, tables, input and frozen C reference binaries. RAM initialization proof only; no CPU/model inference or accuracy acceptance.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(result['status'], flush=True)
