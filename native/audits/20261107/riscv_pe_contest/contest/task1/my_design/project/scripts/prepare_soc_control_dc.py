#!/usr/bin/env python3
"""Prepare CPU/router generic logic and the exact boundary to the mapped PE.

This is a generic synthesis compatibility path for old DC, not just syntax
translation. The PE definition is specialized before becoming a temporary
blackbox. The DC importer must replace that interface with the real mapped DDC.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import time

PROJECT = Path(__file__).resolve().parents[1]
DESIGN = PROJECT.parent


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n',
                          encoding='utf-8')


def run(command, log, *, stdout=None):
    """Close input and cap only our own tool child; never touch interactive DC."""
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))

    started = time.monotonic()
    print('SOC_CONTROL_PREP_PHASE=' + Path(command[0]).name, flush=True)
    with Path(log).open('w', encoding='utf-8') as errors:
        result_stream = Path(stdout).open('w', encoding='utf-8') if stdout else errors
        try:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                     stdout=result_stream, stderr=errors,
                                     start_new_session=True, preexec_fn=limits)
            try:
                while True:
                    remaining = 600 - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError('Tool exceeded 600 seconds: ' + str(log))
                    try:
                        code = child.wait(timeout=min(30, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        print('SOC_CONTROL_PREP_RUNNING tool=' + Path(command[0]).name
                              + ' elapsed_s=' + str(int(time.monotonic() - started)),
                              flush=True)
            except BaseException:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                raise
        finally:
            if stdout:
                result_stream.close()
    if code != 0:
        raise RuntimeError('Tool exited ' + str(code) + '; inspect ' + str(log))
    return {'command': command, 'exit_code': code,
            'elapsed_s': round(time.monotonic() - started, 3)}


def sig(module):
    return {name: {'direction': port['direction'], 'width': len(port['bits'])}
            for name, port in module['ports'].items()}


def ysquote(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"') + '"'


def prepare(output, sv2v, yosys):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError('Preparation output must be empty: ' + str(output))
    source_list = PROJECT / 'build/synth.f'
    includes, defines, sources = [], [], []
    files = {source_list, Path(__file__).resolve(),
             PROJECT / 'scripts/fold_verilog_literal_slices.py',
             PROJECT / 'synth/sram_blackbox.v'}
    source_list_bytes = source_list.read_bytes()
    for line in source_list_bytes.decode('utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('//') or line == '+libext+.v+.sv':
            continue
        if line.startswith('+incdir+'):
            directory = Path(line[8:]).resolve()
            includes.append(directory)
            # Generated Verilog headers can use .h (e.g. pic_map_auto.h).
            # Freeze every include-directory file so suffixes cannot omit one.
            files.update(path.resolve() for path in directory.rglob('*') if path.is_file())
        elif line.startswith('+define+'):
            defines.append(line[8:])
        elif line.startswith(('+', '-')):
            raise RuntimeError('Unsupported source-list line: ' + line)
        else:
            path = Path(line).resolve()
            if path.name in {'sram_reference_model.v', 'axi_ram_model.sv',
                             'tb_soc.sv', 'rfsp16x8_legacy.v', 'sram_blackbox.v'}:
                raise RuntimeError('Unexpected simulation or stub source: ' + str(path))
            sources.append(path)
            files.add(path)
    if 'SYNTHESIS' not in defines or 'RV_OPENSOURCE' not in defines:
        raise RuntimeError('Expected frozen synthesis source-list defines')
    if any(x.split('=')[0] in {'PE_REFERENCE_MACRO', 'PE_LEGACY_RAM'} for x in defines):
        raise RuntimeError('SRAM mode must not be embedded in shared source list')
    config = PROJECT / 'snapshots/soc/common_defines.vh'
    config_bytes = config.read_bytes()
    config_text = config_bytes.decode('utf-8')
    if not re.search(r'^\s*`define\s+RV_BUILD_AXI4\b', config_text, re.M):
        raise RuntimeError('Expected AXI CPU configuration')
    for feature in ('RV_ICCM_ENABLE', 'RV_DCCM_ENABLE', 'RV_ICACHE_ENABLE',
                    'RV_LOCKSTEP_ENABLE'):
        if re.search(r'^\s*`define\s+' + feature + r'\b', config_text, re.M):
            raise RuntimeError('Unexpected enabled CPU feature: ' + feature)
    inputs = {str(path): sha(path) for path in sorted(files)}
    if inputs[str(source_list)] != hashlib.sha256(source_list_bytes).hexdigest():
        raise RuntimeError('Source list changed while it was being read')
    if inputs[str(config)] != hashlib.sha256(config_bytes).hexdigest():
        raise RuntimeError('CPU configuration changed while it was being read')
    frozen = {}
    destinations = {}
    for original, expected in inputs.items():
        path = Path(original)
        relative = path.relative_to(DESIGN)
        target = output / 'inputs/tree' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if sha(target) != expected:
            raise RuntimeError('Input changed during freeze: ' + original)
        frozen[str(target)] = expected
        destinations[original] = target
    if any(sha(path) != expected for path, expected in inputs.items()):
        raise RuntimeError('Inputs changed while freezing sources')
    save(output / 'source_hashes.json', inputs)
    save(output / 'frozen_hashes.json', frozen)
    frozen_args = ['-I' + str(output / 'inputs/tree' / d.relative_to(DESIGN))
                   for d in includes]
    frozen_args.extend('-D' + define for define in defines)
    frozen_args.extend(str(destinations[str(path)]) for path in sources)
    frozen_args.extend(['-DPE_REFERENCE_MACRO',
                        str(destinations[str(PROJECT / 'synth/sram_blackbox.v')])])
    raw = output / 'soc_sv2v.raw.v'
    converted = output / 'soc_sv2v.v'
    # The shipped Haskell binary otherwise starts one runtime capability per
    # virtual CPU; its OS threads exceed the bounded virtual address budget.
    commands = [run([sv2v, '+RTS', '-N1', '-M1G', '-RTS', *frozen_args],
                    output / 'sv2v.log', stdout=raw)]
    if (output / 'sv2v.log').read_text().strip():
        raise RuntimeError('Review unexpected sv2v diagnostics: ' + str(output / 'sv2v.log'))
    helper = destinations[str(PROJECT / 'scripts/fold_verilog_literal_slices.py')]
    spec = importlib.util.spec_from_file_location('frozen_literal_folds', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folded, edits = module.fold_literal_slices(raw.read_text(encoding='utf-8'))
    if module.fold_literal_slices(folded) != (folded, []):
        raise RuntimeError('Literal slice conversion is not idempotent')
    converted.write_text(folded, encoding='utf-8')
    save(output / 'literal_slice_folds.json', {
        'raw_sha256': sha(raw), 'processed_sha256': sha(converted),
        'replacement_count': len(edits), 'edits': edits})
    script = output / 'prepare.ys'
    # hierarchy FIRST specializes ID_WIDTH=3; blackboxing the unspecialized
    # default wrapper would preserve a wrong 4-bit interface.
    script.write_text('\n'.join([
        'read_verilog ' + ysquote(converted),
        'hierarchy -check -top soc_top',
        'blackbox *sram_multiplier_system',
        'proc',
        'write_json ' + ysquote(output / 'specialized_hierarchy.json'),
        'flatten', 'opt', 'memory', 'opt',
        'techmap', 'opt', 'hierarchy -check -top soc_top', 'check -assert', 'stat',
        'write_verilog -noattr ' + ysquote(output / 'soc_control_dc.v'),
        'write_json ' + ysquote(output / 'soc_control_dc.json'), '']), encoding='utf-8')
    commands.append(run([yosys, '-s', str(script)], output / 'yosys.log'))
    log_text = (output / 'yosys.log').read_text(encoding='utf-8')
    if 'Found and reported 0 problems.' not in log_text:
        raise RuntimeError('Missing successful Yosys check-assert evidence')
    if re.search(r'(?m)^(Warning:|ERROR:)', log_text):
        raise RuntimeError('Unexpected Yosys diagnostics; inspect yosys.log')
    data = json.loads((output / 'soc_control_dc.json').read_text(encoding='utf-8'))
    original = json.loads((output / 'specialized_hierarchy.json').read_text(encoding='utf-8'))
    original_top = original['modules']['soc_top']
    original_boundary = original_top['cells']['u_pe']
    original_pe = original['modules'][original_boundary['type']]
    modules = data['modules']
    top = modules['soc_top']
    boundary = top['cells'].get('u_pe')
    if not boundary or boundary['type'] not in modules:
        raise RuntimeError('Missing specialized u_pe boundary')
    pe = modules[boundary['type']]
    params = {k: int(v, 2) for k, v in pe.get('parameter_default_values', {}).items()}
    if params != {'ID_WIDTH': 3, 'M': 16, 'N': 16, 'NUM_PE': 256}:
        raise RuntimeError('Unexpected specialized PE parameters: ' + str(params))
    if str(pe.get('attributes', {}).get('blackbox', '0')).strip('0') != '1':
        raise RuntimeError('Specialized u_pe is not the expected temporary blackbox')
    if pe.get('cells') or pe.get('memories') or pe.get('processes'):
        raise RuntimeError('PE implementation leaked into CPU/router generic logic')
    pe_sig = sig(pe)
    if boundary['type'] != original_boundary['type'] or pe_sig != sig(original_pe):
        raise RuntimeError('The original specialized PE boundary signature changed')
    if len(pe_sig) != 31 or any(pe_sig[name] != {'direction': direction, 'width': 3}
                               for name, direction in [('s_axi_awid', 'input'),
                                                       ('s_axi_arid', 'input'),
                                                       ('s_axi_bid', 'output'),
                                                       ('s_axi_rid', 'output')]):
        raise RuntimeError('Incorrect specialized PE interface')
    if set(boundary['connections']) != set(pe_sig):
        raise RuntimeError('Incomplete PE connections')
    for name, port in pe_sig.items():
        if len(boundary['connections'][name]) != port['width']:
            raise RuntimeError('Incorrect PE connection width: ' + name)
        if boundary['port_directions'][name] != port['direction']:
            raise RuntimeError('Incorrect PE connection direction: ' + name)
    if top.get('memories') or top.get('processes'):
        raise RuntimeError('Unexpected residual top process/memory')
    generic = Counter()
    scope_records = 0
    for name, cell in top['cells'].items():
        if name == 'u_pe':
            continue
        if cell['type'] == '$scopeinfo':
            scope_records += 1
            continue
        if not cell['type'].startswith('$_'):
            raise RuntimeError('Unexpected residual cell: ' + cell['type'])
        generic[cell['type']] += 1
    if not generic or not any(name.startswith('$_DFF') for name in generic):
        raise RuntimeError('CPU sequential logic is missing')
    top_sig = sig(top)
    if top_sig != sig(original_top):
        raise RuntimeError('The original complete SoC top port signature changed')
    if len(top_sig) != 44 or top_sig.get('clk') != {'direction': 'input', 'width': 1}:
        raise RuntimeError('Unexpected SoC top interface')
    # This stub is used only to preserve pin widths/directions before change_link.
    # Final DC must replace it and verify that no empty reachable designs remain.
    ports = ', '.join(pe_sig)
    stub = ['// Temporary specialized boundary; bind to the real mapped DDC.',
            '(* blackbox *) module \\' + boundary['type'] + ' (' + ports + ');']
    for name, port in pe_sig.items():
        width = '' if port['width'] == 1 else '[' + str(port['width'] - 1) + ':0] '
        stub.append('  ' + port['direction'] + ' wire ' + width + name + ';')
    stub.append('endmodule\n')
    (output / 'pe_boundary_stub.v').write_text('\n'.join(stub), encoding='utf-8')
    if any(sha(path) != expected for path, expected in inputs.items()):
        raise RuntimeError('Original sources changed during preparation')
    if any(sha(path) != expected for path, expected in frozen.items()):
        raise RuntimeError('Frozen inputs changed during preparation')
    generated_names = ('soc_sv2v.raw.v', 'soc_sv2v.v', 'soc_control_dc.v',
                       'soc_control_dc.json', 'pe_boundary_stub.v', 'prepare.ys',
                       'sv2v.log', 'yosys.log', 'literal_slice_folds.json',
                       'source_hashes.json', 'frozen_hashes.json',
                       'specialized_hierarchy.json')
    manifest = {
        'status': 'PREPARED', 'top': 'soc_top', 'top_ports': top_sig,
        'original_top_ports': sig(original_top),
        'original_pe_ports': sig(original_pe),
        'pe_boundary': {'instance': 'u_pe', 'source_reference': boundary['type'],
                        'parameters': params, 'ports': pe_sig,
                        'connections': boundary['connections']},
        'generic_cell_count': sum(generic.values()), 'generic_cell_types': generic,
        'scope_metadata_records': scope_records,
        'module_count': len(modules), 'check_assert_pass': True,
        'source_list_sha256': inputs[str(source_list)],
        'input_sha256': inputs, 'frozen_input_sha256': frozen,
        'generated_sha256': {name: sha(output / name) for name in generated_names},
        'commands': commands, 'formal_equivalence_checked': False,
        'tool_address_space_limit_bytes': 3 * 1024**3,
        'technology_mapping_done': False,
        'scope': 'Generic synthesis of CPU/router with actual SoC tieoffs; '
                 'CPU internal hierarchy flattened, PE temporary specialized '
                 'boundary must bind to real mapped DDC; no mapping/STA/SDF '
                 'or program execution claim.',
    }
    save(output / 'conversion.json', manifest)
    print('SOC_CONTROL_DC_PREPARED generic_cells=' + str(sum(generic.values()))
          + ' top_ports=' + str(len(top_sig)) + ' pe_ports=' + str(len(pe_sig)), flush=True)
    return manifest


def main():
    def interrupted(signum, frame):
        raise InterruptedError('Preparation cancelled by signal ' + str(signum))

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--sv2v', default=os.environ.get('TASK1_SV2V', 'sv2v'))
    parser.add_argument('--yosys', default=os.environ.get('TASK1_YOSYS', 'yosys'))
    args = parser.parse_args()
    prepare(args.output_dir, args.sv2v, args.yosys)


if __name__ == '__main__':
    main()
