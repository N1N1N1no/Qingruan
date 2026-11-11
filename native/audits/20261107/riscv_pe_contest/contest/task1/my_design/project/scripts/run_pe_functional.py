#!/usr/bin/env python3
"""Compare original and mapped PE outputs, with specify disabled and no SDF."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
TASK = PROJECT.parents[1]
NETLIST = PROJECT / 'build/dc_pe_io/pe_mapped.v'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sources = {
        'netlist.v': NETLIST,
        'tb.sv': PROJECT / 'tests/tb_pe_gate_functional.sv',
        'rtl/rfsp16x8.v': PROJECT / 'rtl/rfsp16x8.v',
        'rtl/Exact_8bit.v': PROJECT / 'rtl/Exact_8bit.v',
        'rtl/adder.v': PROJECT / 'rtl/adder.v',
        'rtl/pe_1sram_4mul.v': PROJECT / 'rtl/pe_1sram_4mul.v',
        'reference_sram.v': PROJECT / 'tb/sram_reference_model.v',
        'standard_model.v': TASK / 'libraries/standard_timing.v',
        'sram_model.v': TASK / 'libraries/sram_timing.v',
        'compat.c': PROJECT.parent / 'compat/pthread_yield_compat.c',
        'vcs_vm.py': TASK / 'scripts/vcs_vm.py',
        'runner.py': Path(__file__).resolve(),
    }
    for path in sources.values():
        if not path.is_file():
            raise RuntimeError('Missing input: ' + str(path))
    work_root = TASK / 'work'
    work_root.mkdir(exist_ok=True)
    result = Path(tempfile.mkdtemp(prefix='pe_functional.', dir=work_root))
    print('RESULT=' + str(result), flush=True)
    summary = {
        'status': 'RUNNING',
        'scope': 'single PE functional simulation, specify disabled, no SDF',
        'netlist': str(NETLIST), 'cases': [],
        'sdf_annotated': False, 'formal_equivalence': False,
        'soc_simulated': False,
        'gate_model_mode': 'TETRAMAX: disable the library auxiliary input xbuf drivers',
    }
    before = {str(path): sha(path) for path in sources.values()}
    summary['input_sha256'] = before
    env = os.environ.copy()
    env['LC_ALL'] = 'C'

    def run(command, folder, log_name, timeout=180):
        with (folder / log_name).open('w') as log:
            completed = subprocess.run(list(map(str, command)), cwd=folder, env=env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        text = (folder / log_name).read_text(errors='replace')
        if completed.returncode or re.search(r'(?m)^\s*(?:Error-\[|Fatal:|FATAL:)', text):
            raise RuntimeError('Command failed; inspect ' + str(folder / log_name))
        return text

    try:
        snapshot = result / 'inputs'
        for name, path in sources.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if sha(target) != before[str(path)]:
                raise RuntimeError('Input changed while copying: ' + str(path))

        compat = result / 'pthread_yield_compat.o'
        run(['/usr/bin/gcc', '-fPIC', '-c', snapshot / 'compat.c', '-o', compat],
            result, 'compatibility.log')
        traces = []
        for name in ['rtl', 'gate']:
            case = result / name
            case.mkdir()
            if name == 'rtl':
                design_sources = [snapshot / ('rtl/' + file) for file in
                                  ['rfsp16x8.v', 'Exact_8bit.v', 'adder.v', 'pe_1sram_4mul.v']]
                design_sources += [snapshot / 'reference_sram.v']
                defines = ['+define+PE_REFERENCE_MACRO']
            else:
                design_sources = [snapshot / 'netlist.v', snapshot / 'standard_model.v',
                                  snapshot / 'sram_model.v']
                # Library-supported functional mode. Its normal xbuf helpers
                # feed back onto reset input pins and conflict when reset is
                # asserted again. Keep the actual sequential/reset primitives.
                defines = ['+define+TETRAMAX']
            run([sys.executable, snapshot / 'vcs_vm.py', '-full64', '-sverilog',
                 '-timescale=1ns/1ps', '+nospecify', *defines,
                 '-top', 'tb_pe_gate_functional', '-Mdir=' + str(case / 'csrc'),
                 '-o', case / 'simv', '-LDFLAGS', str(compat),
                 *design_sources, snapshot / 'tb.sv'], case, 'compile.log')
            trace = case / 'outputs.txt'
            log = run([case / 'simv', '+TRACE_FILE=' + str(trace)], case, 'run.log')
            match = re.search(
                r'^PE_FUNCTIONAL_TEST_PASS cycles=(\d+) pairs=(\d+) reset_checks=(\d+)$',
                log, re.M)
            if not match or 'SRAM_TIMING_VIOLATION' in log:
                raise RuntimeError('Functional check failed; inspect ' + str(case / 'run.log'))
            cycles, pairs, resets = map(int, match.groups())
            if pairs != 65536 or cycles < 18000 or resets != 1:
                raise RuntimeError('Test coverage incomplete: ' + name)
            if len(trace.read_text().splitlines()) != cycles:
                raise RuntimeError('Incomplete output trace: ' + name)
            traces.append(trace)
            summary['cases'].append({'case': name, 'pass': True, 'checked_cycles': cycles,
                                     'operand_pairs': pairs, 'trace_sha256': sha(trace)})
            print('PE_' + name.upper() + '_FUNCTIONAL_PASS cycles=' + str(cycles), flush=True)
        if sha(traces[0]) != sha(traces[1]):
            raise RuntimeError('RTL and gate output traces differ: ' + str(result))
        if any(sha(Path(path)) != expected for path, expected in before.items()):
            raise RuntimeError('Inputs changed during the test')
        summary.update(status='PASS', output_traces_match=True)
        print('PE_GATE_FUNCTIONAL_PASS', flush=True)
    except BaseException as exc:
        summary.update(status='FAIL', error=str(exc))
        raise
    finally:
        (result / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PE_GATE_FUNCTIONAL_FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
