#!/usr/bin/env python3
"""Audit the narrow, source-proven VCS warning set for the whole-SoC run.

Original compiler, runtime and annotation logs are never rewritten. Unreviewed
SDF diagnostics are rejected; accepting one later requires a source/SDF audit.
"""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re


def _need(condition, explanation):
    if not condition:
        raise RuntimeError('SoC diagnostic review: ' + explanation)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _blocks(text, code):
    return re.findall(r'(?ms)^Warning-\[' + re.escape(code) + r'\].*?(?=\n\n|\Z)', text)


def _declarations(text):
    modules = {}
    for match in re.finditer(r'(?ms)^module\s+(\w+)\b(.*?)^endmodule\b', text):
        name, body = match.groups()
        body = re.sub(r'/\*.*?\*/|//[^\n]*', '', body, flags=re.S)
        header = re.match(r'\s*\((.*?)\)\s*;', body, flags=re.S)
        _need(header is not None, 'unsupported module header ' + name)
        pins = {}
        if re.search(r'\b(?:input|output|inout)\b', header[1]):
            direction = None
            for chunk in header[1].split(','):
                declared = re.search(r'\b(input|output|inout)\b', chunk)
                if declared:
                    direction = declared[1]
                pin = re.sub(r'\[[^]]*\]|\b(?:input|output|inout|wire|reg|logic|signed|unsigned)\b', '', chunk).strip()
                _need(direction is not None and re.fullmatch(r'\w+', pin) is not None,
                      'unsupported ANSI pin in ' + name)
                _need(pin not in pins, 'duplicate ANSI pin ' + name + '.' + pin)
                pins[pin] = direction
        else:
            for direction, declaration in re.findall(r'(?m)^\s*(input|output|inout)\s+([^;]+);', body):
                declaration = re.sub(r'\[[^]]*\]|\b(?:wire|reg|logic|signed|unsigned)\b', '', declaration)
                for pin in declaration.split(','):
                    pin = pin.strip()
                    _need(re.fullmatch(r'\w+', pin) is not None and pin not in pins,
                          'unsupported or duplicate pin ' + name + '.' + pin)
                    pins[pin] = direction
            _need({x.strip() for x in header[1].split(',') if x.strip()} == set(pins),
                  'incomplete non-ANSI port declarations in ' + name)
        _need(name not in modules, 'duplicate module declaration ' + name)
        modules[name] = pins
    return modules


def _location(message):
    location = re.search(r'(?m)^([^\n]+), (\d+)\n', message)
    _need(location is not None, 'warning without a source location')
    return Path(location[1]).resolve(), int(location[2])


