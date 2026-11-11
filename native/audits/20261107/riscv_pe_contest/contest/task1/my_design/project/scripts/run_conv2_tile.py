#!/usr/bin/env python3
"""Feed verified CPU Pool1 output into one four-position, 64-channel Conv2 tile."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
DESIGN = PROJECT.parent
TASK = DESIGN.parent
RESULT_WORDS = 5184


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def input_files():
    paths = []
    for name in ('vendor', 'project/rtl', 'project/snapshots', 'project/sw', 'project/tb'):
        paths.extend(p for p in (DESIGN / name).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    paths.extend(DESIGN / name for name in (
        'compat/pthread_yield_compat.c', 'project/tests/soc_conv2_tile_monitor.sv',
        'project/scripts/prepare.py', 'project/scripts/bin2hex.py',
        'project/scripts/prepare_conv1_point.py',
        'project/scripts/prepare_conv2_tile.py', 'project/scripts/audit_conv2_tile.py',
        'project/scripts/run_conv2_tile.py', 'project/scripts/run_conv2_tile.sh',
        'project/scripts/prepare_conv2_tile.sh',
    ))
    return sorted(set(paths))


def child_limit():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('vector', 'rtl'), default='rtl')
    parser.add_argument('--task2-root', type=Path, default=TASK.parent / 'task2')
    parser.add_argument('--conv1-result', type=Path)
    parser.add_argument('--conv1-pointer', type=Path, default=TASK / 'work/latest_conv1_full_result.json')
    parser.add_argument('--row', type=int, default=0)
    parser.add_argument('--col', type=int, default=0)
    parser.add_argument('--normal-only', action='store_true', help='additional positive spatial check only')
    parser.add_argument('--timeout', type=int, default=1200, help='seconds per EDA step')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    if any(value < 0 or value > 60 or value % 2 for value in (args.row, args.col)):
        parser.error('row and col must be even values in 0..60')
    work = TASK / 'work'
    work.mkdir(exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix='conv2_tile.', dir=work))
    print('RESULT=' + str(case), flush=True)
    summary = dict(status='RUNNING', stage=args.stage, cpu_included=args.stage == 'rtl',
                   full_second_layer=False, full_cnn_deployed=False, sdf_annotated=False,
                   clock_period_ns=10, row=args.row, col=args.col,
                   scope='Actual CPU Pool1 tensor -> Conv2 64 channels x 2x2 spatial positions -> ReLU/Q24/pool 64 values; separate firmware boot',
                   negative_controls_requested=not args.normal_only, commands=[], cases=[])
    env = os.environ.copy()
    for key in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKEFILES'):
        env.pop(key, None)
    env.update(LC_ALL='C', PYTHONDONTWRITEBYTECODE='1')
    active = None

    def interrupted(signum, frame):
        raise RuntimeError('Interrupted by signal ' + str(signum))

    signal.signal(signal.SIGTERM, interrupted)

    def run(command, folder, label, timeout=120, allow_failure=False):
        nonlocal active
        command = list(map(str, command))
        log = case / (label + '.log')
        record = dict(argv=command, cwd=str(folder), log=str(log), timeout_s=timeout)
        summary['commands'].append(record)
        save(case / 'summary.json', summary)
        print('CONV2_TILE_PHASE=' + label, flush=True)
        started = time.monotonic()
        offset = 0
        with log.open('w') as stream:
            active = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                      stdout=stream, stderr=subprocess.STDOUT,
                                      start_new_session=True, preexec_fn=child_limit)
            try:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise RuntimeError('Timeout: ' + str(log))
                    try:
                        code = active.wait(timeout=min(20, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        print(f'RUNNING {label} seconds={int(time.monotonic()-started)} LOG={log}', flush=True)
                        if log.stat().st_size > 64 * 1024**2:
                            raise RuntimeError('Too many diagnostics: ' + str(log))
                        with log.open(errors='replace') as progress:
                            progress.seek(offset)
                            contents = progress.read()
                            offset = progress.tell()
                        for line in contents.splitlines():
                            if 'CONV2_TILE_PROGRESS' in line:
                                print(line, flush=True)
            finally:
                if active.poll() is None:
                    try:
                        os.killpg(active.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        active.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(active.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        active.wait(timeout=5)
                active = None
        record.update(returncode=code, elapsed_s=round(time.monotonic() - started, 3))
        save(case / 'summary.json', summary)
        contents = log.read_text(errors='replace')
        if code and not allow_failure:
            print('\n'.join(contents.splitlines()[-20:]), flush=True)
            raise RuntimeError('Command failed: ' + str(log))
        return code, contents

    try:
        original = {str(p): sha(p) for p in input_files()}
        frozen = case / 'source'
        for name, digest in original.items():
            source = Path(name)
            target = frozen / source.relative_to(DESIGN)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if sha(target) != digest:
                raise RuntimeError('Source changed while freezing: ' + name)
        summary['input_sha256'] = original
        summary['frozen_source_sha256'] = {str(Path(p).relative_to(DESIGN)): d for p, d in original.items()}
        starter = json.loads((TASK / 'SOURCE_MANIFEST.json').read_text())['base_files']
        baseline = {p: d for p, d in summary['frozen_source_sha256'].items()
                    if p.startswith('project/rtl/') or p == 'project/sw/pe_smoke.c'}
        summary['rtl_and_smoke_match_starter'] = all(starter.get(p) == d for p, d in baseline.items())
        if not summary['rtl_and_smoke_match_starter']:
            raise RuntimeError('Baseline RTL or smoke source changed')
        project = frozen / 'project'
        vector_dir = case / 'vector'
        vector_python = env.get('TASK2_PYTHON', str(Path.home() / 'miniforge3/envs/task2-cnn/bin/python'))
        vector_command = [vector_python, project / 'scripts/prepare_conv2_tile.py',
                          '--task2-root', args.task2_root.resolve(), '--output-dir', vector_dir,
                          '--row', args.row, '--col', args.col]
        if args.conv1_result:
            vector_command += ['--conv1-result', args.conv1_result.resolve()]
        else:
            vector_command += ['--conv1-pointer', args.conv1_pointer.resolve()]
        run(vector_command, case, 'prepare_from_actual_pool1', 300)
        vector = json.loads((vector_dir / 'conv2_tile.json').read_text())
        summary['vector'] = vector
        summary['source_conv1_result'] = vector['source_conv1_result']
        if args.stage == 'rtl':
            # Test the handoff guard without altering the verified source run.
            parent = Path(vector['source_conv1_result'])
            parent_summary = json.loads((parent / 'summary.json').read_text())
            bad_pointer = dict(status='PASS', full_first_layer=True, result=str(parent),
                               summary_sha256='0' * 64,
                               independent_audit_sha256=sha(parent / 'independent_validation_summary.json'),
                               results_sha256=sha(parent / 'RESULTS.md'),
                               actual_output=parent_summary['observed_output'],
                               actual_output_sha256=parent_summary['observed_output_sha256'])
            bad_pointer_path = case / 'wrong_source_pointer.json'
            save(bad_pointer_path, bad_pointer)
            source_check = [vector_python, project / 'scripts/prepare_conv2_tile.py',
                            '--task2-root', args.task2_root.resolve(), '--output-dir', case / 'rejected_source_vector',
                            '--conv1-pointer', bad_pointer_path, '--row', args.row, '--col', args.col]
            code, source_log = run(source_check, case, 'wrong_source_guard', 300, allow_failure=True)
            if code == 0 or 'CONV2_TILE_PREPARE_FAILED: Conv1 pointer provenance mismatch: summary_sha256' not in source_log:
                raise RuntimeError('A source pointer with an incorrect hash was not rejected')
            summary['wrong_source_guard_rejected'] = True
            run([sys.executable, project / 'scripts/prepare.py', '--skip-config',
                 '--rv-root', frozen / 'vendor/Cores-VeeR-EL2'], project, 'prepare_frozen_rtl')
            prefix = env.get('RISCV_PREFIX', 'riscv-none-elf-')
            for suffix in ('gcc', 'objcopy', 'objdump', 'nm'):
                if not shutil.which(prefix + suffix):
                    raise RuntimeError('Missing RISC-V tool: ' + prefix + suffix)

            def firmware(name, injection):
                folder = case / (name + '_firmware')
                folder.mkdir()
                command = [prefix + 'gcc', '-march=rv32imc_zicsr', '-mabi=ilp32', '-O3',
                           '-ffreestanding', '-fno-builtin', '-fno-pic', '-msmall-data-limit=0',
                           '-nostdlib', '-nostartfiles', '-Wall', '-Wextra', '-Werror',
                           '-Wl,--no-relax', '-Wl,-T,' + str(project / 'sw/link.ld'),
                           '-Wl,-Map,' + str(folder / 'program.map'), '-I' + str(vector_dir)]
                if injection:
                    command.append('-D' + injection + '=1')
                run([*command, project / 'sw/start.S', project / 'sw/conv2_tile.c',
                     '-o', folder / 'program.elf'], project, name + '_gcc')
                run([prefix + 'objcopy', '-O', 'binary', folder / 'program.elf', folder / 'program.bin'],
                    project, name + '_objcopy')
                run([sys.executable, project / 'scripts/bin2hex.py', folder / 'program.bin', folder / 'program.hex'],
                    project, name + '_hex')
                _, symbols = run([prefix + 'nm', '-S', folder / 'program.elf'], project, name + '_symbols')
                match = re.findall(r'^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+[Bb]\s+conv2_tile_results$', symbols, re.M)
                if len(match) != 1 or int(match[0][1], 16) != RESULT_WORDS * 4:
                    raise RuntimeError('Cannot locate the 5184-word actual CPU result array')
                address = int(match[0][0], 16)
                if address % 4 or not 0x80000000 <= address < address + RESULT_WORDS * 4 <= 0x8003eff0:
                    raise RuntimeError('CPU results overlap RAM or stack limits')
                _, disassembly = run([prefix + 'objdump', '-d', folder / 'program.elf'], project, name + '_objdump')
                (folder / 'program.dis').write_text(disassembly)
                return name, folder, address

            variants = [('normal', None)]
            if not args.normal_only:
                variants += [('wrong_weight', 'C2T_INJECT_WEIGHT_ERROR'),
                             ('wrong_input', 'C2T_INJECT_INPUT_ERROR'),
                             ('wrong_requant', 'C2T_INJECT_REQUANT_ERROR'),
                             ('wrong_pool', 'C2T_INJECT_POOL_ERROR')]
            firmware_cases = [firmware(name, injection) for name, injection in variants]
            models = case / 'models'
            models.mkdir()
            original_ram = (project / 'tb/axi_ram_model.sv').read_text()
            replacements = {'SOC_SMOKE_PASS: CPU checked all 64 PE output words': 'CONV2_TILE_CPU_RETURN_PASS',
                            'SOC_SMOKE_FAIL code=%08x': 'CONV2_TILE_CPU_RETURN_FAIL code=%08x'}
            ram = original_ram
            for old, new in replacements.items():
                if ram.count(old) != 1:
                    raise RuntimeError('RAM diagnostic adaptation no longer matches the source')
                ram = ram.replace(old, new)
            ram_model = models / 'axi_ram_model.sv'
            ram_model.write_text(ram)
            summary['ram_diagnostic_adaptation'] = dict(source_sha256=sha(project / 'tb/axi_ram_model.sv'),
                                                       copy_sha256=sha(ram_model), changes=replacements, logic_changed=False)
            compat = case / 'pthread_yield_compat.o'
            run(['/usr/bin/gcc', '-fPIC', '-c', frozen / 'compat/pthread_yield_compat.c', '-o', compat], case, 'host_compatibility')
            adapter = case / 'vcs_vm.py'
            shutil.copyfile(TASK / 'scripts/vcs_vm.py', adapter)
            summary['vcs_adapter_sha256'] = sha(adapter)
            simulation = case / 'rtl'
            simulation.mkdir()
            _, compile_log = run([
                sys.executable, adapter, '-full64', '-sverilog', '-timescale=1ns/1ps', '-debug_access',
                '+define+VCS', '+define+PE_REFERENCE_MACRO', '+define+AXI_RX_TIMING_CHECK',
                '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0', '-assert', 'svaext',
                '-f', project / 'build/rtl.f', project / 'tb/sram_reference_model.v', ram_model,
                project / 'tb/tb_soc.sv', project / 'tests/soc_conv2_tile_monitor.sv',
                '-top', 'tb_soc', '-top', 'soc_conv2_tile_monitor',
                '-Mdir=' + str(simulation / 'csrc'), '-o', simulation / 'simv', '-LDFLAGS', compat,
            ], simulation, 'rtl_compile', args.timeout)
            if re.search(r'^\s*(?:Error-\[|Fatal:|FATAL:)', compile_log, re.M):
                raise RuntimeError('Compiler reported an error')
            executable_digest = sha(simulation / 'simv')
            expected_binary = (vector_dir / 'cpu_results.bin').read_bytes()
            if len(expected_binary) != RESULT_WORDS * 4:
                raise RuntimeError('Unexpected host result file length')
            for name, folder, address in firmware_cases:
                observed = simulation / (name + '_results.bin')
                trace = simulation / (name + '_trace.tsv')
                negative = name != 'normal'
                code, log = run([simulation / 'simv', '+MEM_HEX=' + str(folder / 'program.hex'),
                                 '+INPUT_FILE=' + str(vector_dir / 'conv2_input.bin'),
                                 '+WEIGHT_FILE=' + str(vector_dir / 'weights_hw.bin'),
                                 '+GOLDEN_FILE=' + str(vector_dir / 'cpu_results.bin'),
                                 f'+RESULT_ADDR={address:08x}', '+OUTPUT_ROW=' + str(args.row),
                                 '+OUTPUT_COL=' + str(args.col), '+OBSERVED_FILE=' + str(observed),
                                 '+TRACE=' + str(trace), '+MAX_CYCLES=6000000'],
                                simulation, name + '_simulation', args.timeout, allow_failure=negative)
                if sha(simulation / 'simv') != executable_digest:
                    raise RuntimeError('Simulator changed between positive and negative cases')
                record = dict(case=name, expected_failure=negative, returncode=code,
                              trace=str(trace), trace_sha256=sha(trace), result_address=f'0x{address:08x}')
                if negative:
                    reason = {'wrong_weight': 'CONV2_TILE_MONITOR_FAIL weight',
                              'wrong_input': 'CONV2_TILE_MONITOR_FAIL input',
                              'wrong_requant': 'CONV2_TILE_MONITOR_FAIL CPU requant',
                              'wrong_pool': 'CONV2_TILE_MONITOR_FAIL CPU pool'}[name]
                    if 'Fatal:' not in log or reason not in log or 'CONV2_TILE_MONITOR_PASS' in log or 'CONV2_TILE_CPU_RETURN_PASS' in log:
                        raise RuntimeError('Negative control failed for unexpected reason: ' + name)
                else:
                    match = re.findall(r'^CONV2_TILE_MONITOR_PASS (.*)$', log, re.M)
                    if len(match) != 1 or log.count('CONV2_TILE_CPU_RETURN_PASS') != 1:
                        raise RuntimeError('Missing independent monitor/CPU completion')
                    if re.search(r'CONV2_TILE_.*FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|^\s*(?:Error-\[|Fatal:|FATAL:)', log, re.M):
                        raise RuntimeError('Normal simulation has a failure diagnostic')
                    if len(re.findall(r'^REFERENCE_SRAM_BEHAVIORAL_MODEL instance=', log, re.M)) != 256:
                        raise RuntimeError('Expected 256 actual SRAM instances')
                    for instance in ('imem', 'dmem'):
                        if f'AXI_RESPONSE_DELAY_NS=1.000 instance=tb_soc.{instance}' not in log or \
                           f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{instance}' not in log:
                            raise RuntimeError('External RAM receive checks are inactive')
                    if observed.read_bytes() != expected_binary:
                        raise RuntimeError('Actual CPU RAM dump differs from expected results')
                    record.update(monitor=match[0], observed_results=str(observed), observed_results_sha256=sha(observed),
                                  accepted_cpu_results=RESULT_WORDS, pe_writes=6978, pe_reads=4608)
                    summary.update(normal_monitor_result=match[0], pe_writes=6978, pe_reads=4608,
                                   accepted_cpu_results=RESULT_WORDS, raw_outputs=256, requant_outputs=256, pool_outputs=64,
                                   observed_results=str(observed), observed_results_sha256=sha(observed))
                record['pass'] = True
                summary['cases'].append(record)
                save(case / 'summary.json', summary)
            summary.update(simulator_sha256=executable_digest,
                           negative_controls_detected=[name for name, _ in variants if name != 'normal'])
            save(case / 'summary.json', summary)
            run([vector_python, project / 'scripts/audit_conv2_tile.py', '--case', case], case, 'independent_audit', 300)
            audit = json.loads((case / 'independent_validation_summary.json').read_text())
            if audit.get('status') != 'PASS':
                raise RuntimeError('Independent Conv2 tile audit failed')
            summary['independent_audit'] = audit
        for name, digest in original.items():
            if sha(name) != digest or sha(frozen / Path(name).relative_to(DESIGN)) != digest:
                raise RuntimeError('Live/frozen source changed: ' + name)
        for metadata in vector.get('provenance', {}).values():
            if 'path' in metadata and sha(metadata['path']) != metadata['sha256']:
                raise RuntimeError('Model or upstream input changed: ' + metadata['path'])
        summary['artifacts_sha256'] = {str(p.relative_to(case)): sha(p) for p in case.rglob('*')
            if p.is_file() and (p.parent == vector_dir or p.name in ('program.elf', 'program.bin', 'program.hex'))}
        summary['status'] = 'PASS'
        (case / 'RESULTS.md').write_text(
            '# 第一层实际输出接入第二层：' + ('CPU/PE RTL 验证通过' if args.stage == 'rtl' else '输入与标准答案已准备') + '\n\n'
            + f"输入来自已通过验证的第一层：`{summary['source_conv1_result']}`，使用其 CPU 实际输出的 32×64×64 个字节。\n\n"
            + f'本次计算第二层全部 64 个通道，在 ({args.row}, {args.col}) 起的 2×2 卷积区域：每个输出需 32×3×3＝288 项，分 18 批交给 16 行 PE。共得到 256 个含偏置卷积值，经过 CPU ReLU、Q24 量化和最大池化，得到 64 个池化值。\n\n'
            + ('独立检查 6978 次 PE 写入、4608 次 PE 读回，以及 CPU 保存的全部 5184 个中间和最终结果。第一层来源哈希错误会被拒绝。'
               + ('权重、输入、量化、池化四种错误负控均被正确拒绝。\n\n' if not args.normal_only else '本次为补充位置的正常计算检查。\n\n')
               if args.stage == 'rtl' else '标准答案与独立整层 PyTorch 卷积核对；尚未运行 CPU。\n\n')
            + '这是两个固件运行之间的数据交接：从前次第一层实际输出生成第二层输入头文件，再由新固件计算。尚未在同一次启动中连续执行两层；完整第二层、后续层与最终分类也尚未完成。本次为 RTL 功能验证，未运行新程序的 SDF。\n', encoding='utf-8')
        save(case / 'summary.json', summary)
        print('CONV2_TILE_' + args.stage.upper() + '_PASS', flush=True)
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(case / 'summary.json', summary)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('CONV2_TILE_RUN_FAILED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
