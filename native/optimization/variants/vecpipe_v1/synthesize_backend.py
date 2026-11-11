"""Map the isolated vector-streaming backend under unchanged module conditions.

Reuse the exact mapped array and existing backend script/constraints. Whole
SoC timing, complete inference and organizer accuracy remain separate gates.
"""
import argparse
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
parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
case = ROOT / ('work/vecpipe-backend-synthesis-' + args.revision)
assert not case.exists(), 'Preserve prior attempt'
original = ROOT / 'work/backend-synthesis/R8_C16_L4/shared'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


old = json.loads((original / 'summary.json').read_text())
assert old['status'] == 'MAPPED_UNIT_SETUP_PASS' and old['SRAMs'] == 128
frozen = json.loads((original / 'inputs-manifest.json').read_text())
for hashes in [old['source_sha256'], frozen['frozen_sha256']]:
    for name, expected in hashes.items():
        assert sha(local(name)) == expected, name
qualification = ROOT / 'work/vecpipe-axi-v1/summary.json'
proof = json.loads(qualification.read_text())
assert proof['status'] == 'VECPIPE_AXI_REFERENCE_FAULT_CANCEL_RESET_THREE_SRAM_GEOMETRIES_PASS'
for name, expected in proof['source_sha256'].items():
    assert sha(local(name)) == expected, name
generation = json.loads((HERE / 'backend-generation.json').read_text())
assert sha(HERE / 'axi_accel_backend_vecpipe.sv') == generation['generated_sha256']
assert sha(ROOT / 'rtl/axi_accel_shared_backend.sv') == generation['original_sha256']
case.mkdir()
inputs = case / 'inputs'
inputs.mkdir()
for source in sorted((original / 'inputs').iterdir()):
    if source.is_file():
        (inputs / source.name).write_bytes(source.read_bytes())
targets = list(inputs.glob('*_axi_vector_engine.sv'))
assert len(targets) == 1
targets[0].write_bytes((HERE / 'axi_vector_engine_vecpipe.sv').read_bytes())
pipeline = inputs / 'fp32_vector_pipeline.sv'
pipeline.write_bytes((HERE / 'fp32_vector_pipeline.sv').read_bytes())
backend = inputs / 'backend.sv'
text = (HERE / 'axi_accel_backend_vecpipe.sv').read_text()
old_header = 'parameter ROWS=8,COLS=32,LANES=4,ID_WIDTH=3'
assert text.count(old_header) == 1
text = text.replace(old_header, 'parameter ROWS=8,COLS=16,LANES=4,ID_WIDTH=3')
old_array = 'affine_array #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES)) u_array('
assert text.count(old_array) == 1
backend.write_text(text.replace(old_array, 'affine_array_ROWS8_COLS16_LANES4 u_array('))
script = (original / 'run.tcl').read_text()
for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
    script = script.replace(prefix, str(BASE) + '/')
script = script.replace(str(original), str(case)).replace('axi_accel_backend', 'axi_accel_backend_vecpipe')
# Preserve the original ordered source list and add only the pipeline module.
needle = '{' + str(backend) + '}'
assert script.count(needle) == 1
script = script.replace(needle, '{' + str(pipeline) + '} ' + needle)
(case / 'run.tcl').write_text(script)
paths = [Path(__file__), ROOT / 'synthesize_backend_choices.py', original / 'run.tcl',
         original / 'summary.json', original / 'inputs-manifest.json', qualification,
         HERE / 'backend-generation.json', HERE / 'axi_accel_backend_vecpipe.sv',
         HERE / 'axi_vector_engine_vecpipe.sv', HERE / 'fp32_vector_pipeline.sv',
         *[local(name) for name in old['source_sha256']], *inputs.iterdir(), case / 'run.tcl']
hashes = {str(path): sha(path) for path in paths}
(case / 'preparation.json').write_text(json.dumps({
    'status': 'VECPIPE_BACKEND_SNAPSHOT_READY_MAPPING_PENDING', 'source_sha256': hashes,
    'geometry': 'R8_C16_L4', 'clock_period_ns': 10, 'input_output_delay_ns': 1,
    'exact_original_mapped_array_reused': True,
    'source_module': 'axi_accel_backend_vecpipe', 'scope': 'All8 backend only, not complete SoC.'
}, indent=2) + '\n')


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))


print('VECPIPE_BACKEND_SYNTHESIS_PHASE map', flush=True)
started = time.monotonic()
with (case / 'run.log').open('w') as log:
    process = subprocess.Popen(['dc_shell', '-f', str(case / 'run.tcl')], cwd=case,
                               stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, preexec_fn=limits)
    try:
        while True:
            try:
                code = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                print('VECPIPE_BACKEND_SYNTHESIS_RUNNING elapsed_s=%d' % (time.monotonic() - started), flush=True)
                if time.monotonic() - started > 7200:
                    raise TimeoutError('backend mapping')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
log = (case / 'run.log').read_text(errors='replace')
assert code == 0 and log.count('BACKEND_SYNTHESIS_MAPPED fp_before_mapping=1') == 1
assert not re.search(r'^Error:', log, re.M)
for name, expected in hashes.items():
    assert sha(Path(name)) == expected, name
setup = float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / 'timing.rpt').read_text())[1])
hold = float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)', (case / 'hold.rpt').read_text())[1])
summary = {'status': 'VECPIPE_BACKEND_MAPPED_SETUP_PASS' if setup >= 0 else 'VECPIPE_BACKEND_MAPPED_SETUP_FAIL',
           'area': float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1]),
           'setup_slack_ns': setup, 'hold_slack_ns': hold,
           'clock_period_ns': 10, 'input_output_delay_ns': 1, 'SRAMs': 128,
           'shared_iterative_FP32_instances_before_mapping': 1,
           'elapsed_s': time.monotonic() - started, 'source_sha256': hashes,
           'artifact_sha256': {name: sha(case / name) for name in ['mapped.ddc', 'mapped.v', 'constraints.sdc', 'area.rpt', 'timing.rpt', 'hold.rpt', 'run.log']},
           'complete_SoC_area_timing_accepted': False, 'official_accuracy_accepted': False,
           'full_inference_accepted': False, 'final_ADP_accepted': False,
           'scope': 'Same exact mapped array, SRAMs, module IO boundary, Nangate/SRAM libraries and 10ns/1ns constraints as scalar shared backend. Adds pointwise FP pipeline; one original shared iterative ALU remains for DIV/other engines. Complete SoC timing/area and actual full inference required.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(summary['status'], flush=True)