def _gate_tfipc(case, compilation):
    netpath = case / 'inputs/soc_mapped.v'
    net = netpath.read_text()
    offsets, position = [], 0
    for line in net.splitlines(keepends=True):
        offsets.append(position)
        position += len(line)
    modules = _declarations(net)
    for path in (case / 'models/standard_model.v', case / 'models/sram_model.v'):
        declarations = _declarations(path.read_text())
        _need(not(set(modules) & set(declarations)), 'duplicate mapped/library module names')
        modules.update(declarations)
    warnings = _blocks(compilation, 'TFIPC')
    _need(len(warnings) == 12562, 'mapped TFIPC count differs from the frozen whole-SoC map')
    counts, seen, entries = Counter(), set(), []
    for warning in warnings:
        path, number = _location(warning)
        _need(path == netpath.resolve() and 1 <= number <= len(offsets),
              'TFIPC does not refer to the frozen mapped netlist')
        _need(number not in seen, 'duplicate TFIPC source line')
        seen.add(number)
        start = offsets[number - 1]
        end = net.find(';', start)
        _need(end >= start, 'unterminated TFIPC instance statement')
        statement = net[start:end + 1]
        instance = re.match(r'\s*(\w+)\s+(\\\S+|\w+)\s*\(', statement)
        _need(instance is not None, 'cannot parse TFIPC instance at line ' + str(number))
        cell, name = instance.groups()
        _need(cell in modules, 'TFIPC names an undeclared module ' + cell)
        connected = set(re.findall(r'\.(\w+)\s*\(', statement[instance.end():]))
        _need(connected <= set(modules[cell]), 'instance connects an undeclared port in ' + cell)
        for pin, direction in modules[cell].items():
            if direction in {'input', 'inout'}:
                _need(not re.search(r'\.' + re.escape(pin) + r'\s*\(\s*\)', statement),
                      'empty named connection on ' + direction + ' ' + cell + '.' + pin)
        missing = set(modules[cell]) - connected
        _need(bool(missing), 'TFIPC instance has no omitted named ports')
        for pin in sorted(missing):
            _need(modules[cell][pin] == 'output', 'omitted input/inout ' + cell + '.' + pin)
            if pin in {'Q', 'QN'}:
                _need(cell in {'DFFR_X1', 'DFFR_X2', 'SDFFR_X1'}, 'unreviewed unused flip-flop output ' + cell)
                if pin == 'Q':
                    _need(re.search(r'\.QN\s*\(\s*[^\s()]+\s*\)', statement) is not None,
                          'omitted Q has no connected complementary output at line ' + str(number))
            else:
                _need(pin == 'Cout' and re.fullmatch(r'pe_axi_reuse_\d+_\d+_CLA(?:16|32)bit_\d+', cell) is not None,
                      'unreviewed unused mapped output ' + cell + '.' + pin)
            counts[cell + '.' + pin] += 1
        entries.append({'line': number, 'cell': cell, 'instance': name, 'missing_outputs': sorted(missing)})
    return {'warning_count': len(warnings), 'netlist_sha256': _sha(netpath),
            'unused_output_counts': dict(sorted(counts.items())),
            'no_missing_input_or_inout': True, 'locations': entries}


def _rtl_tfipc(case, compilation):
    source = case / 'source'
    wrapper = source / 'vendor/Cores-VeeR-EL2/testbench/veer_wrapper.sv'
    socpath = source / 'project/rtl/soc_top.sv'
    defines = set(re.findall(r'(?m)^\s*`define\s+(\w+)',
                            (source / 'project/snapshots/soc/common_defines.vh').read_text()))
    filelist = (source / 'project/build/rtl.f').read_text()
    for values in re.findall(r'(?m)^\+define\+([^\s]+)', filelist):
        defines.update(value.split('=', 1)[0] for value in values.split('+'))
    _need({'RV_OPENSOURCE', 'SOC_CPU_HAS_CORE_ID'} <= defines, 'unexpected CPU compilation macros')
    raw = wrapper.read_text().split('module veer_wrapper', 1)[1].split(');', 1)[0]
    selected, stack, active = [], [], True
    for line in raw.splitlines():
        directive = re.match(r'\s*`(ifdef|ifndef|elsif|else|endif)\b(?:\s+(\w+))?', line)
        if not directive:
            if active:
                selected.append(line)
            continue
        command, macro = directive.groups()
        if command in {'ifdef', 'ifndef'}:
            selected_branch = macro in defines
            if command == 'ifndef':
                selected_branch = not selected_branch
            stack.append([active, selected_branch])
            active = active and selected_branch
        elif command == 'elsif':
            _need(bool(stack), 'unmatched CPU header elsif')
            outer, any_taken = stack[-1]
            selected_branch = not any_taken and macro in defines
            active = outer and selected_branch
            stack[-1][1] |= selected_branch
        elif command == 'else':
            _need(bool(stack), 'unmatched CPU header else')
            outer, any_taken = stack[-1]
            active = outer and not any_taken
            stack[-1][1] = True
        else:
            _need(bool(stack), 'unmatched CPU header endif')
            active = stack.pop()[0]
    _need(not stack, 'unterminated CPU port conditional')
    ports = {pin: direction for direction, pin in re.findall(
        r'(?m)^\s*(input|output|inout)\s+logic\s+(?:\[[^\n]*?\]\s*)*(\w+)\s*(?:,|$)', '\n'.join(selected))}
    soc = socpath.read_text()
    match = re.search(r'\bveer_wrapper\s+u_cpu\s*\((.*?)\n\s*\);', soc, re.S)
    _need(match is not None, 'cannot locate frozen CPU instance')
    connected = set(re.findall(r'\.(\w+)\s*\(', match[1]))
    _need(connected <= set(ports) and set(ports) - connected == {'dmi_active'} and
          ports['dmi_active'] == 'output', 'CPU omitted ports differ from the unused dmi_active output')
    warnings = _blocks(compilation, 'TFIPC')
    _need(len(warnings) == 1 and 'veer_wrapper u_cpu' in warnings[0], 'RTL TFIPC is not solely u_cpu')
    path, line = _location(warnings[0])
    _need(path == socpath.resolve() and line == soc[:match.start()].count('\n') + 1,
          'RTL TFIPC source location differs from u_cpu')
    return {'warning_count': 1, 'unused_output': 'veer_wrapper.dmi_active',
            'wrapper_port_count': len(ports), 'source': str(socpath),
            'wrapper_sha256': _sha(wrapper), 'no_missing_input_or_inout': True}


