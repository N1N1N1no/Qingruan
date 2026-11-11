"""Verify equal full-inference fixtures before accepting candidate timings.

Array tile loops execute inside one GEMM command. The C planner submits one
full M/N/K descriptor per graph batch, so columns do not change this count.
This audit does not accept any running simulation or organizer accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
GEOMETRIES = ['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4']


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=ROOT / 'work/full-candidate-fixtures-v1/summary.json')
args = parser.parse_args()
assert not args.output.exists(), 'Preserve previous audit'
records, proofs, files = {}, {}, [Path(__file__)]
for geometry in GEOMETRIES:
    case = ROOT / 'work/full-rv32' / geometry / 'ILSVRC2012_val_00000274.JPEG'
    proof = json.loads((case / 'preparation.json').read_text())
    assert proof['status'] == 'FULL_RV32_FIXTURES_AND_RAM_IMAGE_READY_EXECUTION_PENDING'
    assert proof['image'] == 'ILSVRC2012_val_00000274.JPEG'
    assert proof['logical_external_RAM_bytes'] == 67108864
    for path, expected in proof['source_sha256'].items():
        assert sha(local(path)) == expected, path
    proofs[geometry] = proof
    records[geometry] = {
        'case': str(case), 'geometry': proof['geometry'],
        'firmware_bin_sha256': sha(case / 'program.bin'),
        'RAM_image_sha256': sha(case / 'data.hex'),
        'testbench_sha256': sha(case / 'tb_soc.sv'),
        'external_RAM_model_sha256': sha(case / 'axi_ram_model.sv'),
        'frozen_source_files_checked': len(proof['source_sha256']),
        'all_frozen_sources_unchanged': True,
        'full_inference_completed_or_accepted': False,
    }
    files.extend([case / 'preparation.json', case / 'program.bin', case / 'tb_soc.sv',
                  case / 'axi_ram_model.sv', case / 'data.hex'])
first = proofs[GEOMETRIES[0]]
for geometry in GEOMETRIES[1:]:
    proof = proofs[geometry]
    assert proof['instruction_memory_model'] == first['instruction_memory_model']
    assert proof['sections'][1:] == first['sections'][1:], 'graph/constants/input/tables/reference differ'
    for parameter in ['ARENA_LO', 'ARENA_HI', 'OUTPUT_ADDR', 'EXPECTED_ADDR',
                      'EXPECTED_COMMANDS', 'NODES', 'EXPECTED_FUSIONS', 'EXPECTED_NODE_EVENTS']:
        assert proof['parameters'][parameter] == first['parameters'][parameter], parameter
    for key in ['testbench_sha256', 'external_RAM_model_sha256']:
        assert records[geometry][key] == records[GEOMETRIES[0]][key], key
assert records['R8_C16_L4']['firmware_bin_sha256'] == records['R8_C32_L4']['firmware_bin_sha256']
assert records['R8_C16_L4']['RAM_image_sha256'] == records['R8_C32_L4']['RAM_image_sha256']
assert first['parameters']['EXPECTED_COMMANDS'] == '4640'
assert first['parameters']['EXPECTED_NODE_EVENTS'] == '1312'
mac_path = ROOT / 'work/mac-plan/commands.jsonl'
nonmac_path = ROOT / 'work/nonmac-plan/commands.jsonl'
mac = [json.loads(line) for line in mac_path.read_text().splitlines()]
nonmac = [json.loads(line) for line in nonmac_path.read_text().splitlines()]
assert sum(command['opcode'] == 3 for command in mac) == 1877
assert len(mac) == 1878 and len(nonmac) == 2584
assert len(mac) + len(nonmac) + 166 + 12 == 4640
files.extend([mac_path, nonmac_path, ROOT / 'c/mac_plan.c', ROOT / 'c/mac_dispatch.c',
              ROOT / 'c/nonmac_plan.c', ROOT / 'c/nonmac_dispatch.c',
              ROOT / 'run_full_rv32.py', ROOT / 'tests/full_model_monitor.sv'])
result = {
    'status': 'THREE_CANDIDATE_FULL_RV32_FIXTURE_CONDITIONS_EQUAL_SOURCE_GUARDS_PASS',
    'image': first['image'], 'candidates': records,
    'common_graph_constants_input_GELU_quant_and_frozen_reference_sections': first['sections'][1:],
    'command_count': {'MAC_graph_batches': 1877, 'Conv_im2col': 1,
                      'nonMAC_and_MAC_postprocessing': 2584, 'quant': 166, 'GELU_fusions': 12,
                      'total': 4640, 'graph_execution_events': 1312},
    'C16_C32_loadable_firmware_and_RAM_images_byte_identical': True,
    'testbench_clock_and_RAM_response_conditions_byte_identical': True,
    'source_sha256': {str(path): sha(path) for path in files},
    'full_inference_accepted': False, 'official_accuracy_accepted': False,
    'final_ADP_accepted': False,
    'scope': 'Read-only comparison of three active first-image full-SoC fixtures and all frozen source files. Full M/N/K MAC commands are counted per graph batch; internal hardware tiling is not another CPU command. Same input, graph, model constants, tables, expected outputs, clock/RAM bench and validation boundaries. No execution success, measured latency, A100 match or final ranking is established here.'
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'status': result['status'], 'command_count': result['command_count'],
                  'frozen_source_files_checked': {g: record['frozen_source_files_checked'] for g, record in records.items()}}), flush=True)
