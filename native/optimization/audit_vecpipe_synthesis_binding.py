"""Bind live benchmark RTL to captured backend/control synthesis inputs.

Source lineage and existing DDC imports are checked separately from mapped
function, complete inference and formal equivalence. No live source is edited.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
HERE = ROOT / 'variants/vecpipe_v1'
parser = argparse.ArgumentParser()
parser.add_argument('--revision', required=True)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
target = ROOT / ('work/vecpipe-synthesis-binding-' + args.revision)
assert not target.exists(), 'Preserve prior evidence'
files = set()
cache = {}


def local(name):
    if name.startswith('/workspace/'):
        return BASE / name[11:]
    if name.startswith('/home/zhz/'):
        return BASE / 'native/rootfs' / name[1:]
    return Path(name)


def sha(path):
    path = path.resolve()
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    if path in cache:
        assert cache[path][0] == signature, 'Input changed during review: ' + str(path)
        return cache[path][1]
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    cache[path] = (signature, digest)
    return digest


def read(path):
    files.add(path)
    return json.loads(path.read_text())


def verify(hashes):
    for name, expected in hashes.items():
        path = local(name)
        assert sha(path) == expected, name
        files.add(path)


def checked(path, statuses):
    data = read(path)
    assert data['status'] in statuses, (str(path), data['status'])
    for key in ['source_sha256', 'inputs_sha256', 'frozen_sha256']:
        verify(data.get(key, {}))
    for name, expected in data.get('artifact_sha256', {}).items():
        artifact = path.parent / name
        assert sha(artifact) == expected, str(artifact)
        files.add(artifact)
    return data


work = ROOT / 'work'
benchmark = work / 'vecpipe-full-rv32-v1'
bench = checked(benchmark / 'preparation.json', ['VECPIPE_REAL_RV32_FULL_MODEL_PREPARED_NOT_EXECUTED'])
assert bench['geometry'] == 'R8_C16_L4' and bench['clock_period_ns'] == 10
compile_log = benchmark / 'compile.log'
parsed = {local(name).resolve() for name in re.findall(r"Parsing (?:design|included) file '([^']+)'", compile_log.read_text())}
assert len(parsed) == 106
files.add(compile_log)
dependency = checked(work / 'compile-dependency-closure-v1/summary.json',
    ['ACTUAL_VCS_DEPENDENCY_CURRENT_BYTE_IDENTITY_AND_RTL_SNAPSHOT_AUDIT_PASS'])
assert len(dependency['supplemental_dependencies']) == 5
scalar_soc = work / 'full-soc-synthesis/R8_C16_L4/shared/v1'
scalar_prep = checked(scalar_soc / 'preparation.json', ['FULL_ALL8_SOC_PREPARED_MAPPING_PENDING'])
assert scalar_prep['parameters_removed_from_generic_backend_instance'] == {
    'COLS': "32'sd16", 'ID_WIDTH': "32'sd3", 'LANES': "32'sd4", 'ROWS': "32'sd8"}
task = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
project = task / 'my_design/project'
original_prepared = task / 'work/soc_read.dbjzuval/prepared'
frozen_path = original_prepared / 'frozen_hashes.json'
frozen = read(frozen_path)
verify(frozen)
frozen_by_path = {local(name).resolve(): expected for name, expected in frozen.items()}
cpu_paths = sorted(path for path in parsed if '/vendor/Cores-VeeR-EL2/' in str(path) or '/snapshots/soc/' in str(path))
assert len(cpu_paths) == 56
for path in cpu_paths:
    snapshot = original_prepared / 'inputs/tree' / path.relative_to(task / 'my_design')
    assert snapshot.resolve() in frozen_by_path, str(snapshot)
    assert sha(path) == sha(snapshot) == frozen_by_path[snapshot.resolve()], str(path)
    files.update([path, snapshot])
control_sources = [project / 'rtl/axi_pe_router.v', ROOT / 'rtl/axi_accel_mmio.sv', ROOT / 'rtl/axi_dma_arbiter.sv']
for path in control_sources:
    assert path.resolve() in parsed
    if path.name != 'axi_pe_router.v':
        assert path.read_bytes() == (scalar_soc / 'inputs' / path.name).read_bytes()
        files.add(scalar_soc / 'inputs' / path.name)
    files.add(path)
scalar_top = work / 'full-rv32/R8_C16_L4/ILSVRC2012_val_00000274.JPEG/soc_accel_top.sv'
assert (scalar_soc / 'inputs/soc_accel_top.sv').read_bytes() == scalar_top.read_bytes()
old_top = scalar_top.read_text()
needle = 'axi_accel_backend #('
assert old_top.count(needle) == 1
assert (benchmark / 'soc_accel_top.sv').read_text() == old_top.replace(needle, 'axi_accel_backend_vecpipe #(')
files.update([scalar_top, scalar_soc / 'inputs/soc_accel_top.sv', benchmark / 'soc_accel_top.sv'])

backend_case = work / 'vecpipe-backend-synthesis-v1'
backend = checked(backend_case / 'summary.json', ['VECPIPE_BACKEND_MAPPED_SETUP_PASS'])
assert backend['SRAMs'] == 128 and min(backend['setup_slack_ns'], backend['hold_slack_ns']) >= 0
inputs = backend_case / 'inputs'
verbatim = []
for snapshot in sorted(inputs.glob('[0-9][0-9]_*')):
    name = snapshot.name[3:]
    if name == 'axi_vector_engine.sv':
        source = HERE / 'axi_vector_engine_vecpipe.sv'
    else:
        matches = [path for path in parsed if path.name == name]
        assert len(matches) == 1, (name, matches)
        source = matches[0]
    assert source.resolve() in parsed and snapshot.read_bytes() == source.read_bytes(), name
    verbatim.append({'snapshot': str(snapshot), 'benchmark_source': str(source), 'sha256': sha(snapshot)})
    files.update([snapshot, source])
assert len(verbatim) == 29
pipeline = HERE / 'fp32_vector_pipeline.sv'
assert pipeline.resolve() in parsed and (inputs / pipeline.name).read_bytes() == pipeline.read_bytes()
verbatim.append({'snapshot': str(inputs / pipeline.name), 'benchmark_source': str(pipeline), 'sha256': sha(pipeline)})
files.update([pipeline, inputs / pipeline.name])
source_backend = HERE / 'axi_accel_backend_vecpipe.sv'
assert source_backend.resolve() in parsed
text = source_backend.read_text()
old_header = 'parameter ROWS=8,COLS=32,LANES=4,ID_WIDTH=3'
old_array = 'affine_array #(.ROWS(ROWS),.COLS(COLS),.LANES(LANES)) u_array('
assert text.count(old_header) == text.count(old_array) == 1
specialized = text.replace(old_header, 'parameter ROWS=8,COLS=16,LANES=4,ID_WIDTH=3').replace(
    old_array, 'affine_array_ROWS8_COLS16_LANES4 u_array(')
assert (inputs / 'backend.sv').read_text() == specialized
files.update([source_backend, inputs / 'backend.sv'])
array_case = work / 'array-synthesis/R8_C16_L4'
old_backend_manifest = read(work / 'backend-synthesis/R8_C16_L4/shared/inputs-manifest.json')
verify(old_backend_manifest['source_sha256'])
verify(old_backend_manifest['frozen_sha256'])
recorded_array_hashes = [value for name, value in old_backend_manifest['source_sha256'].items()
                         if local(name) == array_case / 'mapped.ddc']
assert recorded_array_hashes == [sha(array_case / 'mapped.ddc')]
array_script = (array_case / 'run.tcl').read_text()
assert 'ROWS=8,COLS=16,LANES=4' in array_script
assert str(ROOT / 'rtl/affine_array.v') in array_script or '/workspace/native/optimization/rtl/affine_array.v' in array_script
assert (ROOT / 'rtl/affine_array.v').resolve() in parsed
backend_script = (backend_case / 'run.tcl').read_text()
assert str(array_case / 'mapped.ddc') in backend_script or '/workspace/native/optimization/work/array-synthesis/R8_C16_L4/mapped.ddc' in backend_script
assert 'set_dont_touch [current_design] true' in backend_script
files.update([array_case / 'mapped.ddc', array_case / 'run.tcl', backend_case / 'run.tcl', ROOT / 'rtl/affine_array.v'])
for name in ['HardFloat_consts.vi', 'HardFloat_localFuncs.vi', 'RISCV/HardFloat_specialize.vi']:
    path = ROOT / 'vendor/HardFloat-1/source' / name
    assert path.resolve() in parsed
    assert dependency['supplemental_dependencies'][str(path)]['byte_exact_to_anchor']
    files.add(path)

raw_case = work / 'vecpipe-full-soc-synthesis-v1'
soc_prep = checked(raw_case / 'preparation.json', ['VECPIPE_FULL_SOC_CONTROL_PREPARED_MAPPING_PENDING'])
old_control = (scalar_soc / 'soc_control_dc.v').read_text()
needle = '  axi_accel_backend u_backend ('
assert old_control.count(needle) == 1
assert (raw_case / 'soc_control_dc.v').read_text() == old_control.replace(needle, '  axi_accel_backend_vecpipe u_backend (')
assert soc_prep['original_control_only_callee_name_changed']
files.update([scalar_soc / 'soc_control_dc.v', raw_case / 'soc_control_dc.v'])
raw = checked(raw_case / 'summary.json', ['VECPIPE_FULL_ALL8_SOC_MAPPED_TIMING_FAIL'])
final_case = work / 'vecpipe-full-soc-loads-v1'
final = checked(final_case / 'summary.json', ['VECPIPE_FULL_ALL8_SOC_SDF_LOAD_REPAIR_PASS'])
conditions = checked(work / 'vecpipe-soc-conditions-v2/summary.json',
    ['VECPIPE_COMPLETE_SOC_BOUNDARY_LIBRARY_SDC_PHYSICAL_SRAM_AND_STA_AUDIT_PASS'])
assert final['area'] == conditions['area'] and conditions['physical_SRAMs'] == 128
assert min(conditions['setup_slack_ns'], conditions['hold_slack_ns']) >= 0
gate_prep = checked(work / 'vecpipe-full-soc-gate/v1/preparation.json',
    ['FULL_SOC_GATE_FIXTURES_AND_SDF_READY_EXECUTION_PENDING'])
assert local(gate_prep['source_case']) == final_case
assert gate_prep['sdf_adaptation']['source_sha256'] == sha(final_case / 'mapped.sdf')
target.mkdir()
(target / 'audit_driver.py').write_bytes(Path(__file__).read_bytes())
files.add(target / 'audit_driver.py')
result = {'status': 'VECPIPE_BENCHMARK_RTL_AND_SYNTHESIS_INPUT_LINEAGE_AUDIT_PASS',
          'observed_at': datetime.now(timezone.utc).isoformat(), 'benchmark_case': str(benchmark),
          'CPU_files_byte_equal_to_original_captured_conversion_tree': len(cpu_paths),
          'additional_control_sources_checked': len(control_sources),
          'backend_verbatim_snapshots': verbatim,
          'backend_specialization_only_defaults_and_recorded_array_link': True,
          'same_array_DDC_as_scalar_control_import_verified': True,
          'same_control_only_backend_callee_changed': True,
          'gate_source_linked_to_final_mapped_netlist_and_SDF': True,
          'area': final['area'], 'clock_period_ns': conditions['clock_period_ns'],
          'setup_slack_ns': conditions['setup_slack_ns'], 'hold_slack_ns': conditions['hold_slack_ns'],
          'source_sha256': {str(path): sha(path) for path in sorted(files)},
          'full_model_accepted_by_this_audit': False, 'gate_function_accepted_by_this_audit': False,
          'formal_equivalence_claimed': False, 'historical_missing_include_hashes_backfilled': False,
          'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Current byte identity of captured CPU/backend RTL sources,documented backend specialization and preserved same-array DDC import,complete-control conversion lineage and final gate source. Actual complete-model/gate execution and formal RTL-to-netlist equivalence are separate; this is not an extra inference run or final acceptance.'}
(target / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['status',
      'CPU_files_byte_equal_to_original_captured_conversion_tree', 'additional_control_sources_checked',
      'area', 'full_model_accepted_by_this_audit']}), flush=True)