def _rtl_siob(case, compilation):
    array = case / 'source/project/rtl/pe_array.v'
    text = array.read_text()
    flat = re.sub(r'\s+', '', text)
    _need('.data_in((col==0)?data_in[row*32+:32]:data_wire[(row*N+col-1)*32+:32]),' in flat,
          'array first-column data branch differs from the reviewed expression')
    _need('.psum_in((row==0)?psum_in[col*128+:128]:psum_wire[((row-1)*N+col)*128+:128]),' in flat,
          'array first-row psum branch differs from the reviewed expression')
    instance_lines = [i for i, line in enumerate(text.splitlines(), 1)
                      if re.search(r'pe_1sram_4mul\s+u_pe\s*\(', line)]
    _need(len(instance_lines) == 1, 'expected one generated PE source instantiation')
    observed = Counter()
    warnings = _blocks(compilation, 'SIOB')
    _need(len(warnings) == 17, 'expected exactly seventeen constant-unselected array SIOB warnings')
    for warning in warnings:
        path, line = _location(warning)
        expression = re.search(r'(?m)^"([^"\n]+)"', warning)
        bound = re.search(r'out of declared bounds : \[(\d+):0\]', warning)
        _need(path == array.resolve() and line == instance_lines[0] and expression is not None and bound is not None,
              'SIOB source, expression or declaration bounds differ')
        _need('In module instance : u_pe_array' in warning and 'In module : pe_array.' in warning,
              'SIOB is outside the reviewed generated array')
        value = expression[1]
        if value.startswith('data_wire['):
            _need(value == 'data_wire[((((0 * N) + 0) - 1) * 32)+:32]' and bound[1] == '8191',
                  'unexpected first-column data selection warning')
        else:
            index = re.fullmatch(r'psum_wire\[\(\(\(\(0 - 1\) \* N\) \+ (\d+)\) \* 128\)\+:128\]', value)
            _need(index is not None and 0 <= int(index[1]) < 16 and bound[1] == '32767',
                  'unexpected first-row psum selection warning')
        observed[value] += 1
    _need(len(observed) == 17 and set(observed.values()) == {1}, 'repeated or missing SIOB expressions')
    return {'warning_count': 17, 'source': str(array), 'source_sha256': _sha(array),
            'instance_line': instance_lines[0], 'expressions': dict(observed),
            'explanation': 'Constant col==0 / row==0 selects the valid input branch, while VCS also diagnoses the unselected expression.'}


def _named_instances(body):
    instances = {}
    for match in re.finditer(r'(?ms)^\s*(\w+)\s+(\\\S+|\w+)\s*\((.*?)\)\s*;', body):
        reference, name, pins = match.groups()
        name = name.lstrip('\\')
        _need(name not in instances, 'duplicate mapped instance ' + name)
        connections = {}
        for pin, value in re.findall(r'\.(\w+)\s*\(\s*([^()]*)\s*\)', pins):
            _need(pin not in connections, 'duplicate named connection ' + name + '.' + pin)
            connections[pin] = re.sub(r'\s+', '', value)
        instances[name] = (reference, connections)
    return instances


