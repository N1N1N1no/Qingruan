#!/usr/bin/env python3
"""Read CPU/router control around the previously verified mapped accelerator.

This stage only imports and links the SoC. CPU/control mapping, SoC timing and
program simulation are separate subsequent stages.
"""
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
TASK = PROJECT.parents[1]
EXPORT = PROJECT / 'build/dc_pe_axi_export'
LIMIT = 6 * 1024**3


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def limit_child():
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))


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


def bit_ports(module):
    """Represent Yosys port bits exactly as DC bit-port names."""
    result = []
    for name, port in module['ports'].items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise RuntimeError('Unexpected port identifier: ' + name)
        direction = {'input': 'in', 'output': 'out', 'inout': 'inout'}[port['direction']]
        size = len(port['bits'])
        offset = port.get('offset', 0)
        if size == 1 and not port.get('offset'):
            result.append((name, direction))
        else:
            result.extend((f'{name}[{offset+i}]', direction) for i in range(size))
    return sorted(result)


def main():
    result = Path(tempfile.mkdtemp(prefix='soc_read.', dir=TASK / 'work'))
    print('RESULT=' + str(result), flush=True)
    inputs = result / 'inputs'
    inputs.mkdir()
    summary = {'status': 'RUNNING', 'scope': 'SoC import and linking only',
               'cpu_included': True, 'router_included': True,
               'cpu_control_mapped': False, 'soc_timing_verified': False,
               'soc_program_simulated': False, 'formal_equivalence': False,
               'child_address_space_limit_bytes': LIMIT, 'commands': []}
    env = os.environ.copy()
    env.update(LC_ALL='C', SOC_READ_CASE=str(result))

    def run(command, cwd, log_name, phase, timeout=600):
        log_path = cwd / log_name
        record = {'argv': list(map(str, command)), 'cwd': str(cwd),
                  'log': str(log_path), 'phase': phase, 'timeout_s': timeout}
        summary['commands'].append(record)
        print('SOC_READ_PHASE=' + phase, flush=True)
        start = time.monotonic()
        with log_path.open('w') as log:
            process = subprocess.Popen(record['argv'], cwd=cwd, env=env,
                                       stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True,
                                       preexec_fn=limit_child)
            try:
                while True:
                    try:
                        code = process.wait(timeout=15)
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = time.monotonic() - start
                        print(f'SOC_READ_RUNNING phase={phase} elapsed_s={int(elapsed)}', flush=True)
                        if elapsed >= timeout:
                            raise RuntimeError(f'{phase} timed out after {timeout} seconds')
            except BaseException:
                stop_child(process)
                record.update(exit_code=process.returncode, elapsed_s=round(time.monotonic()-start, 2))
                raise
        record.update(exit_code=code, elapsed_s=round(time.monotonic()-start, 2))
        if code:
            raise RuntimeError(f'{phase} exited {code}; inspect {log_path}')

    try:
        files = {
            'pe_axi_mapped.ddc': EXPORT / 'pe_axi_mapped.ddc',
            'pe_axi_mapped.v': EXPORT / 'pe_axi_mapped.v',
            'artifact_summary.json': EXPORT / 'artifact_summary.json',
            'standard.db': PROJECT / 'build/dc_libraries/standard.db',
            'sram.db': PROJECT / 'build/dc_libraries/sram.db',
            'read_soc_control_batch.tcl': PROJECT / 'synth/read_soc_control_batch.tcl',
            'pe_axi_fingerprint.tcl': PROJECT / 'synth/pe_axi_fingerprint.tcl',
            'run_soc_read.py': Path(__file__),
            'prepare_soc_control_dc.py': PROJECT / 'scripts/prepare_soc_control_dc.py',
            'fold_verilog_literal_slices.py': PROJECT / 'scripts/fold_verilog_literal_slices.py',
            'run_soc_read.sh': PROJECT / 'synth/run_soc_read.sh',
            'run_soc_read.tcl': PROJECT / 'synth/run_soc_read.tcl',
        }
        for index, path in enumerate((TASK.parents[0] / 'env.sh',
                                      TASK.parents[0] / 'runtime/env.vm.sh',
                                      TASK.parents[0] / 'runtime/env.scl2018.sh',
                                      TASK.parents[0] / 'runtime/env.dcpt.sh')):
            files[f'environment_{index}.sh'] = path
        original_hashes = {str(path): sha(path) for path in files.values()}
        for name, path in files.items():
            shutil.copyfile(path, inputs / name)
            if sha(inputs / name) != original_hashes[str(path)]:
                raise RuntimeError('Input changed while freezing: ' + str(path))
        summary['input_sha256'] = original_hashes
        export = json.loads((inputs / 'artifact_summary.json').read_text())
        if export.get('status') != 'EXPORTED' or export.get('top') != 'sram_multiplier_system_ID_WIDTH3':
            raise RuntimeError('Expected a complete AXI system export')
        for name in ('pe_axi_mapped.ddc', 'pe_axi_mapped.v'):
            if sha(inputs / name) != export['artifacts'][name]['sha256']:
                raise RuntimeError('Export manifest mismatch: ' + name)
        for name in ('standard.db', 'sram.db'):
            if sha(inputs / name) != export['checker_dependencies'].get(str(files[name])):
                raise RuntimeError('Library differs from the mapped AXI export: ' + name)
        # Tie reuse to an actual passed gate run and the current accelerator RTL.
        pe_sources = [PROJECT / 'rtl' / name for name in (
            'rfsp16x8.v', 'Exact_8bit.v', 'adder.v', 'pe_1sram_4mul.v',
            'pe_array.v', 'pe_axi_wrapper.v')]
        required_hashes = {str(path): sha(path) for path in pe_sources}
        required_hashes.update({str(files[name]): sha(inputs / name) for name in (
            'pe_axi_mapped.ddc', 'pe_axi_mapped.v', 'artifact_summary.json')})
        prior_case = None
        for candidate in sorted((TASK / 'work').glob('pe_axi_sdf.*'),
                                key=lambda p: p.stat().st_mtime, reverse=True):
            prior_file = candidate / 'summary.json'
            if not prior_file.is_file():
                continue
            prior = json.loads(prior_file.read_text())
            if (prior.get('status') == 'PASS' and prior.get('sdf_annotated') is True and
                    all(prior.get('input_sha256', {}).get(name) == value
                        for name, value in required_hashes.items())):
                prior_case = candidate
                shutil.copyfile(prior_file, inputs / 'prior_axi_sdf_summary.json')
                break
        if prior_case is None:
            raise RuntimeError('No passed AXI SDF run matches this export and accelerator RTL')
        summary['reused_axi_sdf_case'] = str(prior_case)
        summary['reused_accelerator_source_sha256'] = required_hashes

        run([sys.executable, str(PROJECT / 'scripts/prepare_soc_control_dc.py'),
             '--output-dir', str(result / 'prepared')], result, 'prepare.log', 'prepare_cpu_router', timeout=1300)
        conversion = json.loads((result / 'prepared/conversion.json').read_text())
        if conversion.get('status') != 'PREPARED' or conversion.get('check_assert_pass') is not True:
            raise RuntimeError('CPU/router generic structural check did not pass')
        for name, expected_hash in conversion['generated_sha256'].items():
            if sha(result / 'prepared' / name) != expected_hash:
                raise RuntimeError('Prepared manifest mismatch: ' + name)
        data = json.loads((result / 'prepared/soc_control_dc.json').read_text())
        modules = data['modules']
        top = modules['soc_top']
        pe = top['cells']['u_pe']
        blackbox = modules[pe['type']]
        top_ports = bit_ports(top)
        pe_ports = bit_ports(blackbox)
        if len(pe_ports) != 256 or len(top['cells']) < 2:
            raise RuntimeError('Incomplete specialized accelerator or CPU/router control')
        expected = []
        for key, values in (('expected_top_ports', top_ports), ('expected_pe_ports', pe_ports)):
            expected.append('set ' + key + ' {' + ' '.join('{' + name + ' ' + direction + '}'
                                                         for name, direction in values) + '}')
        source_design = export['source_design']
        for key, value in {
            'expected_wrapper_top': export['top'],
            'expected_array_reference': source_design['array_reference'],
            'expected_array_fingerprint': source_design['array_fingerprint_before'],
            'expected_sram_count': 256, 'expected_pe_count': 256,
            'expected_reused_physical_leaf_cells': int(source_design['physical_leaf_cells']),
            'expected_reused_virtual_constants': int(source_design['virtual_constant_leaf_cells']),
        }.items():
            if not re.fullmatch(r'[A-Za-z0-9_]+', str(value)):
                raise RuntimeError('Unexpected Tcl expectation value')
            expected.append('set ' + key + ' {' + str(value) + '}')
        (result / 'expected.tcl').write_text('\n'.join(expected) + '\n')
        summary['top_port_bits'] = len(top_ports)
        summary['accelerator_port_bits'] = len(pe_ports)
        prepared_hashes = {str(path): sha(path) for path in (result / 'prepared').rglob('*') if path.is_file()}
        prepared_hashes[str(result / 'expected.tcl')] = sha(result / 'expected.tcl')
        (result / 'dc').mkdir()
        run(['dc_shell', '-f', str(inputs / 'read_soc_control_batch.tcl')],
            result / 'dc', 'run.log', 'read_link_soc')
        log = (result / 'dc/run.log').read_text(errors='replace')
        if (len(re.findall(r'^DC_READ_SOC_CONTROL_PASS\b', log, re.M)) != 1 or
                re.search(r'(?im)^\s*(?:Error:|Fatal:|Internal error)', log)):
            raise RuntimeError('Missing SoC import completion or DC error; inspect dc/run.log')
        warnings = {}
        for path in (result / 'dc').iterdir():
            if path.suffix not in ('.log', '.rpt'):
                continue
            text = path.read_text(errors='replace')
            if re.search(r'(?im)^\s*(?:Error:|Fatal:|Internal error)|'
                         r'\((?:LNK-(?:005|043)|UID-341|LINK-5)\)|'
                         r'Unable to resolve reference|Warning:[^\n]*unresolved', text):
                raise RuntimeError('DC error/unresolved reference in ' + str(path))
            warnings[path.name] = len(re.findall(r'^\s*Warning:', text, re.M))
        summary['dc_warning_lines_by_file'] = warnings
        reports = {}
        for line in (result / 'dc/read_summary.rpt').read_text().splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                reports[key] = value
        summary['dc_structure'] = reports
        for name in ('soc_elaborated.ddc', 'soc_elaborated.v'):
            path = result / 'dc' / name
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError('Missing linked SoC output: ' + name)
        for name, expected_hash in {**original_hashes, **required_hashes, **prepared_hashes,
                                    **conversion['input_sha256'],
                                    **conversion['frozen_input_sha256']}.items():
            if sha(name) != expected_hash:
                raise RuntimeError('Input changed during import: ' + name)
        summary['conversion'] = conversion
        summary['prepared_sha256'] = prepared_hashes
        summary['output_sha256'] = {
            str(path.relative_to(result)): sha(path) for path in (result / 'dc').iterdir()
            if path.is_file() and path.suffix in ('.ddc', '.v', '.rpt', '.tsv', '.txt')}
        summary['status'] = 'PASS'
        save(result / 'summary.json', summary)
        print('DC_READ_SOC_PASS top=soc_top sram_instances=256 cpu_control_mapped=false', flush=True)
        print('SOC_READ_ONLY: CPU/control synthesis, SoC timing and program simulation remain.', flush=True)
        return 0
    except BaseException as error:
        summary.update(status='FAIL', error=str(error))
        save(result / 'summary.json', summary)
        print('SOC_READ_FAILED: ' + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
