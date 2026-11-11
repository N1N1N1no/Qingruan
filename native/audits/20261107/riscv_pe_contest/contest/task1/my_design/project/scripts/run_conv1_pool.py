#!/usr/bin/env python3
"""Run a real Conv1 tile followed by CPU ReLU, Q24 requantization and pooling."""
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
MEMORY_LIMIT = 8 * 1024**3


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


def input_files():
    files = []
    for directory in ('vendor', 'project/rtl', 'project/snapshots', 'project/sw', 'project/tb'):
        files.extend(p for p in (DESIGN / directory).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    files.extend(DESIGN / name for name in (
        'compat/pthread_yield_compat.c',
        'project/tests/soc_conv1_pool_monitor.sv',
        'project/scripts/prepare.py', 'project/scripts/bin2hex.py',
        'project/scripts/prepare_conv1_point.py', 'project/scripts/prepare_conv1_tile.py', 'project/scripts/prepare_conv1_pool.py', 'project/scripts/run_conv1_pool.py',
        'project/scripts/run_conv1_pool.sh', 'project/scripts/prepare_conv1_pool.sh',
    ))
    return sorted(set(files))


def child_limit():
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))


def all_result_goldens(vector):
    return ([v for batch in vector['partial_golden'] for v in batch]
            + vector['accumulator_golden'] + vector['requant_golden'] + vector['pool_golden']
            + vector['quant_case_golden'] + vector['pool_case_golden'])