def _swc_review(case, compilation, annotation):
    """Resolve only the 129 address-alias SWC notices against their SDF lines.

    Other warning types are deliberately not accepted by this helper; its
    caller separately rejects every diagnostic outside the approved set.
    """
    compile_warnings = _blocks(compilation, 'SDFCOM_SWC')
    annotation_warnings = _blocks(annotation, 'SDFCOM_SWC')
    _need(len(compile_warnings) == len(annotation_warnings) == 129 and
          Counter(compile_warnings) == Counter(annotation_warnings),
          'compile/annotation must contain the identical 129 SWC notices')
    netpath, sdfpath = case / 'inputs/soc_mapped.v', case / 'gate/soc.sdf'
    net = netpath.read_text()
    modules = {match[1]: match[2] for match in re.finditer(
        r'(?ms)^module\s+(\w+)\b(.*?)^endmodule\b', net)}
    _need('soc_top' in modules, 'mapped SoC top is absent')
    top = _named_instances(modules['soc_top'])
    _need('u_pe' in top and top['u_pe'][0] == 'sram_multiplier_system_ID_WIDTH3',
          'mapped accelerator identity differs')
    accelerator = top['u_pe']
    _need(accelerator[1].get('s_axi_araddr') == 'mem_axi_araddr',
          'accelerator read-address port is not the reviewed top wire')
    pe_body = modules[accelerator[0]]
    pe = _named_instances(pe_body)
    aliases = {}
    for alias, bit in re.findall(r'(?m)^\s*assign\s+(\w+)\s*=\s*s_axi_araddr\[(\d+)\]\s*;', pe_body):
        _need(alias not in aliases, 'duplicate accelerator address alias')
        aliases[alias] = int(bit)
    _need(aliases == {'N9033': 0, 'N9034': 1, 'N9035': 2, 'N6766': 3,
                      'N6767': 4, 'N6768': 5, 'N6769': 6, 'N6770': 7, 'N6771': 8},
          'accelerator pure address aliases differ from the reviewed map')
    declarations = _declarations(net)
    for model in (case / 'models/standard_model.v', case / 'models/sram_model.v'):
        declarations.update(_declarations(model.read_text()))
    notices, wanted, seen = [], set(), set()
    for warning in annotation_warnings:
        first = re.match(r'Warning-\[SDFCOM_SWC\] Simple Wire Connection\n'
                         r'(?P<file>[^\n,]+), (?P<line>\d+)\n'
                         r'module: (?P<module>\w+), "instance: (?P<scope>[^"\n]+)"\n', warning)
        _need(first is not None and first['file'] == sdfpath.name, 'unexpected SWC diagnostic header')
        detail = re.fullmatch(
            r'\s*SDF Warning: The path from\s+(.*?)\s+to\s+(.*?)\s+doesn\x27t have a\s+'
            r'simple\s+wire connection, delay will still\s+be annotated\.\s*', warning[first.end():], re.S)
        _need(detail is not None, 'SWC diagnostic does not state the delay remains annotated')
        source_text, destination_text = [re.sub(r'\s+', '', value) for value in detail.groups()]
        source_name, source_pin = source_text.lstrip('\\').rsplit('.', 1)
        destination = re.fullmatch(r'u_pe\.(\w+)\.(\w+)', destination_text)
        _need(destination is not None, 'SWC destination is outside the reviewed accelerator leaf scope')
        destination_name, destination_pin = destination.groups()
        _need(source_name in top and destination_name in pe, 'SWC source/destination instance is absent')
        source_cell, source_pins = top[source_name]
        destination_cell, destination_pins = pe[destination_name]
        _need(first['module'] == source_cell and first['scope'].rstrip() ==
              'tb_soc.dut.' + ('\\' if source_text.startswith('\\') else '') + source_name,
              'SWC source module or full simulation scope differs')
        _need(declarations[source_cell].get(source_pin) == 'output' and
              declarations[destination_cell].get(destination_pin) == 'input',
              'SWC endpoints are not output-to-input library pins')
        source_wire = source_pins.get(source_pin)
        bit = re.fullmatch(r'mem_axi_araddr\[(\d+)\]', source_wire or '')
        alias = destination_pins.get(destination_pin)
        _need(bit is not None and alias in aliases and int(bit[1]) == aliases[alias],
              'SWC endpoint path does not follow the same pure address-bit alias')
        number = int(first['line'])
        _need(number not in wanted, 'repeated SWC SDF line')
        endpoint = (source_name, source_pin, destination_name, destination_pin)
        _need(endpoint not in seen, 'repeated SWC endpoint pair')
        seen.add(endpoint)
        wanted.add(number)
        notices.append({'sdf_line': number, 'source_instance': source_name, 'source_pin': source_pin,
                        'source_cell': source_cell, 'destination_instance': 'u_pe/' + destination_name,
                        'destination_pin': destination_pin, 'destination_cell': destination_cell,
                        'top_wire': source_wire, 'accelerator_port': 's_axi_araddr',
                        'accelerator_alias': alias, 'address_bit': int(bit[1])})
    sdf_lines, celltype, instance = {}, None, None
    with sdfpath.open() as stream:
        for number, line in enumerate(stream, 1):
            cell = re.fullmatch(r'\s*\(CELLTYPE "([^"]+)"\)\s*', line)
            inst = re.fullmatch(r'\s*\(INSTANCE\s*([^()]*)\)\s*', line)
            if cell:
                celltype = cell[1]
            if inst:
                instance = inst[1].strip()
            if number in wanted:
                _need(celltype == 'soc_top' and instance == '', 'SWC line is outside the top SDF CELL')
                sdf_lines[number] = line.strip()
            if number >= max(wanted):
                break
    _need(set(sdf_lines) == wanted, 'missing SWC SDF source lines')
    bits, representative = Counter(), None
    for notice in notices:
        line = sdf_lines[notice['sdf_line']]
        match = re.fullmatch(r'\(INTERCONNECT (\S+) (\S+) (\([^()]+\)) (\([^()]+\))\)', line)
        _need(match is not None, 'SWC SDF entry is not a two-transition INTERCONNECT')
        source_path = re.sub(r'\\(.)', r'\1', match[1])
        _need(source_path == notice['source_instance'] + '/' + notice['source_pin'] and
              match[2] == notice['destination_instance'] + '/' + notice['destination_pin'],
              'SWC warning endpoints differ from the exact SDF INTERCONNECT')
        values = []
        for group in (match[3], match[4]):
            triple = [Decimal(value) for value in group[1:-1].split(':')]
            _need(len(triple) == 3 and all(value.is_finite() and value > 0 for value in triple),
                  'SWC delay triplets must remain finite and positive')
            values.append(triple)
        notice.update(rise_triplet_ns=[str(v) for v in values[0]],
                      fall_triplet_ns=[str(v) for v in values[1]], sdf_entry=line)
        bits[notice['address_bit']] += 1
        if notice['source_instance'] == 'U113150' and notice['destination_instance'] == 'u_pe/U27776':
            _need(notice['source_cell'] == 'BUF_X4' and notice['source_pin'] == 'Z' and
                  notice['destination_cell'] == 'MUX2_X1' and notice['destination_pin'] == 'S' and
                  notice['address_bit'] == 3, 'representative SWC path differs')
            representative = {**notice, 'rise_ns': str(values[0][2]), 'fall_ns': str(values[1][2]),
                              'source_hierarchy': 'tb_soc.dut.U113150.Z',
                              'destination_hierarchy': 'tb_soc.dut.u_pe.U27776.S'}
    _need(dict(bits) == {0: 2, 1: 2, 2: 2, 3: 78, 4: 10, 5: 9, 6: 12, 7: 8, 8: 6},
          'SWC address-bit distribution differs')
    _need(representative is not None, 'observed SWC representative is absent')
    return {'warning_count': 129, 'bit_counts': dict(sorted(bits.items())),
            'source_sdf_sha256': _sha(sdfpath), 'netlist_sha256': _sha(netpath),
            'all_warned_paths_are_pure_cross_boundary_address_aliases': True,
            'all_warning_messages_state_delay_still_annotated': True,
            'representative_delay': representative, 'locations': notices}


