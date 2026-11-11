#!/usr/bin/env python3
"""Compare the same CPU smoke program in original RTL and mapped whole-SoC gates.

This is a no-SDF functional regression, not timing acceptance or formal equivalence.
"""
import argparse
from collections import Counter
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

from prepare_pe_sdf_models import prepare_models

PROJECT = Path(__file__).resolve().parents[1]
DESIGN = PROJECT.parent
TASK = PROJECT.parents[1]
MEMORY_LIMIT = 8 * 1024**3
MAX_LOG_BYTES = 64 * 1024**2


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def merge_hashes(*maps):
    result = {}
    for mapping in maps:
        for name, digest in mapping.items():
            if name in result and result[name] != digest:
                raise RuntimeError('Conflicting input provenance: ' + name)
            result[name] = digest
    return result


def check_hashes(hashes):
    for filename, digest in hashes.items():
        if not Path(filename).is_file() or sha(filename) != digest:
            raise RuntimeError('Missing or changed input: ' + filename)


def select_source():
    rejected = []
    for case in sorted((TASK / 'work').glob('soc_compile.*'),
                       key=lambda path: path.stat().st_mtime, reverse=True):
        report = case / 'summary.json'
        if not report.is_file():
            continue
        try:
            data = json.loads(report.read_text())
            if data.get('status') != 'PASS':
                continue
            structure = data['dc_synthesis_summary']
            if (data.get('soc_mapped') is not True or data.get('compile_performed') is not True or
                    structure.get('top') != 'soc_top' or structure.get('unmapped_leaf_cells') != '0' or
                    structure.get('pe_instances') != '256' or structure.get('sram_instances') != '256'):
                raise RuntimeError('Expected a fully mapped whole-SoC result')
            dependencies = merge_hashes(
                data['source_dependencies_sha256'], data['input_sha256'],
                {str(case / name): digest for name, digest in data['output_sha256'].items()},
                {str(report): sha(report), str(case / 'expected.tcl'): data['expected_tcl_sha256']})
            for name in ('dc/soc_mapped.v', 'dc/soc_mapped.ddc', 'dc/soc_constraints.sdc'):
                if str(case / name) not in dependencies:
                    raise RuntimeError('Missing mapped provenance: ' + name)
            check_hashes(dependencies)
            parent = Path(data['source_soc_read_case'])
            read_summary = json.loads((parent / 'summary.json').read_text())
            if read_summary.get('status') != 'PASS':
                raise RuntimeError('The mapped SoC parent import did not pass')
            return case, data, read_summary, dependencies, rejected
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            rejected.append({'case': str(case), 'reason': str(error)})
    raise RuntimeError('No intact mapped SoC result matches the current sources; '
                       'run run_soc_compile.tcl first. Rejected: ' + json.dumps(rejected))


def source_tree_files(root):
    return sorted(path for path in root.rglob('*') if path.is_file() and
                  not any(part in {'build', '.git', '__pycache__'} for part in path.relative_to(root).parts) and
                  path.suffix not in {'.pyc', '.vpd', '.vcd'})


def check_netlist(path, library_paths):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', path.read_text(), flags=re.S)
    blocks = re.findall(r'\bmodule\s+(\w+)\s*\(.*?;(.*?)\bendmodule\b', text, re.S)
    modules = {}
    for name, body in blocks:
        if name in modules or re.search(r'\balways(?:_\w+)?\b|\binitial\b', body):
            raise RuntimeError('Duplicate or behavioral mapped module: ' + name)
        modules[name] = Counter(ref for ref, _ in re.findall(
            r'^\s*(\w+)\s+(\\\S+|\w+)\s*\(', body, re.M))
    if 'soc_top' not in modules:
        raise RuntimeError('Mapped netlist does not define soc_top')
    libraries = set()
    for library in library_paths:
        libraries.update(re.findall(r'\bmodule\s+(\w+)\b', library.read_text()))
    memo = {}

    def count(name, active):
        if name in memo:
            return memo[name].copy()
        if name in active:
            raise RuntimeError('Recursive mapped hierarchy: ' + name)
        if name not in modules:
            if name not in libraries:
                raise RuntimeError('Unresolved mapped cell type: ' + name)
            return Counter({name: 1})
        result = Counter()
        for ref, number in modules[name].items():
            for leaf, descendants in count(ref, active | {name}).items():
                result[leaf] += number * descendants
        if name.endswith('_pe_1sram_4mul'):
            result['__PE_INSTANCES__'] += 1
        memo[name] = result
        return result.copy()

    leaves = count('soc_top', set())
    if leaves['SRAM_6T_CORE_16x8_MC_TB'] != 256 or leaves['__PE_INSTANCES__'] != 256:
        raise RuntimeError('Expected the complete mapped 256-PE/SRAM array')
    return {'top': 'soc_top', 'module_definitions': len(modules),
            'pe_instances': leaves.pop('__PE_INSTANCES__'),
            'sram_instances': leaves['SRAM_6T_CORE_16x8_MC_TB'],
            'leaf_cell_counts': dict(sorted(leaves.items()))}