def inspect_trace(path, vector, mode='normal'):
    """Match accepted CPU configuration and intermediate/final results."""
    lines = path.read_text().splitlines()
    if not lines or lines[0] != 'CONV1_POOL_TRACE_V1':
        raise RuntimeError('Missing transaction trace header')
    events = []
    for line in lines[1:]:
        columns = line.split('\t')
        if len(columns) < 3:
            raise RuntimeError('Malformed transaction trace')
        events.append((columns[1], dict(item.split('=', 1) for item in columns[2:])))
    writes = [(0, 0), (8, 0xffff)]
    writes += [(0x200 + 4*i, 0) for i in range(64)]
    for batch in vector['batches']:
        for row, words in enumerate(batch['weight_words']):
            writes += [(0x20 + 4*i, value) for i, value in enumerate(words)]
            writes.append((0x10, 1 << row))
        writes += [(0x100 + 4*row, value) for row, value in enumerate(batch['input_words'])]
    if mode == 'wrong_weight':
        address, value = writes[66]
        writes[66] = address, value ^ 1
        writes = writes[:162]
    observed = [values for kind, values in events if kind == 'PE_WRITE']
    if len(observed) != len(writes):
        raise RuntimeError('Incorrect number of actual configuration writes')
    for index, (actual, (offset, value)) in enumerate(zip(observed, writes)):
        if (int(actual['index']) != index or int(actual['addr'], 16) != 0x90000000 + offset or
                int(actual['value'], 16) != value):
            raise RuntimeError('Actual configuration differs from image/model at write ' + str(index))
    if mode == 'wrong_weight':
        if any(kind != 'PE_WRITE' for kind, _ in events):
            raise RuntimeError('Wrong-weight test unexpectedly accepted a result')
    else:
        partials = [v for batch in vector['partial_golden'] for v in batch]
        expected_cpu = all_result_goldens(vector)
        if mode == 'wrong_requant':
            expected_cpu = expected_cpu[:192]
        elif mode == 'wrong_pool':
            expected_cpu = expected_cpu[:256]
        for kind, expected in (('PE_RESULT', partials), ('CPU_RESULT', expected_cpu)):
            actual = [values for event, values in events if event == kind]
            if len(actual) != len(expected) or any(
                int(item['index']) != i or int(item['value'], 16) != (value & 0xffffffff)
                for i, (item, value) in enumerate(zip(actual, expected))
            ):
                raise RuntimeError('Actual ' + kind + ' differs from standard answer in ' + mode)
        complete = sum(kind == 'COMPLETE' for kind, _ in events)
        if complete != (1 if mode == 'normal' else 0):
            raise RuntimeError('Unexpected/missing completion record')
        if len(events) != len(writes) + 128 + len(expected_cpu) + complete:
            raise RuntimeError('Extra transaction records')
    return {'configuration_payloads_checked': len(writes), 'trace_records': len(events),
            'accepted_cpu_results': sum(kind == 'CPU_RESULT' for kind, _ in events)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('vector', 'rtl'), default='rtl')
    parser.add_argument('--task2-root', type=Path, default=TASK.parent / 'task2')
    parser.add_argument('--validation-index', type=int, default=0)
    parser.add_argument('--output-channel-start', type=int, choices=(0, 16), default=0)
    parser.add_argument('--row', type=int, default=0)
    parser.add_argument('--col', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=1200, help='seconds per EDA step')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    work = TASK / 'work'
    work.mkdir(exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix='conv1_pool.', dir=work))
    print('RESULT=' + str(case), flush=True)
    summary = {
        'status': 'RUNNING', 'stage': args.stage,
        'scope': 'Real Conv1 16x4 tile plus CPU ReLU/Q24 requantization and 2x2 pooling; includes arithmetic boundary checks',
        'cpu_included': args.stage == 'rtl', 'sdf_annotated': False,
        'full_cnn_deployed': False, 'clock_period_ns': 10,
        'commands': [], 'cases': [],
    }
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
        record = {'argv': command, 'cwd': str(folder), 'log': str(log), 'timeout_s': timeout}
        summary['commands'].append(record)
        save(case / 'summary.json', summary)
        print('CONV1_POOL_PHASE=' + label, flush=True)
        start = time.monotonic()
        with log.open('w') as stream:
            active = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                      stdout=stream, stderr=subprocess.STDOUT,
                                      start_new_session=True, preexec_fn=child_limit)
            try:
                while True:
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        raise RuntimeError('Timeout: ' + str(log))
                    try:
                        code = active.wait(timeout=min(20, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        print(f'RUNNING {label} seconds={int(time.monotonic()-start)} LOG={log}', flush=True)
                        if log.stat().st_size > 64 * 1024**2:
                            raise RuntimeError('Too many diagnostics: ' + str(log))
            finally:
                if active.poll() is None:
                    os.killpg(active.pid, signal.SIGTERM)
                    try:
                        active.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(active.pid, signal.SIGKILL)
                        active.wait(timeout=5)
                active = None
        record.update(returncode=code, elapsed_s=round(time.monotonic() - start, 3))
        save(case / 'summary.json', summary)
        text = log.read_text(errors='replace')
        if code and not allow_failure:
            print('\n'.join(text.splitlines()[-20:]), flush=True)
            raise RuntimeError('Command failed: ' + str(log))
        return code, text

    try:
        original = {str(p): sha(p) for p in input_files()}
        frozen = case / 'source'
        for name, digest in original.items():
            source = Path(name)
            target = frozen / source.relative_to(DESIGN)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if sha(target) != digest:
                raise RuntimeError('Input changed while freezing: ' + name)
        summary['input_sha256'] = original
        summary['frozen_source_sha256'] = {
            str(Path(name).relative_to(DESIGN)): digest for name, digest in original.items()
        }
        starter = json.loads((TASK / 'SOURCE_MANIFEST.json').read_text())['base_files']
        unchanged = {name: digest for name, digest in summary['frozen_source_sha256'].items()
                     if name.startswith('project/rtl/') or name == 'project/sw/pe_smoke.c'}
        summary['rtl_and_smoke_match_starter'] = all(starter.get(n) == d for n, d in unchanged.items())
        project = frozen / 'project'
        vector_dir = case / 'vector'
        vector_python = env.get('TASK2_PYTHON', str(Path.home() / 'miniforge3/envs/task2-cnn/bin/python'))
        run([vector_python, project / 'scripts/prepare_conv1_pool.py',
             '--task2-root', args.task2_root.resolve(), '--output-dir', vector_dir,
             '--validation-index', args.validation_index, '--output-channel-start', args.output_channel_start,
             '--row', args.row, '--col', args.col], case, 'prepare_real_vector', 180)
        vector = json.loads((vector_dir / 'conv1_pool.json').read_text())
        summary['vector'] = {key: vector[key] for key in (
            'sample_relative_path', 'validation_index', 'output_channel_start', 'lane_positions',
            'partial_golden', 'bias', 'accumulator_golden', 'requant_golden', 'pool_golden')}
        summary['data_provenance'] = vector['provenance']
        if args.stage == 'rtl':
            if vector['terms'][0]['input_q'][0] == 0:
                raise RuntimeError('The first input must be nonzero for the weight-error negative control')
            vendor = frozen / 'vendor/Cores-VeeR-EL2'
            run([sys.executable, project / 'scripts/prepare.py', '--skip-config', '--rv-root', vendor],
                project, 'prepare_frozen_rtl')
            build = project / 'build'
            prefix = env.get('RISCV_PREFIX', 'riscv-none-elf-')
            for suffix in ('gcc', 'objcopy', 'objdump', 'nm'):
                if not shutil.which(prefix + suffix):
                    raise RuntimeError('Missing RISC-V tool: ' + prefix + suffix)

            def firmware(folder, injection=None):
                folder.mkdir()
                command = [prefix + 'gcc', '-march=rv32imc_zicsr', '-mabi=ilp32', '-O2',
                           '-ffreestanding', '-fno-builtin', '-fno-pic', '-msmall-data-limit=0',
                           '-nostdlib', '-nostartfiles', '-Wall', '-Wextra', '-Werror',
                           '-Wl,--no-relax', '-Wl,-T,' + str(project / 'sw/link.ld'),
                           '-Wl,-Map,' + str(folder / 'program.map'), '-I' + str(vector_dir)]
                if injection:
                    command.append('-D' + injection + '=1')
                run([*command, project / 'sw/start.S', project / 'sw/conv1_pool.c',
                     '-o', folder / 'program.elf'], project, folder.name + '_gcc')
                run([prefix + 'objcopy', '-O', 'binary', folder / 'program.elf', folder / 'program.bin'],
                    project, folder.name + '_objcopy')
                _, dis = run([prefix + 'objdump', '-d', folder / 'program.elf'], project, folder.name + '_objdump')
                (folder / 'program.dis').write_text(dis)
                run([sys.executable, project / 'scripts/bin2hex.py', folder / 'program.bin', folder / 'program.hex'],
                    project, folder.name + '_hex')
                _, symbols = run([prefix + 'nm', '-S', folder / 'program.elf'], project, folder.name + '_symbols')
                matches = re.findall(r'^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+[Bb]\s+conv1_pool_results$', symbols, re.M)
                if len(matches) != 1 or int(matches[0][1], 16) != 304 * 4:
                    raise RuntimeError('Cannot locate the 304 actual CPU result words')
                address = int(matches[0][0], 16)
                if address % 4 or not 0x80000000 <= address < 0x8003e000:
                    raise RuntimeError('Result symbol is not safely within RAM')
                return address

            firmware_cases = []
            for name, injection in (
                ('normal', None),
                ('wrong_weight', 'C1B_INJECT_WEIGHT_ERROR'),
                ('wrong_requant', 'C1B_INJECT_REQUANT_ERROR'),
                ('wrong_pool', 'C1B_INJECT_POOL_ERROR'),
            ):
                folder = case / (name + '_firmware')
                address = firmware(folder, injection)
                firmware_cases.append((name, folder, address))
            summary['result_addresses'] = {name: f'0x{address:08x}'
                                           for name, _, address in firmware_cases}
            # The RAM model's old label describes only the original smoke C.
            # Adapt two diagnostic strings in an isolated copy, not any logic.
            models = case / 'models'
            models.mkdir()
            ram_original = (project / 'tb/axi_ram_model.sv').read_text()
            replacements = {
                'SOC_SMOKE_PASS: CPU checked all 64 PE output words': 'CONV1_POOL_CPU_RETURN_PASS',
                'SOC_SMOKE_FAIL code=%08x': 'CONV1_POOL_CPU_RETURN_FAIL code=%08x',
            }
            ram = ram_original
            for old, new in replacements.items():
                if ram.count(old) != 1:
                    raise RuntimeError('RAM diagnostic adaptation no longer matches the source')
                ram = ram.replace(old, new)
            ram_model = models / 'axi_ram_model.sv'
            ram_model.write_text(ram)
            summary['ram_diagnostic_adaptation'] = {'source_sha256': sha(project / 'tb/axi_ram_model.sv'),
                'copy_sha256': sha(ram_model), 'changes': replacements, 'logic_changed': False}
            golden_file = vector_dir / 'golden.hex'
            golden_words = all_result_goldens(vector)
            if len(golden_words) != 304:
                raise RuntimeError('Expected 304 real and boundary-check result words')
            golden_file.write_text(''.join(f'{value & 0xffffffff:08x}\n' for value in golden_words))
            compat = case / 'pthread_yield_compat.o'
            run(['/usr/bin/gcc', '-fPIC', '-c', frozen / 'compat/pthread_yield_compat.c', '-o', compat],
                case, 'host_compatibility')
            adapter = case / 'vcs_vm.py'
            shutil.copyfile(TASK / 'scripts/vcs_vm.py', adapter)
            summary['vcs_adapter_sha256'] = sha(adapter)
            simulation = case / 'rtl'
            simulation.mkdir()
            _, compile_text = run([
                sys.executable, adapter, '-full64', '-sverilog', '-timescale=1ns/1ps', '-debug_access',
                '+define+VCS', '+define+PE_REFERENCE_MACRO', '+define+AXI_RX_TIMING_CHECK',
                '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0', '-assert', 'svaext',
                '-f', build / 'rtl.f', project / 'tb/sram_reference_model.v',
                ram_model, project / 'tb/tb_soc.sv', project / 'tests/soc_conv1_pool_monitor.sv',
                '-top', 'tb_soc', '-top', 'soc_conv1_pool_monitor',
                '-Mdir=' + str(simulation / 'csrc'), '-o', simulation / 'simv', '-LDFLAGS', compat,
            ], simulation, 'rtl_compile', args.timeout)
            if re.search(r'^\s*(?:Error-\[|Fatal:|FATAL:)', compile_text, re.M):
                raise RuntimeError('Compiler reported an error')
            executable_digest = sha(simulation / 'simv')
            for name, image, address in firmware_cases:
                trace = simulation / (name + '_trace.tsv')
                code, log = run([simulation / 'simv', '+MEM_HEX=' + str(image / 'program.hex'),
                                 '+GOLDEN_FILE=' + str(golden_file), f'+RESULT_ADDR={address:08x}',
                                 '+TRACE=' + str(trace)], simulation, name + '_simulation',
                                args.timeout, allow_failure=name != 'normal')
                if sha(simulation / 'simv') != executable_digest:
                    raise RuntimeError('Simulator changed between positive and negative tests')
                if name == 'normal':
                    match = re.findall(r'^CONV1_POOL_MONITOR_PASS (.*)$', log, re.M)
                    if len(match) != 1 or log.count('CONV1_POOL_CPU_RETURN_PASS') != 1:
                        raise RuntimeError('Missing independent monitor or CPU completion: ' + str(case / 'normal_simulation.log'))
                    if re.search(r'CONV1_POOL_.*FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|^\s*(?:Error-\[|Fatal:|FATAL:)', log, re.M):
                        raise RuntimeError('Diagnostics rejected the normal test')
                    if len(re.findall(r'^REFERENCE_SRAM_BEHAVIORAL_MODEL instance=', log, re.M)) != 256:
                        raise RuntimeError('Expected the full 256 SRAM instances')
                    for instance in ('imem', 'dmem'):
                        if f'AXI_RESPONSE_DELAY_NS=1.000 instance=tb_soc.{instance}' not in log or \
                           f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{instance}' not in log:
                            raise RuntimeError('External-memory receive checks are not active')
                    summary['normal_monitor_result'] = match[0]
                else:
                    # This VCS can return zero after $fatal; inspect the exact
                    # failure reason and reject any claimed successful finish.
                    expected_reason = {
                        'wrong_weight': 'CONV1_POOL_MONITOR_FAIL PE partial',
                        'wrong_requant': 'CONV1_POOL_MONITOR_FAIL CPU requant',
                        'wrong_pool': 'CONV1_POOL_MONITOR_FAIL CPU pool',
                    }[name]
                    if ('Fatal:' not in log or expected_reason not in log or
                            'CONV1_POOL_MONITOR_PASS' in log or 'CONV1_POOL_CPU_RETURN_PASS' in log):
                        raise RuntimeError('Negative control did not fail for its expected reason: ' + name)
                if not trace.is_file() or trace.stat().st_size == 0:
                    raise RuntimeError('Missing actual AXI trace')
                payload_checks = inspect_trace(trace, vector, mode=name)
                summary['cases'].append({'case': name, 'pass': True, 'returncode': code,
                                         'expected_failure': name != 'normal', 'trace_sha256': sha(trace),
                                         'payload_checks': payload_checks})
            summary.update(cpu_included=True, pe_writes=258, pe_reads=128,
                           negative_controls_detected=['wrong_weight', 'wrong_requant', 'wrong_pool'],
                           requant_outputs=64, pool_outputs=16, quant_boundary_cases=24, pool_boundary_cases=8,
                           simulator_sha256=executable_digest)
        for name, digest in original.items():
            if sha(name) != digest or sha(frozen / Path(name).relative_to(DESIGN)) != digest:
                raise RuntimeError('Live or frozen source changed: ' + name)
        for metadata in vector['provenance'].values():
            if 'path' in metadata and sha(metadata['path']) != metadata['sha256']:
                raise RuntimeError('Model/vector input changed: ' + metadata['path'])
        summary['artifacts_sha256'] = {str(p.relative_to(case)): sha(p) for p in case.rglob('*')
            if p.is_file() and (p.parent == vector_dir or p.name in ('program.elf', 'program.hex', 'program.bin'))}
        summary['status'] = 'PASS'
        (case / 'RESULTS.md').write_text(
            '# 卷积、ReLU、量化和池化：' + ('CPU/PE RTL 验证通过' if args.stage == 'rtl' else '标准答案已生成') + '\n\n'
            + f"图片：`{vector['sample_relative_path']}`；16个输出通道从 {vector['output_channel_start']} 起；"
            + f"四个位置：{vector['lane_positions']}。\n\n"
            + 'PE完成64个真实卷积结果。CPU执行ReLU、按模型Q24规则重新量化得到64个0～127的整数，'
            + '再按每通道四个位置取最大值，得到16个池化输出。\n\n'
            + f"池化输出：`{vector['pool_golden']}`。\n\n"
            + ('独立检查258次PE配置、128次PE读回，以及304个CPU实际RAM结果。'
               '其中272个结果来自真实图片的计算链，另有24个量化边界和8个池化边界检查。'
               '权重错误、量化错误、池化错误三种负控均使用同一仿真器并被正确拒绝。\n\n'
               if args.stage == 'rtl' else '卷积和后处理均与独立数学参考核对。\n\n')
            + '范围为第一层16输出通道的一个2x2卷积区域及对应16个池化值。'
            + '完整第一层、后续层和整网分类尚未实现。原RTL、smoke、单点和并行卷积测试保留；'
            + '此次为RTL功能验证，未运行综合或SDF。\n', encoding='utf-8')
        save(case / 'summary.json', summary)
        print('CONV1_POOL_' + args.stage.upper() + '_PASS', flush=True)
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(case / 'summary.json', summary)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('CONV1_POOL_RUN_FAILED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
