#!/usr/bin/env python3
"""Run the public RTL starter in a fresh working copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

TASK = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if 'build' in rel.parts or '__pycache__' in rel.parts or '.git' in rel.parts or path.suffix == '.pyc' or rel.as_posix() == 'project/ucli.key':
            continue
        if path.is_symlink():
            raise RuntimeError('Source symlink is not supported: ' + str(path))
        if path.is_file():
            result[rel.as_posix()] = sha(path)
    return result


def run(command, cwd, log, env, summary, timeout):
    item = {'command': list(map(str, command)), 'exit_code': None, 'log': str(log)}
    summary['commands'].append(item)
    print('RUN ' + log.stem, flush=True)
    started = time.monotonic()
    with log.open('w') as handle:
        proc = subprocess.Popen(item['command'], cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while True:
                try:
                    item['exit_code'] = proc.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    elapsed = int(time.monotonic() - started)
                    print(f'RUNNING {log.stem} seconds={elapsed} LOG={log}', flush=True)
                    if elapsed > timeout:
                        raise RuntimeError('Step timeout: ' + str(log))
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            raise
    item['seconds'] = round(time.monotonic() - started, 3)
    if item['exit_code']:
        print('\n'.join(log.read_text(errors='replace').splitlines()[-25:]), flush=True)
        raise RuntimeError('Command failed; inspect ' + str(log))
    print('EXIT_CODE[' + log.stem + ']=0', flush=True)


def check_log(path, markers, count):
    text = path.read_text(errors='replace')
    for marker in markers:
        if marker not in text:
            raise RuntimeError('Missing marker ' + marker + ' in ' + str(path))
    if re.search(r'AXI_RX_SETUP_FAIL|AXI_RX_UNKNOWN|SOC_SMOKE_FAIL|^\s*(?:Error-\[|Fatal:|FATAL:)', text, re.M):
        raise RuntimeError('Failure in ' + str(path))
    found = sum(line.startswith('REFERENCE_SRAM_BEHAVIORAL_MODEL instance=') for line in text.splitlines())
    if found != count:
        raise RuntimeError(f'SRAM count {found}, expected {count}: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=TASK / 'base')
    parser.add_argument('--stage', choices=['rtl', 'prepare', 'firmware', 'generic'], default='rtl')
    parser.add_argument('--case', choices=['all', 'pe', 'delay0', 'delay1'], default='all')
    parser.add_argument('--output-root', type=Path, default=TASK / 'work')
    parser.add_argument('--timeout', type=int, default=7200)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output_root.resolve()
    safe = re.compile(r'^[A-Za-z0-9_./:+-]+$')
    if not safe.fullmatch(str(source)) or not safe.fullmatch(str(output)) or output.is_relative_to(source):
        raise RuntimeError('Use ASCII paths without spaces; output must be outside source')
    before = snapshot(source)
    is_baseline = source == (TASK / 'base').resolve()
    if is_baseline:
        expected = json.loads((TASK / 'SOURCE_MANIFEST.json').read_text())['base_files']
        if before != expected:
            raise RuntimeError('Starter changed; edit a separate copy and use --source')
    output.mkdir(parents=True, exist_ok=True)
    result = Path(tempfile.mkdtemp(prefix=args.stage + '.', dir=output))
    print('RESULT=' + str(result), flush=True)
    summary = {'status': 'RUNNING', 'scope': args.stage, 'baseline': is_baseline, 'source': str(source), 'commands': [], 'cases': []}
    try:
        working = result / 'source'
        shutil.copytree(source, working, ignore=shutil.ignore_patterns('build', '__pycache__', '*.pyc', '.git', 'ucli.key'), copy_function=shutil.copy)
        if snapshot(working) != before:
            raise RuntimeError('Working-copy input mismatch')
        save(result / 'inputs.json', before)
        project = working / 'project'
        vendor = working / 'vendor/Cores-VeeR-EL2'
        env = os.environ.copy()
        for key in ['MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKEFILES']:
            env.pop(key, None)
        env.update(LC_ALL='C', PYTHONDONTWRITEBYTECODE='1', RV_ROOT=str(vendor))
        prefix = env.get('RISCV_PREFIX', 'riscv-none-elf-')
        common = ['PREPARE_ARGS=--skip-config', 'RV_ROOT=' + str(vendor), 'PYTHON=' + sys.executable,
                  'RISCV_PREFIX=' + prefix, 'ISA=rv32imc_zicsr', 'VCS=' + str(TASK / 'scripts/vcs_vm.py'),
                  'SV2V=' + env.get('TASK1_SV2V', 'sv2v'), 'YOSYS=' + env.get('TASK1_YOSYS', 'yosys'), 'VCS_ARGS=', 'RUN_ARGS=+WAVE']
        make = shutil.which('make')
        if not make:
            raise RuntimeError('make not found')
        call = lambda cmd, label: run(cmd, project, result / (label + '.log'), env, summary, args.timeout)
        call([make, 'prepare', *common], 'prepare')
        if is_baseline:
            expected_lists = json.loads((TASK / 'SOURCE_MANIFEST.json').read_text())['source_lists']
            for name, expected in expected_lists.items():
                actual = (project / 'build' / name).read_text().replace(str(project), '$PROJECT').replace(str(vendor), '$VENDOR')
                if actual != expected:
                    raise RuntimeError('Compile order/configuration changed: ' + name)
        if args.stage in ['rtl', 'firmware']:
            for suffix in ['gcc', 'objcopy', 'objdump']:
                if not shutil.which(prefix + suffix):
                    raise RuntimeError('RISC-V tool not found: ' + prefix + suffix)
            call([make, '-B', 'sw', *common], 'firmware')
        if args.stage == 'generic':
            call([make, '-f', 'Makefile.frontend', 'frontend-synth-check', *common], 'generic_synthesis')
            reports = list((project / 'build/frontend_synth').glob('*/report.json'))
            if len(reports) != 1:
                raise RuntimeError('Expected one generic synthesis report')
            report = json.loads(reports[0].read_text())
            if report.get('pass') is not True or report.get('sram_blackbox_count') != 256:
                raise RuntimeError('Generic synthesis did not retain 256 SRAM macros')
        if args.stage == 'rtl':
            compat = project / 'build/pthread_yield_compat.o'
            call(['/usr/bin/gcc', '-fPIC', '-c', str(working / 'compat/pthread_yield_compat.c'), '-o', str(compat)], 'compatibility')
            common.append('COMPAT_OBJ=' + str(compat))
            firmware_hash = sha(project / 'build/program.hex')
            for label, delay in [('pe', None), ('delay0', 0), ('delay1', 1)]:
                if args.case not in ['all', label]:
                    continue
                case = result / label
                case.mkdir()
                started = time.time_ns()
                cmd = [make, '-f', 'Makefile.frontend', 'frontend-pe-test' if delay is None else 'frontend-run', *common]
                if delay is not None:
                    cmd.append('DELAY_NS=' + str(delay) + '.0')
                call(cmd, label)
                names = ['ram_compile.log', 'ram_test.log', 'pe_compile.log', 'pe_test.log'] if delay is None else ['compile.log', 'run.log', 'soc.vpd']
                for name in names:
                    path = project / 'build' / name
                    if not path.is_file() or path.stat().st_size == 0 or path.stat().st_mtime_ns < started:
                        raise RuntimeError('Missing/empty/stale artifact: ' + str(path))
                    shutil.copy2(path, case / name)
                if delay is None:
                    check_log(case / 'ram_test.log', ['WEIGHT_RAM_TEST_PASS'], 1)
                    check_log(case / 'pe_test.log', ['PE_AXI_TEST_PASS', 'AXI_RESPONSE_DELAY_NS=0.000 instance=tb_pe_axi.ram', 'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_pe_axi.ram'], 256)
                else:
                    markers = ['SOC_SMOKE_PASS: CPU checked all 64 PE output words']
                    for inst in ['imem', 'dmem']:
                        markers += [f'AXI_RESPONSE_DELAY_NS={delay:.3f} instance=tb_soc.{inst}', f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{inst}']
                    check_log(case / 'run.log', markers, 256)
                if sha(project / 'build/program.hex') != firmware_hash or snapshot(working) != before or snapshot(source) != before:
                    raise RuntimeError('Source/firmware changed during run')
                save(case / 'artifact_hashes.json', {p.name: sha(p) for p in case.iterdir() if p.is_file()})
                summary['cases'].append({'case': label, 'pass': True})
                print('RTL_CASE_PASS=' + label, flush=True)
        if snapshot(source) != before or snapshot(working) != before:
            raise RuntimeError('Source set changed during run')
        summary['status'] = 'PASS'
        if args.stage == 'rtl':
            print('TASK1_PUBLIC_RTL_' + ('ALL' if args.case == 'all' else args.case.upper()) + '_PASS')
        else:
            print('TASK1_PUBLIC_' + args.stage.upper() + '_PASS')
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        raise
    finally:
        save(result / 'summary.json', summary)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('TASK1_PUBLIC_RUN_FAILED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
