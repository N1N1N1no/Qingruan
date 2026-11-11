# Isolated simulator scheduling experiment: four RTL evaluation threads.
# Original driver, RTL, firmware, formal arithmetic proof and running jobs are unchanged.
"""Crosscheck original CPU+PE RTL in local Verilator against frozen VCS.

The entire accepted-transaction trace (including cycles) must be byte-exact.
This is a qualification for the original full-model runner, not model timing.
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

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
gold = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/work/soc_sdf.u63n3r7w'
parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v2')
parser.add_argument('--arithmetic-proof', type=Path)
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / ('work/original-baseline-verilator-crosscheck-' + args.revision)
assert not case.exists(), 'Preserve prior attempt'
proof = json.loads((gold / 'summary.json').read_text())
assert proof['status'] == 'PASS' and proof['output_traces_match']


def local(path):
    return BASE / path[11:] if path.startswith('/workspace/') else Path(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


for path, value in proof['source_tree_sha256'].items():
    assert sha(local(path)) == value, path
for item in ['rtl/trace.tsv', 'rtl/run.log', 'rtl/compile.log']:
    assert sha(gold / item) == proof['output_sha256'][item]
case.mkdir()
arithmetic = None
arithmetic_files = []
if args.arithmetic_proof:
    arithmetic_dir = args.arithmetic_proof.resolve()
    arithmetic = json.loads((arithmetic_dir / 'summary.json').read_text())
    assert arithmetic['status'] == 'ORIGINAL_SIGNED_MAGNITUDE_PRODUCT_AND_CLA33_ALL_BINARY_INPUTS_FORMAL_PASS'
    for path, expected in arithmetic['source_sha256'].items():
        assert sha(local(path)) == expected, path
    for item, expected in arithmetic['artifact_sha256'].items():
        assert sha(arithmetic_dir / item) == expected, item
    arithmetic_files = [arithmetic_dir / 'summary.json', *(arithmetic_dir / item for item in arithmetic['artifact_sha256'])]
compile_argv = next(command['argv'] for command in proof['commands'] if command['phase'] == 'rtl_compile')
file_index = compile_argv.index('-f')
source_list = local(compile_argv[file_index + 1])
lines, sources = [], []
for line in source_list.read_text().splitlines():
    if line.startswith('/'):
        source = local(line)
        sources.append(source)
        if arithmetic and source.name in ['Exact_8bit.v', 'adder.v']:
            assert any(Path(path).name == source.name and expected == sha(source)
                       for path, expected in arithmetic['source_sha256'].items())
            if source.name == 'Exact_8bit.v':
                lines.extend(str(arithmetic_dir / item) for item in ['original_gates.v', 'simulation_arithmetic.v'])
        else:
            lines.append(str(source))
    elif line.startswith('+incdir+'):
        directory = local(line[len('+incdir+'):])
        lines.append('+incdir+' + str(directory))
        sources.extend(path for path in directory.rglob('*') if path.suffix in ['.vh', '.svh'])
    else:
        lines.append(line)
(case / 'rtl.f').write_text('\n'.join(lines) + '\n')
extra = []
for argument in compile_argv[file_index + 2:]:
    source = local(argument)
    if source.suffix not in ['.sv', '.v']:
        continue
    target = case / source.name
    text = source.read_text()
    if source.name == 'axi_ram_model.sv':
        assert text.count('#0;') == 1
        text = text.replace('#0;', '#0.001;')
    target.write_text(text)
    extra.append(target)
wrapper = case / 'crosscheck_top.sv'
wrapper.write_text('module crosscheck_top; tb_soc tb_soc(); soc_program_monitor soc_program_monitor(); endmodule\n')
package = ROOT / 'toolchain-cache/verilator-5.032/usr'
tool = package / 'bin/verilator'
environment = dict(os.environ, VERILATOR_ROOT=str(package / 'share/verilator'))
files = [Path(__file__), gold / 'summary.json', gold / 'rtl/trace.tsv', gold / 'rtl/run.log',
         ROOT / 'toolchain-cache/verilator-provenance.json', tool, package / 'bin/verilator_bin',
         source_list, case / 'rtl.f', wrapper, *sources, *extra,
         *(local(path) for path in proof['source_tree_sha256']), *arithmetic_files]
hashes = {str(path): sha(path) for path in dict.fromkeys(files)}
commands = []


def run(argv, phase, timeout):
    argv = list(map(str, argv))
    start = time.monotonic()
    print('ORIGINAL_BASELINE_CROSSCHECK_PHASE ' + phase, flush=True)

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))

    with (case / (phase + '.log')).open('w') as stream:
        child = subprocess.Popen(argv, cwd=case, env=environment, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=limits)
        (case / 'live-process.json').write_text(json.dumps({'status': 'RUNNING', 'phase': phase,
             'pid': child.pid, 'argv': argv}, indent=2) + '\n')
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('ORIGINAL_BASELINE_CROSSCHECK_RUNNING %s elapsed_s=%d' %
                          (phase, time.monotonic() - start), flush=True)
                    if time.monotonic() - start > timeout:
                        raise TimeoutError(phase)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
            (case / 'live-process.json').write_text(json.dumps({'status': 'TERMINAL', 'phase': phase,
                'returncode': child.returncode}, indent=2) + '\n')
    commands.append({'phase': phase, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - start})
    assert code == 0, str(case / (phase + '.log'))
    return (case / (phase + '.log')).read_text(errors='replace')


run([tool, '--binary', '--timing', '--assert', '-Wno-fatal', '--threads', '4', '--build-jobs', '2',
     '-CFLAGS', '-O3 -march=native', '--top-module', 'crosscheck_top', '--Mdir', case / 'obj_dir',
     '-o', 'original_smoke_simv', '+define+PE_REFERENCE_MACRO', '+define+AXI_RX_TIMING_CHECK',
     '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0', '-f', case / 'rtl.f', *extra, wrapper], 'compile', 1800)
executable = case / 'obj_dir/original_smoke_simv'
simulator_hash = sha(executable)
original_argv = next(command['argv'] for command in proof['commands'] if command['phase'] == 'rtl_simulate')
argv = [executable]
for argument in original_argv[1:]:
    if argument.startswith('+MEM_HEX='):
        argv.append('+MEM_HEX=' + str(local(argument.split('=', 1)[1])))
    elif argument.startswith('+TRACE='):
        argv.append('+TRACE=' + str(case / 'trace.tsv'))
    else:
        argv.append(argument)
text = run(argv, 'normal', 300)
assert 'SOC_SMOKE_PASS: CPU checked all 64 PE output words' in text
assert 'SOC_PROGRAM_MONITOR_PASS pe_reads=64 pe_writes=87 pe_responses=87' in text
assert not re.search(r'(?i)Fatal:|Error-\[|MONITOR_FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)', text)
assert (case / 'trace.tsv').read_bytes() == (gold / 'rtl/trace.tsv').read_bytes(), 'Accepted payloads/cycles differ from VCS'
for path, value in hashes.items():
    assert sha(Path(path)) == value, path
assert sha(executable) == simulator_hash
result = {'status': 'ORIGINAL_CPU_PE_VERILATOR_VCS_COMPLETE_TRANSACTION_TRACE_AND_CYCLES_PASS',
          'reference_PE_output_words_checked': 64, 'PE_config_writes_checked': 87,
          'complete_accepted_transaction_trace_byte_identical': True,
          'source_sha256': hashes, 'simulator_sha256': simulator_hash, 'commands': commands,
          'trace_sha256': sha(case / 'trace.tsv'), 'clock_period_ns': 10, 'RAM_response_delay_ns': 1,
          'adaptation': 'Isolated RAM completion #0 delta ->1ps, before next clock. Same source/payload/cycle trace. C++ -O3 native compilation affects simulation implementation only.',
          'original_full_model_inference_accepted': False, 'before_after_ADP_accepted': False,
          'formally_proved_combinational_adapter_used': arithmetic is not None,
          'arithmetic_equivalence_summary': str(arithmetic_dir / 'summary.json') if arithmetic else None,
          'scope': 'Original full CPU/router/16x16 four-plane PE RTL on the task1 C smoke, optionally with the explicitly recorded all-input-formal combinational simulation adapter. All accepted IFU/memory/PE payloads and cycle numbers exactly match recorded original unadapted VCS. No register, memory, clock or control changes. This qualifies the local simulator/adapter for further baseline testing; it is not original full-model runtime or organizer accuracy.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(result['status'], flush=True)
