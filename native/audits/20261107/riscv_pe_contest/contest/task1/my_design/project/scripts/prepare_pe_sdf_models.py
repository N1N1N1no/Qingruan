#!/usr/bin/env python3
"""Prepare auditable VCS timing-model copies without editing source libraries.

Compile the resulting standard model with TETRAMAX, NTC, and RECREM defined.
All specify blocks, timing checks, functional UDPs, and notifiers are retained.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re


MODULE = re.compile(r"(?ms)^module\s+(\w+)\b.*?^endmodule\b")
XBUF = re.compile(r"ng_xbuf\(\s*(\w+)\s*,\s*(\w+)\s*,\s*1'b1\s*\);")
TETRAMAX = re.compile(
    r"(?ms)^(?P<indent>[ \t]*)`ifdef TETRAMAX[ \t]*\n"
    r"(?P<enabled>.*?)^(?P=indent)`else[ \t]*\n"
    r"(?P<disabled>.*?)^(?P=indent)`endif\b"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inputs(module: str) -> set[str]:
    # These supplied standard-cell models declare one scalar input per line.
    declarations = re.findall(r"(?m)^\s*input\s+([^;]+);", module)
    if any(not re.fullmatch(r"\w+", name) for name in declarations):
        raise ValueError("Unsupported standard-model input declaration")
    return set(declarations)


def _forbidden(inputs: set[str]) -> set[str]:
    # Delayed pin signals are driven by $setuphold/$recrem, not aliases.
    return inputs | {name + '_d' for name in inputs} | {name + '_di' for name in inputs}


def _preprocess(source: str, defines: set[str]) -> str:
    """Select the library's simple conditional branches for static validation."""
    active = True
    stack = []
    selected = []
    for line in source.splitlines(keepends=True):
        directive = re.fullmatch(
            r"\s*`(ifdef|ifndef|else|endif)(?:\s+(\w+))?\s*", line
        )
        if not directive:
            if active:
                selected.append(line)
            continue
        command, name = directive.groups()
        if command in {'ifdef', 'ifndef'}:
            if name not in {'NTC', 'RECREM', 'TETRAMAX'}:
                raise ValueError('Unexpected conditional macro: ' + str(name))
            condition = name in defines
            if command == 'ifndef':
                condition = not condition
            stack.append((active, condition, False))
            active = active and condition
        elif command == 'else':
            if not stack or stack[-1][2]:
                raise ValueError('Invalid conditional else')
            parent, condition, _ = stack[-1]
            stack[-1] = (parent, condition, True)
            active = parent and not condition
        else:
            if not stack:
                raise ValueError('Unmatched conditional endif')
            active = stack.pop()[0]
    if stack:
        raise ValueError('Unterminated conditional directive')
    return ''.join(selected)


def _standard_model(source: str) -> tuple[str, dict]:
    changes = []

    def transform_module(match):
        original = match.group(0)
        name = match.group(1)
        forbidden = _forbidden(_inputs(original))

        def transform_branch(branch):
            disabled = branch['disabled']
            aliases = XBUF.findall(disabled)
            if not aliases:
                raise ValueError('Unexpected empty TETRAMAX branch in ' + name)
            if branch['enabled'].strip() or XBUF.sub('', disabled).strip():
                raise ValueError('Unexpected TETRAMAX branch contents in ' + name)
            buffers = [(target, value) for target, value in aliases if target not in forbidden]
            skipped = [(target, value) for target, value in aliases if target in forbidden]
            indent = branch['indent']
            added = ''.join(indent + '  buf(' + target + ', ' + value + ');\n'
                            for target, value in buffers)
            changes.append({'module': name,
                            'restored_internal_buffers': [list(pair) for pair in buffers],
                            'removed_input_feedback': [list(pair) for pair in skipped]})
            return (indent + '`ifdef TETRAMAX\n' + added + indent + '`else\n' +
                    disabled + indent + '`endif')

        return TETRAMAX.sub(transform_branch, original)

    output = MODULE.sub(transform_module, source)
    if len(changes) != 28 or sum(len(XBUF.findall(m[0])) for m in MODULE.finditer(source)) != 128:
        raise ValueError('Source library no longer has the reviewed 28 branches / 128 xbuf instances')
    # Byte-for-byte preservation covers every path, check, notifier, and UDP.
    for pattern in [r'(?ms)^\s*specify\b.*?^\s*endspecify\b',
                    r'(?ms)^\s*primitive\b.*?^\s*endprimitive\b']:
        if re.findall(pattern, source) != re.findall(pattern, output):
            raise ValueError('A timing block or UDP was modified')
    if re.findall(r'\b(?:NOTIFIER|notifier)\b', source) != re.findall(r'\b(?:NOTIFIER|notifier)\b', output):
        raise ValueError('Notifier references changed')

    configurations = []
    for ntc, recrem in itertools.product([False, True], repeat=2):
        flags = {name for name, enabled in [('NTC', ntc), ('RECREM', recrem)] if enabled}
        native = _preprocess(source, flags)
        # The default branch is preserved exactly, including original xbuf UDP.
        if native != _preprocess(output, flags):
            raise ValueError('Non-TETRAMAX branch changed')
        active = _preprocess(output, flags | {'TETRAMAX'})
        if re.search(r'\bng_xbuf\b', active):
            raise ValueError('An auxiliary xbuf remains active in TETRAMAX mode')
        models = {m.group(1): m.group(0) for m in MODULE.finditer(active)}
        aliases_checked = 0
        for original_module in MODULE.finditer(native):
            original = original_module.group(0)
            name = original_module.group(1)
            forbidden = _forbidden(_inputs(original))
            for target, value in XBUF.findall(original):
                expression = re.compile(r'\bbuf\(\s*' + re.escape(target) + r'\s*,\s*' +
                                        re.escape(value) + r'\s*\);')
                count = len(expression.findall(models[name]))
                if count != (0 if target in forbidden else 1):
                    raise ValueError('Missing alias or restored feedback: ' + name + '.' + target)
                aliases_checked += target not in forbidden
        for name in ['DFFR_X1', 'SDFFR_X1']:
            model = models[name]
            if '$setuphold(' not in model:
                raise ValueError('Setup/hold checks disappeared in ' + name)
            if ntc and recrem:
                if '$recrem(' not in model or ',CK_d,' not in model:
                    raise ValueError('Negative timing-check signal support missing in ' + name)
            elif '$recovery(' not in model or '$hold(' not in model:
                raise ValueError('Reset timing checks disappeared in ' + name)
        configurations.append({'NTC': ntc, 'RECREM': recrem, 'TETRAMAX': True,
                               'internal_buffers_checked': aliases_checked})
    return output, {'modified_preprocessor_branches': len(changes),
                    'restored_internal_buffers': sum(len(x['restored_internal_buffers']) for x in changes),
                    'removed_input_feedback': sum(len(x['removed_input_feedback']) for x in changes),
                    'branches': changes, 'static_configurations': configurations,
                    'specify_blocks_unchanged': True, 'functional_udps_unchanged': True,
                    'notifier_references_unchanged': True}