def validate_diagnostics(case_dir, case_name):
    """Require every compile warning to match the frozen reviewed source set."""
    case = Path(case_dir).resolve()
    _need(case_name in {'rtl', 'gate'}, 'unknown simulation case')
    folder = case / case_name
    compilation = (folder / 'compile.log').read_text(errors='replace')
    simulation = (folder / 'run.log').read_text(errors='replace')
    error_pattern = r'(?im)^\s*(?:Error(?:-\[|:)|Fatal(?:-\[|:)|FATAL:|\$fatal)'
    _need(re.search(error_pattern, compilation + '\n' + simulation) is None, 'compiler/runtime error diagnostic')
    _need(re.search(r'(?im)^\s*(?:Warning|Lint)(?:-|:)', simulation) is None,
          'normal runtime contains a warning or lint diagnostic')
    _need(re.search(r'(?i)timing[_ ]violation', simulation) is None, 'normal runtime contains a timing violation')
    _need(not re.search(r'SOC_(?:SMOKE|PROGRAM_MONITOR|SDF_OBSERVER)_FAIL|TIMEOUT after', simulation),
          'normal runtime contains a design/monitor failure')
    codes = Counter(re.findall(r'(?m)^\s*(?:Warning|Lint)-\[([^]]+)\]', compilation))
    expected = Counter({'LINX_KRNL': 1, 'TFIPC': 1, 'SIOB': 17} if case_name == 'rtl'
                       else {'LINX_KRNL': 1, 'TFIPC': 12562, 'SDFCOM_SWC': 129})
    _need(codes == expected, 'unreviewed or missing diagnostic codes: ' + str(dict(codes)))
    diagnostic_starts = re.findall(r'(?m)^\s*(?:Warning|Lint)(?:-|:)[^\n]*', compilation)
    _need(len(diagnostic_starts) == sum(codes.values()), 'unnumbered/unparsed compiler warning')
    kernel = _blocks(compilation, 'LINX_KRNL')
    _need(len(kernel) == 1 and re.fullmatch(
        r"Warning-\[LINX_KRNL\] Unsupported Linux kernel\n  Linux kernel '[^'\n]+' is not supported\.\n  Supported versions are 2\.4\* or 2\.6\*\.", kernel[0]) is not None,
        'Linux support-list warning wording differs')
    result = {'status': 'PASS', 'diagnostic_codes': dict(codes),
              'normal_runtime_warning_count': 0, 'normal_timing_violations': 0,
              'kernel_warning': 'Old VCS Linux support list; compiler and simulation completed successfully.',
              'reviewed_annotation_warning_count': 0,
              'original_logs_preserved': True}
    if case_name == 'rtl':
        result['tfipc_review'] = _rtl_tfipc(case, compilation)
        result['siob_review'] = _rtl_siob(case, compilation)
    else:
        tfipc = _gate_tfipc(case, compilation)
        artifact = folder / 'tfipc_review.json'
        artifact.write_text(json.dumps(tfipc, ensure_ascii=False, indent=2) + '\n')
        result['tfipc_review'] = {key: value for key, value in tfipc.items() if key != 'locations'}
        result['tfipc_review'].update(artifact=str(artifact), artifact_sha256=_sha(artifact))
        annotation = (folder / 'sdf_annotation.log').read_text(errors='replace')
        annotation_codes = Counter(re.findall(r'(?m)^\s*(?:Warning|Lint)-\[([^]]+)\]', annotation))
        _need(annotation_codes == Counter({'SDFCOM_SWC': 129}), 'unreviewed annotation diagnostic codes')
        _need(re.search(error_pattern, annotation) is None and
              re.findall(r'Total errors:\s*(\d+)', annotation) == ['0'] and
              re.findall(r'Total warnings:\s*(\d+)', annotation) == ['129'],
              'annotation statistics contain errors or unaccounted warnings')
        swc = _swc_review(case, compilation, annotation)
        artifact = folder / 'swc_review.json'
        artifact.write_text(json.dumps(swc, ensure_ascii=False, indent=2) + '\n')
        result['swc_review'] = {key: value for key, value in swc.items() if key != 'locations'}
        result['swc_review'].update(artifact=str(artifact), artifact_sha256=_sha(artifact))
        result['reviewed_annotation_warning_count'] = 129
    return result
