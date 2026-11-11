#!/usr/bin/env python3
"""Run one complete real-image Conv2/ReLU/Q24/pool on the RISC-V/PE RTL."""
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
TOTAL_TILES = 3844
OUTPUT_BYTES = 61504


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
        'compat/pthread_yield_compat.c', 'project/tests/soc_conv2_full_monitor.sv',
        'project/scripts/prepare.py', 'project/scripts/bin2hex.py',
        'project/scripts/prepare_conv1_point.py', 'project/scripts/prepare_conv2_tile.py',
        'project/scripts/prepare_conv2_full.py',
        'project/scripts/audit_conv2_full.py',
        'project/scripts/run_conv2_full.py', 'project/scripts/run_conv2_full.sh',
        'project/scripts/prepare_conv2_full.sh',
    ))
    return sorted(set(files))


def child_limit():
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))


def output_indices(start, count):
    for tile in range(start, start + count):
        group, pixel = divmod(tile, 961)
        for col in range(16):
            yield (group * 16 + col) * 961 + pixel


def blocks(start, count):
    end = start + count
    while start < end:
        size = min(32, end - start, 961 - start % 961)
        yield start, size
        start += size


def write_count(start, count):
    return 66 + sum(18 * (80 + 16 * size) for _, size in blocks(start, count))


