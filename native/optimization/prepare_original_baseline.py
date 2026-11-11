"""Prepare the genuine original CPU+16x16 four-plane PE model benchmark.

Unmodified original graph/PE C and unfused graph are used. This only builds
firmware and an input image; no RTL execution or before/after ADP is claimed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
SWIN = BASE / 'native/swin-tiny'
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
parser = argparse.ArgumentParser()
parser.add_argument('--image', default='ILSVRC2012_val_00000274.JPEG')
args = parser.parse_args()
assert re.fullmatch(r'ILSVRC2012_val_\d{8}\.JPEG', args.image)
case = ROOT / 'work/original-baseline-full' / args.image
assert not case.exists(), 'Preserve previous baseline preparation'
original = SWIN / 'work/verify.ylnpqxth'
proof = json.loads((original / 'summary.json').read_text())
assert proof['status'] == 'PASS' and not proof['full_model_rtl']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


for path, value in proof['source_sha256'].items():
    assert sha(local(path)) == value, path
graph = (original / 'graph.bin').read_bytes()
assert sha(original / 'graph.bin') == proof['compiled']['sha256']
assert sha(original / 'constants.bin') == proof['compiled']['constant_sha256']
header = struct.unpack_from('<16I', graph)
assert header[0:2] == (0x4e495753, 1) and header[3:5] == (1396, 77)
assert header[14:] == (0, 0), 'Must retain original unfused graph'
input_tensor = struct.unpack_from('<6I8i', graph, header[7] + header[5] * 56)
output_tensor = struct.unpack_from('<6I8i', graph, header[7] + header[6] * 56)
assert input_tensor[1:3] == (150528, 1) and output_tensor[1:3] == (1000, 1)
input_path = ROOT / 'work/fusion-proof' / (args.image + '.input')
expected_path = ROOT / 'work/fusion-proof' / (args.image + '.baseline')
if (original / (args.image + '.input.bin')).exists():
    assert input_path.read_bytes() == (original / (args.image + '.input.bin')).read_bytes()
    assert expected_path.read_bytes() == (original / (args.image + '.c.bin')).read_bytes()
assert len(input_path.read_bytes()) == 602112 and len(expected_path.read_bytes()) == 4000
stats = {'weight_loads': 0, 'pe_passes': 0, 'mmio_writes': 0, 'mmio_reads': 0}
for offset in range(header[4]):
    batch, m, n, k, *_ = struct.unpack_from('<12i', graph, header[9] + offset * 48)
    assert min(batch, m, n, k) > 0 and k <= 2147483647 // (255 * 255)
    columns = (n + 15) // 16
    rows = (m + 3) // 4
    blocks = (k + 15) // 16
    stats['weight_loads'] += batch * columns * blocks * 2
    stats['pe_passes'] += batch * columns * rows * blocks * 4
    stats['mmio_reads'] += batch * columns * rows * blocks * 4 * 64
    stats['mmio_writes'] += 65 + batch * columns * blocks * (162 + rows * 36)
assert stats['pe_passes'] == 17941440 and stats['mmio_reads'] == 1148252160
assert stats['mmio_writes'] == 181690705
case.mkdir(parents=True)
layout = case / 'baseline_layout.h'
layout.write_text('#define MODEL_DESCRIPTOR_BASE 0x80040000u\n#define MODEL_EXPECTED_BASE 0x83fff000u\n#define MODEL_NODES 1396\n' +
                  ''.join('#define BASELINE_' + key.upper() + ' ' + str(value) + 'ull\n' for key, value in stats.items()))
sources = [PROJECT / 'sw/start.S', SWIN / 'c/graph.c', SWIN / 'c/pe_driver.c',
           ROOT / 'c/original_baseline_main.c']
files = [Path(__file__), original / 'summary.json', original / 'graph.bin', original / 'constants.bin',
         SWIN / 'c/graph.h', SWIN / 'c/pe_driver.h', input_path, expected_path,
         PROJECT / 'sw/link.ld', layout, *sources]
hashes = {str(path): sha(path) for path in files}
prefix = Path('/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-')
if not Path(str(prefix) + 'gcc').is_file():
    prefix = BASE / 'native/rootfs/home/zhz/rtl_tools/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-'
argv = [str(prefix) + 'gcc', '-march=rv32imc_zicsr', '-mabi=ilp32', '-O2', '-ffp-contract=off',
        '-ffreestanding', '-fno-builtin', '-msmall-data-limit=0', '-nostartfiles', '-Wl,--no-relax',
        '-Wl,-T,' + str(PROJECT / 'sw/link.ld'), '-Wl,-Map,' + str(case / 'program.map'),
        '-I' + str(SWIN / 'c'), '-I' + str(case), *map(str, sources), '-lm', '-lc', '-lgcc', '-o', str(case / 'program.elf')]
with (case / 'compile.log').open('w') as log:
    subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
subprocess.run([str(prefix) + 'objcopy', '-O', 'binary', case / 'program.elf', case / 'program.bin'], check=True)
symbols = subprocess.check_output([str(prefix) + 'nm', '-S', case / 'program.elf'], text=True)
(case / 'symbols.txt').write_text(symbols)
addresses = {}
for variable in ['node_cycles', 'inference_cycles', 'model_error_node', 'model_mac_jobs', 'model_top1',
                 'model_expected_top1', 'model_max_error_bits', 'baseline_weight_loads', 'baseline_pe_passes',
                 'baseline_mmio_writes', 'baseline_mmio_reads']:
    match = re.search(r'^([0-9a-f]+)\s+([0-9a-f]+)\s+[BbDd]\s+' + variable + '$', symbols, re.M)
    assert match, variable
    addresses[variable] = int(match[1], 16)
sections = [(0x80000000, (case / 'program.bin').read_bytes()), (0x80040000, graph),
            (header[12], (original / 'constants.bin').read_bytes()),
            (header[13] + input_tensor[0], input_path.read_bytes()), (0x83fff000, expected_path.read_bytes())]
sections.sort()
for (address, data), (following, _) in zip(sections, sections[1:]):
    assert address + len(data) <= following
assert header[13] + header[11] < 0x83fff000
assert all(0x80000000 <= address and address + len(data) <= 0x84000000 for address, data in sections)
with (case / 'data.hex').open('w') as stream:
    for address, data in sections:
        stream.write('@%08x\n' % (address - 0x80000000))
        for start in range(0, len(data), 65536):
            stream.write(''.join('%02x\n' % value for value in data[start:start + 65536]))
for path, value in hashes.items():
    assert sha(Path(path)) == value, path
result = {'status': 'ORIGINAL_CPU_PE_FULL_MODEL_FIRMWARE_AND_IMAGE_PREPARED_NOT_EXECUTED',
          'image': args.image, 'geometry': 'original16x16x4 signed-magnitude four-plane PE,256SRAM',
          'unmodified_graph_and_PE_driver_C': True, 'SWIN_HOST_not_defined': True,
          'graph_nodes': 1396, 'MAC_jobs': 77, 'static_original_driver_counts': stats,
          'source_sha256': hashes, 'compiler_argv': argv,
          'firmware_bytes': (case / 'program.bin').stat().st_size,
          'artifact_sha256': {name: sha(case / name) for name in ['program.elf', 'program.bin', 'program.map', 'symbols.txt', 'data.hex']},
          'symbols': addresses, 'arena_lo': header[13], 'arena_hi': header[13] + header[11],
          'output_address': header[13] + output_tensor[0], 'expected_address': 0x83fff000,
          'sections': [{'address': hex(address), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for address, data in sections],
          'logical_external_RAM_bytes': 67108864, 'clock_period_ns_for_future_comparison': 10,
          'baseline_total_cycles_measured': False, 'full_RV32_executed': False,
          'official_accuracy_accepted': False, 'before_after_ADP_accepted': False,
          'scope': 'Build and loader bounds only. Original unfused1396-node graph, unmodified original C operators and four-plane MMIO PE driver;same first input and frozen-C output as optimized cases. New wrapper adds64-bit cycle/node counters and the same internal frozen-C check outside the timed region. Static MMIO counts are not observed cycles or system performance. Actual original CPU+PE RTL inference is still required.'}
(case / 'preparation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status', 'firmware_bytes', 'static_original_driver_counts']}), flush=True)
