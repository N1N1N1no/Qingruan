"""Compile/run the prepared complete all8 mapped SoC with active MAXIMUM SDF.

Stages deliberately stop short of acceptance: annotation diagnostics require
source review and exact count validation before a gate/SDF PASS can be issued.
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
import sys
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
PROJECT = TASK / 'my_design/project'
sys.path.insert(0, str(PROJECT / 'scripts'))
from run_pe_axi_sdf import compiled_runtime_files

parser = argparse.ArgumentParser()
parser.add_argument('--stage', choices=['compile', 'normal', 'corrupt', 'timing-probe'], required=True)
parser.add_argument('--revision', default='v3')
args = parser.parse_args()
assert re.fullmatch(r'[A-Za-z0-9_-]+', args.revision)
case = ROOT / 'work/full-soc-gate/R8_C16_L4/shared' / args.revision
preparation = json.loads((case / 'preparation.json').read_text())
assert preparation['status'] == 'FULL_SOC_GATE_FIXTURES_AND_SDF_READY_EXECUTION_PENDING'


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(hashes):
    for name, wanted in hashes.items():
        assert sha(local(name)) == wanted, name


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))


def run(argv, label, timeout):
    argv = list(map(str, argv))
    started = time.monotonic()
    print('FULL_SOC_GATE_PHASE ' + label, flush=True)
    with (case / (label + '.log')).open('w') as log:
        process = subprocess.Popen(argv, cwd=case, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=limits)
        (case / 'live-process.json').write_text(json.dumps({
            'stage': label, 'status': 'RUNNING', 'pid': process.pid,
            'argv': argv}, indent=2) + '\n')
        try:
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('FULL_SOC_GATE_RUNNING ' + label + ' elapsed_s=%d' %
                          (time.monotonic() - started), flush=True)
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(label)
                    if log.tell() > 256 * 1024**2:
                        raise RuntimeError('Excessive diagnostics; inspect ' + label)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
            (case / 'live-process.json').write_text(json.dumps({
                'stage': label, 'status': 'TERMINAL', 'returncode': process.returncode},
                indent=2) + '\n')
    record = {'stage': label, 'argv': argv, 'returncode': code,
              'elapsed_s': time.monotonic() - started}
    (case / (label + '-command.json')).write_text(json.dumps(record, indent=2) + '\n')
    return record, (case / (label + '.log')).read_text(errors='replace')


verify(preparation['source_sha256'])
verify(preparation['prepared_sha256'])
if args.stage == 'compile':
    assert not (case / 'compile.log').exists(), 'Preserve previous compile attempt'
    deps = [Path(__file__), case / 'preparation.json', TASK / 'scripts/vcs_vm.py',
            TASK / 'my_design/compat/pthread_yield_compat.c',
            PROJECT / 'scripts/run_pe_axi_sdf.py']
    hashes = {**preparation['source_sha256'], **preparation['prepared_sha256'],
              **{str(path): sha(path) for path in deps}}
    (case / 'execution-preparation.json').write_text(json.dumps({
        'status': 'FULL_SOC_GATE_COMPILE_PREPARED', 'source_sha256': hashes,
        'clock_period_ns': 10, 'SDF_triplet': 'MAXIMUM',
        'specify_and_timing_checks_enabled': True,
        'scope': 'Compilation only, all annotation/runtime diagnostics need review.'
    }, indent=2) + '\n')
    record, text = run(['/usr/bin/gcc', '-fPIC', '-c',
                        TASK / 'my_design/compat/pthread_yield_compat.c',
                        '-o', case / 'compat.o'], 'compat', 120)
    assert record['returncode'] == 0
    inputs = case / 'inputs'
    argv = ['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py',
            '-full64', '-sverilog', '-timescale=1ns/1fs', '-debug_access+all',
            '-assert', 'svaext', '+define+VCS', '+define+TETRAMAX+NTC+RECREM',
            '-negdelay', '+neg_tchk', '+sdfverbose', '+sdfsummary',
            '+define+AXI_RX_TIMING_CHECK', '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0',
            '+incdir+' + str(PROJECT / 'snapshots/soc'),
            '-top', 'tb_soc', '-top', 'complete_soc_monitor', '-top', 'full_soc_sdf_observer',
            '-Mdir=' + str(case / 'csrc'), '-o', case / 'simv', '-LDFLAGS', case / 'compat.o',
            inputs / 'soc_mapped.v', case / 'models/standard_model.v', case / 'models/sram_model.v',
            inputs / 'axi_ram_model.sv', inputs / 'tb_soc.sv', inputs / 'monitor.sv', inputs / 'observer.sv']
    record, text = run(argv, 'compile', 1800)
    verify(hashes)
    if record['returncode'] != 0 or re.search(r'(?m)^\s*(?:Error-\[|Fatal:)', text):
        (case / 'compile-failure.json').write_text(json.dumps({
            'status': 'GATE_COMPILE_FAILED_NO_EXECUTION', 'command': record,
            'source_sha256': hashes}, indent=2) + '\n')
        raise RuntimeError(str(case / 'compile.log'))
    result = {'status': 'GATE_SDF_COMPILED_ANNOTATION_REVIEW_AND_EXECUTION_PENDING',
              'source_sha256': hashes, 'command': record,
              'runtime_files': compiled_runtime_files(case),
              'annotation_reviewed': False, 'gate_function_accepted': False}
    (case / 'compile-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(result['status'], flush=True)
else:
    compiled = json.loads((case / 'compile-result.json').read_text())
    assert compiled['status'] == 'GATE_SDF_COMPILED_ANNOTATION_REVIEW_AND_EXECUTION_PENDING'
    verify(compiled['source_sha256'])
    assert compiled_runtime_files(case) == compiled['runtime_files']
    assert not (case / (args.stage + '.log')).exists(), 'Preserve previous run'
    annotation_hashes = {name: sha(case / name) for name in ['sdfAnnotateInfo', 'sdf_annotation.log']}
    argv = [case / 'simv']
    for arg in preparation['original_RTL_normal_argv'][1:]:
        if arg.startswith('+MEM_HEX='):
            argv.append('+MEM_HEX=' + str(case / 'inputs/program.hex'))
        elif arg.startswith('+REPORT='):
            argv.append('+REPORT=' + str(case / (args.stage + '-report.txt')))
        else:
            argv.append(arg)
    if args.stage == 'corrupt':
        argv.append('+INJECT_VECTOR_ERROR')
    elif args.stage == 'timing-probe':
        argv.append('+FULL_SOC_SDF_TIMING_PROBE')
    record, text = run(argv, args.stage, 3600)
    verify(compiled['source_sha256'])
    assert compiled_runtime_files(case) == compiled['runtime_files']
    assert all(sha(case / name) == wanted for name, wanted in annotation_hashes.items())
    if args.stage == 'normal':
        assert record['returncode'] == 0 and 'COMPLETE_SOC_PASS commands=22' in text
        assert 'COMPLETE_SOC_CPU_RETURN_PASS' in text and 'FULL_SOC_SDF_DELAY_OBSERVED' in text
        assert not re.search(r'(?i)timing[_ ]violation|Fatal:|Error-\[', text)
        trace = [list(map(int, match.groups())) for match in re.finditer(
            r'COMPLETE_SOC_ENGINE_DONE command=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', text)]
        assert trace == preparation['expected_RTL_engine_trace'], 'Gate engine trace differs from RTL'
        assert (case / 'normal-report.txt').read_bytes() == (local(preparation['gold_RTL_case']) / 'report.txt').read_bytes(), 'Gate CPU/call cycles differ from RTL'
    elif args.stage == 'corrupt':
        assert 'COMPLETE_SOC_NEGATIVE_PASS CPU_detected_corrupted_byte=7' in text
        assert 'COMPLETE_SOC_CPU_RETURN_FAIL code=bad00120' in text and 'COMPLETE_SOC_CPU_RETURN_PASS' not in text
        assert not re.search(r'(?i)timing[_ ]violation', text)
    else:
        assert record['returncode'] == 0 and 'FULL_SOC_SDF_TIMING_PROBE_DONE' in text
        assert 'Timing violation' in text and 'SRAM_TIMING_VIOLATION' in text
    result = {'status': 'GATE_STAGE_DIAGNOSTIC_CHECKS_PASS_ANNOTATION_ACCEPTANCE_OPEN',
              'stage': args.stage, 'command': record,
              'runtime_files_unchanged': True, 'annotation_reports_sha256': annotation_hashes,
              'log_sha256': sha(case / (args.stage + '.log')),
              'annotation_reviewed': False, 'full_gate_SDF_acceptance': False,
              'official_accuracy_accepted': False,
              'scope': 'Stage checks only. A separate exact SDF annotation/diagnostic review and the full required stage evidence are still mandatory.'}
    (case / (args.stage + '-result.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(result['status'] + ' stage=' + args.stage, flush=True)
