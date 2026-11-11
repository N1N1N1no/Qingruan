import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
"""Review completed mapped-SoC corruption proof without accepting normal run."""
import json
from pathlib import Path
import re
import sys

from gate_connectivity_audit import ROOT, BASE, SCRIPTS, need, sha
from gate_runtime_diagnostics import checked_runtime_text
sys.path.insert(0, str(SCRIPTS))
from run_pe_axi_sdf import compiled_runtime_files

case = ROOT / 'work/vecpipe-full-soc-gate/v1'
output = case / 'corrupt-review.json'
need(not output.exists(), 'Preserve prior review')


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def verify(hashes):
    for path, value in hashes.items():
        need(sha(local(path)) == value, 'Changed frozen input: ' + path)


prep = json.loads((case / 'preparation.json').read_text())
compiled = json.loads((case / 'compile-result.json').read_text())
annotation = json.loads((case / 'annotation-review.json').read_text())
record = json.loads((case / 'corrupt-result.json').read_text())
for hashes in [prep['source_sha256'], prep['prepared_sha256'], compiled['source_sha256'],
               annotation['source_sha256'], record['executor_source_sha256']]:
    verify(hashes)
need(annotation['status'] == 'ALL_PHYSICAL_SDF_ANNOTATION_AND_SOURCE_DIAGNOSTICS_PASS_RUNTIME_PENDING', 'Incomplete annotation')
need(compiled_runtime_files(case) == compiled['runtime_files'], 'Compiled runtime changed')
need(record['stage'] == 'corrupt' and record['status'] == 'GATE_STAGE_DIAGNOSTIC_CHECKS_PASS_ANNOTATION_ACCEPTANCE_OPEN', 'Incomplete corruption stage')
need(record['command']['returncode'] == 0 and record['runtime_files_unchanged'], 'Unexpected process/runtime result')
need(all(sha(case / name) == value for name, value in record['annotation_reports_sha256'].items()), 'Annotation reports changed')
text = (case / 'corrupt.log').read_text()
need(sha(case / 'corrupt.log') == record['log_sha256'], 'Runtime log changed')
expected_fatal = checked_runtime_text(case, 'corrupt', text, local)
trace = [list(map(int, match.groups())) for match in re.finditer(
    r'COMPLETE_SOC_ENGINE_DONE command=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', text)]
need(trace == prep['expected_RTL_engine_trace'][:4], 'Corruption must occur after the same first4 RTL commands')

# Guard the diagnostic exception against other locations, codes or additional
# fatal messages. These are in-memory log mutations, not changes to live files.
mutations = [text.replace('code=bad00120', 'code=bad00121'),
             text.replace('tb_soc.dmem.core: at time', 'tb_soc.imem.core: at time'),
             text.replace('COMPLETE_SOC_CPU_RETURN_FAIL code=bad00120', 'COMPLETE_SOC_CPU_RETURN_PASS'),
             text + '\nFatal: unrelated design failure\n']
for changed in mutations:
    try:
        checked_runtime_text(case, 'corrupt', changed, local)
    except AssertionError:
        pass
    else:
        raise AssertionError('Unexpected diagnostics were exempted')
paths = [Path(__file__), ROOT / 'gate_runtime_diagnostics.py', case / 'preparation.json',
         case / 'compile-result.json', case / 'annotation-review.json', case / 'corrupt-result.json',
         case / 'corrupt.log', case / 'inputs/axi_ram_model.sv', case / 'inputs/monitor.sv']
result = {'status': 'FULL_SOC_GATE_CORRUPTED_OUTPUT_CPU_DETECTION_PASS',
          'same_compiled_SDF_runtime_verified': True, 'completed_commands_before_detection': 4,
          'CPU_detected_corrupted_byte': 7, 'CPU_return_code': 'bad00120',
          'intentional_CPU_failure_termination': expected_fatal,
          'unexpected_diagnostics_or_timing_violations': 0, 'diagnostic_rejection_guards': len(mutations),
          'source_sha256': {str(path): sha(path) for path in paths},
          'normal_22_command_gate_function_accepted': False, 'full_image_inference_accepted': False,
          'official_accuracy_accepted': False, 'final_ADP_accepted': False,
          'scope': 'Completed mapped complete-SoC MAXIMUM-SDF corruption negative control only. Frozen monitor requires actual CPU bad00120 return, failure index8, stage4, exactly4 commands and injected=2. The one intentional failure-return $fatal is accepted only at its exact frozen RAM source callsite; other warnings/fatals/timing violations fail. Normal gate and full-image acceptance remain pending.'}
output.write_text(json.dumps(result, indent=2) + '\n')
print(result['status'], flush=True)