def _sram_model(source: str) -> tuple[str, dict]:
    output = source
    condition = "ce_in == 1'b0 && we_in == 1'b1"
    for bit in range(8):
        old = f' (posedge clk => (rd_out[{bit}] +: rd_value[{bit}])) = (0,0);'
        if output.count(old) != 1:
            raise ValueError('Expected one unconditional SRAM path per output bit')
        output = output.replace(old, ' if (' + condition + ')' + old)
    # SDF HOLD(data, clock, value) maps to Verilog $hold(clock, data, value).
    pins = ['ce_in', 'we_in'] + [f'addr_in[{i}]' for i in range(4)] + [f'wd_in[{i}]' for i in range(8)]
    for pin in pins:
        if source.count(f'$setup({pin},posedge clk,0,notifier);') != 1:
            raise ValueError('Unexpected SRAM setup check: ' + pin)
        if source.count(f'$hold(posedge clk,{pin},0,notifier);') != 1:
            raise ValueError('Unexpected SRAM hold check: ' + pin)
    if re.findall(r'\$\w+\([^;]*;', source) != re.findall(r'\$\w+\([^;]*;', output):
        raise ValueError('SRAM timing checks or diagnostics changed')
    return output, {'conditional_output_paths': 8, 'condition': condition,
                    'timing_checks_unchanged': True, 'hold_checks_verified': len(pins),
                    'hold_event_order': 'SDF HOLD(data, clock); Verilog $hold(clock, data)'}


def prepare_models(standard_source: Path, sram_source: Path, output_dir: Path) -> dict:
    """Write compatible timing model copies and return their transformation record."""
    standard_source, sram_source, output_dir = map(Path, (standard_source, sram_source, output_dir))
    inputs = {name: (path, path.read_bytes()) for name, path in
              [('standard', standard_source), ('sram', sram_source)]}
    standard, standard_record = _standard_model(inputs['standard'][1].decode())
    sram, sram_record = _sram_model(inputs['sram'][1].decode())
    output_dir.mkdir(parents=True, exist_ok=True)
    record = {
        'required_defines': ['TETRAMAX', 'NTC', 'RECREM'],
        'required_vcs_options': ['+neg_tchk'],
        'notes': [
            'TETRAMAX removes auxiliary feedback to input pins and delayed pin signals.',
            'Unidirectional buffers preserve the internal conditions used by timing checks.',
            'Buffers preserve binary condition values and propagate unknowns conservatively.',
            'NTC and RECREM select delayed pins for negative setup/hold and recovery/removal.',
            'No timing check, path, functional UDP, or notifier is disabled.',
            'SRAM clock-to-output conditions match the DC-compatible Liberty / SDF.',
        ],
    }
    for name, text, detail in [('standard', standard, standard_record), ('sram', sram, sram_record)]:
        source_path, source_bytes = inputs[name]
        target = output_dir / (name + '_model.v')
        if target.resolve() in {standard_source.resolve(), sram_source.resolve()}:
            raise ValueError('Refusing to overwrite a source library')
        target.write_text(text)
        if source_path.read_bytes() != source_bytes:
            raise RuntimeError('Source library changed during preparation: ' + str(source_path))
        record[name] = {'source': str(source_path.resolve()), 'source_sha256': _sha(source_bytes),
                        'model': str(target.resolve()), 'model_sha256': _sha(target.read_bytes()), **detail}
    manifest = output_dir / 'model_compatibility.json'
    record['manifest'] = str(manifest.resolve())
    manifest.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('standard_source', type=Path)
    parser.add_argument('sram_source', type=Path)
    parser.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_models(args.standard_source, args.sram_source, args.output_dir),
                     indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