def limit_child():
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))


def stop_child(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def read_log(path):
    if path.stat().st_size > MAX_LOG_BYTES:
        raise RuntimeError('Too many diagnostics; inspect ' + str(path))
    return path.read_text(errors='replace')


def inspect_simulation(log, trace, case_name):
    if len(re.findall(r'^SOC_SMOKE_PASS: CPU checked all 64 PE output words\s*$', log, re.M)) != 1:
        raise RuntimeError('Missing unique CPU firmware success marker: ' + case_name)
    matches = re.findall(
        r'^SOC_PROGRAM_MONITOR_PASS pe_reads=(\d+) pe_writes=(\d+) pe_responses=(\d+) '
        r'ifu_reads=(\d+) mem_reads=(\d+) mem_writes=(\d+) cycles=(\d+)\s*$', log, re.M)
    if len(matches) != 1:
        raise RuntimeError('Missing unique independent program monitor result: ' + case_name)
    reads, writes, responses, ifu, mem_reads, mem_writes, cycles = map(int, matches[0])
    if (reads, writes, responses) != (64, 87, 87) or min(ifu, mem_writes, cycles) <= 0:
        raise RuntimeError('Incomplete CPU/AXI/PE checks: ' + case_name)
    sram_messages = re.findall(r'^REFERENCE_SRAM_BEHAVIORAL_MODEL instance=(\S+)\s*$', log, re.M)
    if len(sram_messages) != 256 or len(set(sram_messages)) != 256:
        raise RuntimeError('Expected 256 distinct active SRAM models: ' + case_name)
    for instance in ('imem', 'dmem'):
        for marker in (f'AXI_RESPONSE_DELAY_NS=1.000 instance=tb_soc.{instance}',
                       f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{instance}'):
            if len(re.findall(re.escape(marker) + r'\s*$', log, re.M)) != 1:
                raise RuntimeError('Missing actual memory timing configuration: ' + marker)
    if not trace.is_file() or not trace.stat().st_size:
        raise RuntimeError('No accepted-transaction trace: ' + case_name)
    lines = trace.read_text().splitlines()
    if any(not line.strip() or re.search(r'(?i)\b[0-9a-f]*[xz][0-9a-f]*\b', line) for line in lines):
        raise RuntimeError('Unknown values or blank records in transaction trace: ' + case_name)
    return {'pe_reads': reads, 'pe_writes': writes, 'pe_responses': responses,
            'ifu_reads': ifu, 'mem_reads': mem_reads, 'mem_writes': mem_writes,
            'cycles': cycles, 'sram_instances': 256, 'trace_lines': len(lines),
            'trace_sha256': sha(trace)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=1200,
                        help='maximum seconds for each VCS compile/simulation (default: 1200)')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    case = Path(tempfile.mkdtemp(prefix='soc_functional.', dir=TASK / 'work'))
    print('RESULT=' + str(case), flush=True)
    inputs = case / 'inputs'
    inputs.mkdir()
    summary = {'status': 'RUNNING', 'scope': 'Original RTL versus mapped whole-SoC CPU program functional simulation',
               'cpu_included': True, 'router_included': True, 'sdf_annotated': False,
               'specify_enabled': False, 'timing_checks_enabled': False,
               'external_axi_receiver_setup_check_enabled': True,
               'timing_acceptance_established': False, 'formal_equivalence_checked': False,
               'physical_parasitics_used': False, 'multicorner_verified': False,
               'clock_period_ns': 10, 'external_memory_response_delay_ns': 1,
               'gate_model_mode': 'Audited compatible model copies, TETRAMAX; no NTC or RECREM; +nospecify',
               'child_address_space_limit_bytes': MEMORY_LIMIT, 'commands': [], 'cases': []}
    save(case / 'summary.json', summary)
    env = os.environ.copy()
    for key in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKEFILES'):
        env.pop(key, None)
    env.update(LC_ALL='C', PYTHONDONTWRITEBYTECODE='1')

    def run(command, folder, log_name, phase, timeout):
        command = list(map(str, command))
        path = folder / log_name
        record = {'argv': command, 'cwd': str(folder), 'log': str(path),
                  'phase': phase, 'timeout_s': timeout}
        summary['commands'].append(record)
        save(case / 'summary.json', summary)
        print('SOC_FUNCTIONAL_PHASE=' + phase, flush=True)
        start = time.monotonic()
        with path.open('w') as stream:
            process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True, preexec_fn=limit_child)
            try:
                while True:
                    remaining = timeout - (time.monotonic() - start)
                    if remaining <= 0:
                        raise RuntimeError('Timeout during ' + phase + '; inspect ' + str(path))
                    try:
                        code = process.wait(timeout=min(15, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if path.stat().st_size > MAX_LOG_BYTES:
                            raise RuntimeError('Too many diagnostics during ' + phase)
                        print('SOC_FUNCTIONAL_RUNNING phase=' + phase + ' elapsed_s=' +
                              str(int(time.monotonic() - start)), flush=True)
            except BaseException:
                stop_child(process)
                record.update(exit_code=process.returncode, elapsed_s=round(time.monotonic()-start, 3))
                raise
        record.update(exit_code=code, elapsed_s=round(time.monotonic()-start, 3))
        text = read_log(path)
        if code or re.search(r'(?im)^\s*(?:Error(?:-\[|:)|Fatal:|FATAL:)|'
                             r'SRAM_TIMING_VIOLATION|Timing violation|AXI_RX_TIMING_VIOLATION|'
                             r'SOC_PROGRAM_MONITOR_FAIL|SOC_SMOKE_FAIL|TIMEOUT after', text):
            raise RuntimeError('Command failed during ' + phase + '; inspect ' + str(path))
        return text

    try:
        source, prior, read_summary, dependencies, rejected = select_source()
        summary.update(source_soc_compile_case=str(source), source_dependencies_sha256=dependencies,
                       rejected_source_cases=rejected)
        files = {
            'soc_mapped.v': source / 'dc/soc_mapped.v',
            'soc_constraints.sdc': source / 'dc/soc_constraints.sdc',
            'source_summary.json': source / 'summary.json',
            'source_read_summary.json': Path(prior['source_soc_read_case']) / 'summary.json',
            'standard_model.v': TASK / 'libraries/standard_timing.v',
            'sram_model.v': TASK / 'libraries/sram_timing.v',
            'vcs_vm.py': TASK / 'scripts/vcs_vm.py',
            'compat.c': DESIGN / 'compat/pthread_yield_compat.c',
            'run_soc_functional.py': Path(__file__).resolve(),
            'run_soc_functional.sh': PROJECT / 'synth/run_soc_functional.sh',
            'run_soc_functional.tcl': PROJECT / 'synth/run_soc_functional.tcl',
            'prepare_pe_sdf_models.py': PROJECT / 'scripts/prepare_pe_sdf_models.py',
            'tb_soc.sv': PROJECT / 'tb/tb_soc.sv',
            'axi_ram_model.sv': PROJECT / 'tb/axi_ram_model.sv',
            'reference_sram.v': PROJECT / 'tb/sram_reference_model.v',
            'soc_program_monitor.sv': PROJECT / 'tests/soc_program_monitor.sv',
        }
        for filename in ('validation_summary.json', 'verify_soc_compile_independent.py'):
            if (source / filename).is_file():
                files[filename] = source / filename
        for index, path in enumerate((TASK.parent / 'env.sh', TASK.parent / 'runtime/env.vm.sh',
                                      TASK.parent / 'runtime/env.scl2018.sh', TASK.parent / 'runtime/env.dcpt.sh')):
            files[f'environment_{index}.sh'] = path
        live_hashes = {str(path): sha(path) for path in files.values()}
        merge_hashes(dependencies, live_hashes)
        summary['input_sha256'] = live_hashes
        for name, path in files.items():
            shutil.copyfile(path, inputs / name)
            if sha(inputs / name) != live_hashes[str(path)]:
                raise RuntimeError('Input changed while freezing: ' + str(path))
        summary['netlist_structure'] = check_netlist(inputs / 'soc_mapped.v',
                                                    [inputs / 'standard_model.v', inputs / 'sram_model.v'])
        working = case / 'source'
        shutil.copytree(DESIGN, working, ignore=shutil.ignore_patterns(
            'build', '.git', '__pycache__', '*.pyc', '*.vpd', '*.vcd', 'ucli.key'))
        original_sources = {str(path): sha(path) for path in source_tree_files(DESIGN)}
        working_sources = {str(path.relative_to(working)): sha(path) for path in source_tree_files(working)}
        if {str(Path(name).relative_to(DESIGN)): digest for name, digest in original_sources.items()} != working_sources:
            raise RuntimeError('The isolated source tree differs from the current engineering sources')
        summary['source_tree_sha256'] = original_sources
        summary['frozen_source_tree_sha256'] = working_sources
        # The exact conversion input RTL/headers/configuration must match this
        # program run, including all files from the frozen include directories.
        matched_conversion = {}
        for name, digest in read_summary['conversion']['input_sha256'].items():
            path = Path(name)
            if path.is_relative_to(DESIGN) and 'build' not in path.relative_to(DESIGN).parts:
                frozen = working / path.relative_to(DESIGN)
                if not frozen.is_file() or sha(frozen) != digest:
                    raise RuntimeError('The RTL/configuration differs from synthesis: ' + name)
                matched_conversion[name] = digest
        summary['synthesis_source_correspondence_sha256'] = matched_conversion
        project, vendor = working / 'project', working / 'vendor/Cores-VeeR-EL2'
        env.update(RV_ROOT=str(vendor))
        prefix = env.get('RISCV_PREFIX', 'riscv-none-elf-')
        common = ['PREPARE_ARGS=--skip-config', 'RV_ROOT=' + str(vendor),
                  'PYTHON=' + sys.executable, 'RISCV_PREFIX=' + prefix, 'ISA=rv32imc_zicsr']
        run(['make', 'prepare', *common], project, case / 'prepare.log', 'prepare_frozen_configuration', 120)
        expected_source_list = read_summary['conversion']['input_sha256'].get(str(PROJECT / 'build/synth.f'))
        restored = (project / 'build/synth.f').read_text().replace(str(project), str(PROJECT)).replace(
            str(vendor), str(DESIGN / 'vendor/Cores-VeeR-EL2'))
        if hashlib.sha256(restored.encode()).hexdigest() != expected_source_list:
            raise RuntimeError('Prepared compile order/configuration differs from the synthesis source list')
        summary['normalized_synthesis_source_list_sha256'] = expected_source_list
        run(['make', '-B', 'sw', *common], project, case / 'firmware.log', 'compile_riscv_firmware', 120)
        firmware = inputs / 'program.hex'
        shutil.copyfile(project / 'build/program.hex', firmware)
        summary['firmware_artifacts_sha256'] = {str(path.relative_to(case)): sha(path)
            for path in (project / 'build').iterdir() if path.is_file() and
            path.name in {'program.hex', 'program.bin', 'pe_smoke.elf', 'pe_smoke.dis', 'pe_smoke.map', 'rtl.f', 'synth.f'}}
        summary['firmware_hex_sha256'] = sha(firmware)
        summary['model_adaptation'] = prepare_models(inputs / 'standard_model.v', inputs / 'sram_model.v', case / 'models')
        compat = case / 'pthread_yield_compat.o'
        run(['/usr/bin/gcc', '-fPIC', '-c', inputs / 'compat.c', '-o', compat],
            case, 'compatibility.log', 'compile_vcs_host_compatibility', 60)
        traces, checks = [], []
        for name in ('rtl', 'gate'):
            folder = case / name
            folder.mkdir()
            common_vcs = [sys.executable, inputs / 'vcs_vm.py', '-full64', '-sverilog',
                          '-timescale=1ns/1ps', '+define+VCS', '+nospecify', '-debug_access',
                          '+define+AXI_RX_TIMING_CHECK', '+define+AXI_RAM_OUTPUT_DELAY_NS=1.0',
                          '+incdir+' + str(project / 'snapshots/soc'),
                          '-assert', 'svaext', '-top', 'tb_soc', '-top', 'soc_program_monitor',
                          '-Mdir=' + str(folder / 'csrc'), '-o', folder / 'simv', '-LDFLAGS', str(compat)]
            if name == 'rtl':
                design_args = ['+define+PE_REFERENCE_MACRO', '-f', project / 'build/rtl.f', inputs / 'reference_sram.v']
            else:
                design_args = ['+define+TETRAMAX+GATE_SIM', inputs / 'soc_mapped.v',
                               case / 'models/standard_model.v', case / 'models/sram_model.v']
            compile_log = run([*common_vcs, *design_args, inputs / 'axi_ram_model.sv',
                               inputs / 'tb_soc.sv', inputs / 'soc_program_monitor.sv'],
                              folder, 'compile.log', name + '_compile', args.timeout)
            trace = folder / 'trace.tsv'
            simulation_log = run([folder / 'simv', '+MEM_HEX=' + str(firmware), '+TRACE=' + str(trace)],
                                 folder, 'run.log', name + '_simulate', args.timeout)
            counts = inspect_simulation(simulation_log, trace, name)
            diagnostics = dict(Counter(re.findall(r'(?:Warning|Lint)-\[([^]]+)\]', compile_log + '\n' + simulation_log)))
            summary['cases'].append({'case': name, 'pass': True, 'checks': counts,
                                     'diagnostic_codes': diagnostics,
                                     'diagnostics_scope': 'All diagnostic codes retained for explicit review; no errors or runtime violations accepted'})
            checks.append(counts)
            traces.append(trace)
            save(case / 'summary.json', summary)
            print('SOC_' + name.upper() + '_PROGRAM_FUNCTIONAL_PASS pe_reads=64 pe_writes=87', flush=True)
        if checks[0] != checks[1] or traces[0].read_bytes() != traces[1].read_bytes():
            raise RuntimeError('Original RTL and mapped SoC accepted-transaction traces differ')
        check_hashes(merge_hashes(dependencies, live_hashes, original_sources))
        if {str(path.relative_to(working)): sha(path) for path in source_tree_files(working)} != working_sources:
            raise RuntimeError('The frozen source tree changed during program simulation')
        for name, path in files.items():
            if sha(inputs / name) != live_hashes[str(path)]:
                raise RuntimeError('A frozen input changed: ' + name)
        for filename, digest in summary['firmware_artifacts_sha256'].items():
            if sha(case / filename) != digest:
                raise RuntimeError('Firmware or prepared source list changed: ' + filename)
        if sha(firmware) != summary['firmware_hex_sha256']:
            raise RuntimeError('The firmware image changed during simulation')
        summary.update(status='PASS', soc_program_simulated=True, output_traces_match=True,
                       reference_pe_output_words_checked=64, pe_config_writes_checked=87,
                       output_sha256={str(path.relative_to(case)): sha(path)
                                      for name in ('rtl', 'gate') for path in (case / name).iterdir()
                                      if path.is_file() and path.name in {'run.log', 'compile.log', 'trace.tsv'}})
        save(case / 'summary.json', summary)
        print('SOC_GATE_FUNCTIONAL_PASS', flush=True)
        print('SOC_FUNCTIONAL_ONLY: one smoke program and 1 ns memory responses; no SDF, timing or formal acceptance.', flush=True)
        return 0
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(case / 'summary.json', summary)
        print('SOC_GATE_FUNCTIONAL_FAILED: ' + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    def terminated(signum, frame):
        raise InterruptedError('Received signal ' + str(signum))
    signal.signal(signal.SIGTERM, terminated)
    raise SystemExit(main())