def stream_partials(golden, start, count):
    return b''.join(golden[(tile * 18 + batch) * 256:(tile * 18 + batch + 1) * 256]
                    for first, size in blocks(start, count)
                    for batch in range(18) for tile in range(first, first + size))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('vector', 'rtl'), default='rtl')
    parser.add_argument('--task2-root', type=Path, default=TASK.parent / 'task2')
    parser.add_argument('--conv1-result', type=Path)
    parser.add_argument('--conv1-pointer', type=Path, default=TASK / 'work/latest_conv1_full_result.json')
    parser.add_argument('--preflight-only', action='store_true', help='boundary and negative checks, no full coverage run')
    parser.add_argument('--tile-start', type=int, default=0)
    parser.add_argument('--tile-count', type=int, default=TOTAL_TILES)
    parser.add_argument('--shards', type=int, choices=(1, 2, 4), default=4,
                        help='independent CPU simulations; 1 tests one continuous run')
    parser.add_argument('--timeout', type=int, default=14400, help='seconds per EDA step')
    args = parser.parse_args()
    if not (0 <= args.tile_start < TOTAL_TILES and 0 < args.tile_count <= TOTAL_TILES - args.tile_start):
        parser.error('tile range must lie within 0..3843')
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    work = TASK / 'work'
    work.mkdir(exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix='conv2_full.', dir=work))
    print('RESULT=' + str(case), flush=True)
    full = not args.preflight_only and args.tile_start == 0 and args.tile_count == TOTAL_TILES
    shards = min(args.shards, args.tile_count)
    summary = dict(status='RUNNING', stage=args.stage, full_second_layer_requested=full,
                   preflight_only=args.preflight_only, single_boot_two_layers=False,
                   negative_controls_requested=True,
                   full_second_layer=False, compiler_optimization='-O3',
                   shards=shards, requested_shards=args.shards,
                   simulation_mode='sharded_cpu_simulation' if shards > 1 else 'continuous_cpu_simulation',
                   full_cnn_deployed=False, cpu_included=args.stage == 'rtl', sdf_annotated=False,
                   clock_period_ns=10, tile_start=args.tile_start, tile_count=args.tile_count,
                   scope='Actual prior CPU Pool1 -> Conv2 64x62x62 -> ReLU/Q24/pool 64x31x31; independent firmware boot',
                   commands=[], cases=[])
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
        print('CONV2_FULL_PHASE=' + label, flush=True)
        start = time.monotonic()
        progress_offset = 0
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
                        with log.open(errors='replace') as progress:
                            progress.seek(progress_offset)
                            recent = progress.read()
                            progress_offset = progress.tell()
                        for line in recent.splitlines():
                            if 'CONV2_FULL_PROGRESS' in line:
                                print(line, flush=True)
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
        contents = log.read_text(errors='replace')
        if code and not allow_failure:
            print('\n'.join(contents.splitlines()[-20:]), flush=True)
            raise RuntimeError('Command failed: ' + str(log))
        return code, contents

    def run_parallel(jobs):
        """Run independent CPU ranges concurrently, keeping process ownership."""
        processes = {}
        streams = []
        results = {}
        started = time.monotonic()
        last_progress = started
        try:
            for name, command, folder in jobs:
                command = list(map(str, command))
                log = case / (name + '_simulation.log')
                record = dict(argv=command, cwd=str(folder), log=str(log), timeout_s=args.timeout)
                summary['commands'].append(record)
                stream = log.open('w')
                streams.append(stream)
                print('CONV2_FULL_PHASE=' + name + '_simulation', flush=True)
                process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                           stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=True, preexec_fn=child_limit)
                processes[name] = (process, record, log)
            save(case / 'summary.json', summary)
            while len(results) < len(jobs):
                if time.monotonic() - started > args.timeout:
                    raise RuntimeError('Parallel CPU range simulations timed out')
                for name, (process, record, log) in processes.items():
                    if name in results:
                        continue
                    if log.stat().st_size > 64 * 1024**2:
                        raise RuntimeError('Too many diagnostics: ' + str(log))
                    code = process.poll()
                    if code is not None:
                        contents = log.read_text(errors='replace')
                        record.update(returncode=code, elapsed_s=round(time.monotonic() - started, 3))
                        results[name] = (code, contents)
                        save(case / 'summary.json', summary)
                        if code or 'CONV2_FULL_MONITOR_FAIL' in contents or 'CONV2_FULL_MONITOR_PASS' not in contents:
                            raise RuntimeError('CPU range failed: ' + str(log))
                if time.monotonic() - last_progress >= 20:
                    last_progress = time.monotonic()
                    for name, (_, _, log) in processes.items():
                        lines = [line for line in log.read_text(errors='replace').splitlines()
                                 if 'CONV2_FULL_PROGRESS' in line]
                        print('RUNNING ' + name + ' seconds=' + str(int(time.monotonic()-started))
                              + (' ' + lines[-1] if lines else ' initializing CPU/RAM'), flush=True)
                if len(results) < len(jobs):
                    # Short polls also let GUI cancellation interrupt promptly.
                    time.sleep(0.5)
        finally:
            for process, _, _ in processes.values():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
            for process, _, _ in processes.values():
                if process.poll() is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
            for stream in streams:
                stream.close()
        return results

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
        unchanged = {p: d for p, d in summary['frozen_source_sha256'].items()
                     if p.startswith('project/rtl/') or p == 'project/sw/pe_smoke.c'}
        summary['rtl_and_smoke_match_starter'] = all(starter.get(p) == d for p, d in unchanged.items())
        if not summary['rtl_and_smoke_match_starter']:
            raise RuntimeError('Baseline RTL or smoke source changed')
        project = frozen / 'project'
        vector_dir = case / 'vector'
        vector_python = env.get('TASK2_PYTHON', str(Path.home() / 'miniforge3/envs/task2-cnn/bin/python'))
        vector_command = [vector_python, project / 'scripts/prepare_conv2_full.py',
                          '--task2-root', args.task2_root.resolve(), '--output-dir', vector_dir]
        if args.conv1_result:
            vector_command += ['--conv1-result', args.conv1_result.resolve()]
        else:
            vector_command += ['--conv1-pointer', args.conv1_pointer.resolve()]
        run(vector_command, case, 'prepare_from_actual_pool1', 300)
        vector = json.loads((vector_dir / 'conv2_full.json').read_text())
        summary['vector'] = vector
        summary['source_conv1_result'] = vector['source_conv1_result']
        if args.stage == 'rtl':
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
            code, source_log = run([vector_python, project / 'scripts/prepare_conv2_full.py',
                                   '--task2-root', args.task2_root.resolve(),
                                   '--output-dir', case / 'rejected_source_vector',
                                   '--conv1-pointer', bad_pointer_path], case,
                                  'wrong_source_guard', 300, allow_failure=True)
            if code == 0 or 'Conv1 pointer provenance mismatch: summary_sha256' not in source_log:
                raise RuntimeError('Incorrect upstream source hash was not rejected')
            summary['wrong_source_guard_rejected'] = True
            run([sys.executable, project / 'scripts/prepare.py', '--skip-config',
                 '--rv-root', frozen / 'vendor/Cores-VeeR-EL2'], project, 'prepare_frozen_rtl')
            prefix = env.get('RISCV_PREFIX', 'riscv-none-elf-')
            for suffix in ('gcc', 'objcopy', 'objdump', 'nm'):
                if not shutil.which(prefix + suffix):
                    raise RuntimeError('Missing RISC-V tool: ' + prefix + suffix)

            def firmware(name, start, count, injection=None):
                folder = case / (name + '_firmware')
                folder.mkdir()
                command = [prefix + 'gcc', '-march=rv32imc_zicsr', '-mabi=ilp32', '-O3',
                           '-ffreestanding', '-fno-builtin', '-fno-pic', '-msmall-data-limit=0',
                           '-nostdlib', '-nostartfiles', '-Wall', '-Wextra', '-Werror',
                           '-Wl,--no-relax', '-Wl,-T,' + str(project / 'sw/link.ld'),
                           '-Wl,-Map,' + str(folder / 'program.map'), '-I' + str(vector_dir),
                           '-DC2F_TILE_START=' + str(start), '-DC2F_TILE_COUNT=' + str(count)]
                if injection:
                    command.append('-D' + injection + '=1')
                run([*command, project / 'sw/start.S', project / 'sw/conv2_full.c',
                     '-o', folder / 'program.elf'], project, name + '_gcc')
                run([prefix + 'objcopy', '-O', 'binary', folder / 'program.elf', folder / 'program.bin'],
                    project, name + '_objcopy')
                run([sys.executable, project / 'scripts/bin2hex.py', folder / 'program.bin', folder / 'program.hex'],
                    project, name + '_hex')
                _, symbols = run([prefix + 'nm', '-S', folder / 'program.elf'], project, name + '_symbols')
                match = re.findall(r'^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+[Bb]\s+conv2_full_output$', symbols, re.M)
                if len(match) != 1 or int(match[0][1], 16) != OUTPUT_BYTES:
                    raise RuntimeError('Cannot locate the 61504-byte CPU output buffer')
                address = int(match[0][0], 16)
                if address % 4 or not 0x80000000 <= address < address + OUTPUT_BYTES <= 0x8003eff0:
                    raise RuntimeError('Output buffer overlaps RAM/stack limits')
                _, dis = run([prefix + 'objdump', '-d', folder / 'program.elf'], project, name + '_objdump')
                (folder / 'program.dis').write_text(dis)
                return dict(name=name, folder=folder, start=start, count=count, address=address)

            firmware_cases = [
                firmware('preflight_start', 0, 2),
                firmware('preflight_group_boundary', 960, 2),
                firmware('preflight_block_boundary', 0, 33),
                firmware('preflight_last', 3843, 1),
                firmware('wrong_weight', 0, 1, 'C2F_INJECT_WEIGHT_ERROR'),
                firmware('wrong_input', 0, 1, 'C2F_INJECT_INPUT_ERROR'),
                firmware('wrong_pool', 0, 1, 'C2F_INJECT_POOL_ERROR'),
            ]
            next_tile = args.tile_start
            for shard in range(0 if args.preflight_only else shards):
                count = args.tile_count // shards + (shard < args.tile_count % shards)
                name = 'normal' if shards == 1 else 'normal_' + str(shard)
                firmware_cases.append(firmware(name, next_tile, count))
                next_tile += count
            models = case / 'models'
            models.mkdir()
            ram_original = (project / 'tb/axi_ram_model.sv').read_text()
            replacements = {
                'SOC_SMOKE_PASS: CPU checked all 64 PE output words': 'CONV2_FULL_CPU_RETURN_PASS',
                'SOC_SMOKE_FAIL code=%08x': 'CONV2_FULL_CPU_RETURN_FAIL code=%08x',
            }
            ram = ram_original
            for old, new in replacements.items():
                if ram.count(old) != 1:
                    raise RuntimeError('RAM diagnostic adaptation does not match source')
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
            _, compile_text = run([
                sys.executable, adapter, '-full64', '-sverilog', '-timescale=1ns/1ps', '-debug_access',
                '+define+VCS', '+define+PE_REFERENCE_MACRO', '+define+AXI_RX_TIMING_CHECK',
                '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0', '-assert', 'svaext',
                '-f', project / 'build/rtl.f', project / 'tb/sram_reference_model.v', ram_model,
                project / 'tb/tb_soc.sv', project / 'tests/soc_conv2_full_monitor.sv',
                '-top', 'tb_soc', '-top', 'soc_conv2_full_monitor',
                '-Mdir=' + str(simulation / 'csrc'), '-o', simulation / 'simv', '-LDFLAGS', compat,
            ], simulation, 'rtl_compile', args.timeout)
            if re.search(r'^\s*(?:Error-\[|Fatal:|FATAL:)', compile_text, re.M):
                raise RuntimeError('Compiler reported an error')
            executable_digest = sha(simulation / 'simv')
            golden = (vector_dir / 'pool.bin').read_bytes()
            if len(golden) != OUTPUT_BYTES:
                raise RuntimeError('Invalid full-layer pool golden size')
            def simulation_command(item):
                name = item['name']
                trace = simulation / (name + '_tiles.tsv')
                observed = simulation / (name + '_pool.bin')
                observed_partials = simulation / (name + '_partials.bin')
                return [simulation / 'simv', '+MEM_HEX=' + str(item['folder'] / 'program.hex'),
                           '+INPUT_FILE=' + str(vector_dir / 'conv2_input.bin'),
                           '+WEIGHT_FILE=' + str(vector_dir / 'weights_hw.bin'),
                           '+PARTIAL_FILE=' + str(vector_dir / 'partials.bin'),
                           '+POOL_FILE=' + str(vector_dir / 'pool.bin'),
                           f"+RESULT_ADDR={item['address']:08x}", '+TILE_START=' + str(item['start']),
                           '+TILE_COUNT=' + str(item['count']), '+OBSERVED_FILE=' + str(observed),
                           '+OBSERVED_PARTIAL_FILE=' + str(observed_partials),
                           '+TRACE=' + str(trace), '+MAX_CYCLES=' + str(2500000 + 350000 * item['count'])]

            parallel_results = None
            normal_records = []
            for item in firmware_cases:
                name = item['name']
                trace = simulation / (name + '_tiles.tsv')
                observed = simulation / (name + '_pool.bin')
                observed_partials = simulation / (name + '_partials.bin')
                negative = name.startswith('wrong_')
                if name.startswith('normal') and shards > 1:
                    if parallel_results is None:
                        parallel_results = run_parallel([(other['name'], simulation_command(other), simulation)
                                                        for other in firmware_cases if other['name'].startswith('normal')])
                    code, log = parallel_results[name]
                else:
                    code, log = run(simulation_command(item), simulation, name + '_simulation', args.timeout,
                                    allow_failure=negative)
                if sha(simulation / 'simv') != executable_digest:
                    raise RuntimeError('Simulator changed between tests')
                record = dict(case=name, expected_failure=negative, returncode=code,
                              tile_start=item['start'], tile_count=item['count'])
                if negative:
                    reason = {'wrong_weight': 'CONV2_FULL_MONITOR_FAIL weight',
                              'wrong_input': 'CONV2_FULL_MONITOR_FAIL PE configuration',
                              'wrong_pool': 'CONV2_FULL_MONITOR_FAIL CPU output'}[name]
                    if 'Fatal:' not in log or reason not in log or 'CONV2_FULL_MONITOR_PASS' in log or 'CONV2_FULL_CPU_RETURN_PASS' in log:
                        raise RuntimeError('Negative test failed for unexpected reason: ' + name)
                else:
                    match = re.findall(r'^CONV2_FULL_MONITOR_PASS (.*)$', log, re.M)
                    if len(match) != 1 or log.count('CONV2_FULL_CPU_RETURN_PASS') != 1:
                        raise RuntimeError('Missing monitor/CPU completion: ' + name)
                    if re.search(r'CONV2_FULL_.*FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|^\s*(?:Error-\[|Fatal:|FATAL:)', log, re.M):
                        raise RuntimeError('Normal test has a failure diagnostic: ' + name)
                    if len(re.findall(r'^REFERENCE_SRAM_BEHAVIORAL_MODEL instance=', log, re.M)) != 256:
                        raise RuntimeError('Expected 256 actual SRAM instances')
                    for instance in ('imem', 'dmem'):
                        if f'AXI_RESPONSE_DELAY_NS=1.000 instance=tb_soc.{instance}' not in log or \
                           f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{instance}' not in log:
                            raise RuntimeError('External RAM timing checks are not active')
                    actual = observed.read_bytes()
                    if len(actual) != OUTPUT_BYTES or any(actual[i] != golden[i] for i in output_indices(item['start'], item['count'])):
                        raise RuntimeError('Actual CPU output artifact differs from golden: ' + name)
                    record.update(monitor=match[0], output_bytes_checked=item['count'] * 16,
                                  pe_reads_checked=item['count'] * 1152,
                                  pe_writes_checked=write_count(item['start'], item['count']),
                                  observed_partials=str(observed_partials),
                                  observed_partials_sha256=sha(observed_partials),
                                  observed_sha256=sha(observed), trace_sha256=sha(trace))
                    if name.startswith('normal'):
                        normal_records.append(record)
                record['pass'] = True
                summary['cases'].append(record)
                save(case / 'summary.json', summary)
            # CPU resets isolate ranges for faster validation. Scatter their
            # actual CHW output bytes and concatenate actual PE reads in order.
            merged = bytearray(OUTPUT_BYTES)
            coverage = bytearray(OUTPUT_BYTES)
            merged_partials = bytearray(TOTAL_TILES * 18 * 256)
            partial_golden = (vector_dir / 'partials.bin').read_bytes()
            for record in normal_records:
                start, count = record['tile_start'], record['tile_count']
                actual = (simulation / (record['case'] + '_pool.bin')).read_bytes()
                actual_partials = Path(record['observed_partials']).read_bytes()
                expected_stream = stream_partials(partial_golden, start, count)
                if actual_partials != expected_stream:
                    raise RuntimeError('Actual partial dump differs from the reference')
                # Reorder only actual CPU/PE read bytes to global tile-major.
                cursor = 0
                for block_start, block_count in blocks(start, count):
                    for batch in range(18):
                        for tile in range(block_start, block_start + block_count):
                            offset = (tile * 18 + batch) * 256
                            merged_partials[offset:offset + 256] = actual_partials[cursor:cursor + 256]
                            cursor += 256
                if cursor != len(actual_partials):
                    raise RuntimeError('Unexpected stream length')
                for index in output_indices(start, count):
                    if coverage[index]:
                        raise RuntimeError('Overlapping CPU range output coverage')
                    coverage[index] = 1
                    merged[index] = actual[index]
            if sum(coverage) != (0 if args.preflight_only else args.tile_count * 16):
                raise RuntimeError('Incomplete merged actual CPU output')
            observed = simulation / 'normal_pool.bin'
            observed_partials = simulation / 'normal_partials.bin'
            observed.write_bytes(merged)
            observed_partials.write_bytes(merged_partials)
            summary.update(normal_monitor_results=[r['monitor'] for r in normal_records],
                           output_bytes_checked=(0 if args.preflight_only else args.tile_count * 16),
                           pe_writes=sum(write_count(r['tile_start'], r['tile_count']) for r in normal_records),
                           pe_reads=1152 * (0 if args.preflight_only else args.tile_count),
                           sequential_schedule_pe_writes=write_count(args.tile_start, args.tile_count),
                           observed_output=str(observed), observed_output_sha256=sha(observed),
                           observed_partials=str(observed_partials), observed_partials_sha256=sha(observed_partials))
            summary.update(negative_controls_detected=['wrong_weight', 'wrong_input', 'wrong_pool'], simulator_sha256=executable_digest)
            save(case / 'summary.json', summary)
            run([vector_python, project / 'scripts/audit_conv2_full.py', '--case', case],
                case, 'independent_audit', 300)
            audit = json.loads((case / 'independent_validation_summary.json').read_text())
            if audit.get('status') != 'PASS':
                raise RuntimeError('Independent full-layer audit did not pass')
            summary['independent_audit'] = audit
            summary['full_second_layer'] = full
        for name, digest in original.items():
            if sha(name) != digest or sha(frozen / Path(name).relative_to(DESIGN)) != digest:
                raise RuntimeError('Live/frozen source changed: ' + name)
        for metadata in vector.get('provenance', {}).values():
            if 'path' in metadata and sha(metadata['path']) != metadata['sha256']:
                raise RuntimeError('Model/vector input changed: ' + metadata['path'])
        summary['artifacts_sha256'] = {str(p.relative_to(case)): sha(p) for p in case.rglob('*')
            if p.is_file() and (p.parent == vector_dir or p.name in ('program.elf', 'program.hex', 'program.bin'))}
        summary['status'] = 'PASS'
        title = '完整第二层' if full else ('第二层边界预检' if args.preflight_only else '第二层指定分块')
        (case / 'RESULTS.md').write_text(
            '# ' + title + ('：CPU/PE RTL 验证通过' if args.stage == 'rtl' else '：标准答案已生成') + '\n\n'
            + '输入是上一次完整第一层运行保存的实际 CPU Pool1 输出，32×64×64 个字节。\n\n'
            + '第二层：64×62×62 卷积结果 → ReLU/Q24 → 2×2 最大池化 → 64×31×31，即 61504 个输出字节。\n\n'
            + (f"本次实际核对 {summary['pe_reads']:,} 个 PE 部分和、{summary['pe_writes']:,} 次配置写入和 {summary['output_bytes_checked']:,} 个 CPU 池化输出字节。\n\n"
               if args.stage == 'rtl' else '这是电脑侧标准答案生成，尚未运行 CPU。\n\n')
            + ('开头、32 块边界、通道组边界、最后位置预检通过；输入、权重和池化错误均被拒绝。\n\n' if args.stage == 'rtl' else '')
            + (f"完整范围由 {shards} 段独立 CPU 仿真覆盖，合并实际输出时检查无重叠、无遗漏。该并行验证用时不能作为单 CPU 推理延迟。\n\n" if args.stage == 'rtl' and not args.preflight_only and shards > 1 else '')
            + '每次保留最多 32 个小块的部分和，同一批权重重复使用；程序和数据保持在原 256 KB RAM 内。\n\n'
            + '本次为第二层 RTL 功能验证，第一层和第二层仍由不同固件运行。第三层、全连接层及最终分类尚未接入；未运行新程序 SDF。\n', encoding='utf-8')
        save(case / 'summary.json', summary)
        print('CONV2_FULL_' + args.stage.upper() + '_PASS', flush=True)
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(case / 'summary.json', summary)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('CONV2_FULL_RUN_FAILED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
