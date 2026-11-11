"""Prove warned SDF connections through only bit wiring and module ports.

No diagnostic is waived by name alone. Every notice is tied to its exact SDF
line, to declared physical pin directions, and to a common wire equivalence
class built from the actual mapped Verilog. Logic gates are never collapsed.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
SCRIPTS = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project/scripts'
sys.path.insert(0, str(SCRIPTS))
from prepare_soc_sdf import _sdf_path
from validate_soc_sdf_warnings import _blocks, _declarations

ID = r'(?:\\[^\s]+|[A-Za-z_$][\w$]*)'
INST = re.compile(r'(?m)^[ \t]*(\w+)[ \t]+(' + ID + r')[ \t]*\(([^;]*?)\)[ \t]*;')
DECL = re.compile(r'(?m)^[ \t]*(input|output|inout|wire)\s+(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([^;]+);')
ASSIGN = re.compile(r'(?m)^[ \t]*assign[ \t]+([^;=]+)=([^;]+);')
TOKEN = re.compile(r"\s*(\\[^\s]+|\d+'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+|[A-Za-z_$][\w$]*|\d+|[{}\[\]:,])")


def need(ok, reason):
    if not ok:
        raise RuntimeError('Gate connectivity audit: ' + reason)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def name(token):
    return token[1:] if token.startswith('\\') else token


def pins(text):
    matches = re.findall(r'\.(\w+)[ \t\r\n]*\(\s*([^()]*)\)', text)
    result = dict(matches)
    need(len(matches) == len(result), 'duplicate named port')
    remainder = re.sub(r'\.(\w+)[ \t\r\n]*\(\s*([^()]*)\)', '', text)
    need(not remainder.replace(',', '').strip(), 'unsupported port connection syntax')
    return result


class Wires:
    def __init__(self, path):
        raw = path.read_text()
        raw = re.sub(r'/\*.*?\*/|//[^\n]*', '', raw, flags=re.S)
        blocks = re.findall(r'(?ms)^[ \t]*module[ \t]+(\w+)[ \t]*\(.*?;(.*?)^[ \t]*endmodule\b', raw)
        self.modules = {}
        self.parent, self.rank = {}, {}
        for module, body in blocks:
            need(module not in self.modules, 'duplicate module ' + module)
            widths, ports = {}, {}
            for direction, hi, lo, declaration in DECL.findall(body):
                indices = list(range(int(hi), int(lo) + (-1 if int(hi) >= int(lo) else 1),
                                     -1 if int(hi) >= int(lo) else 1)) if hi else [None]
                for token in declaration.split(','):
                    token = token.strip()
                    need(re.fullmatch(ID, token) is not None, 'unsupported declaration ' + token)
                    signal = name(token)
                    need(signal not in widths or widths[signal] == indices, 'conflicting wire width')
                    widths[signal] = indices
                    if direction != 'wire':
                        ports[signal] = direction
            self.modules[module] = {'body': body, 'widths': widths, 'ports': ports, 'hier': {}, 'leaves': {}}
        need('soc_accel_top' in self.modules, 'missing top')
        for module in self.modules.values():
            for ref, instance, connections in INST.findall(module['body']):
                if ref in self.modules:
                    module['hier'][name(instance)] = (ref, pins(connections))
        self.scopes = {(): ('soc_accel_top', 0)}
        self.alias_assignments = self.port_bits = 0
        self._build(())

    def root(self, node):
        self.parent.setdefault(node, node)
        parent = self.parent[node]
        if parent != node:
            self.parent[node] = self.root(parent)
        return self.parent[node]

    def join(self, a, b):
        a, b = self.root(a), self.root(b)
        if a == b:
            return
        if self.rank.get(a, 0) < self.rank.get(b, 0):
            a, b = b, a
        self.parent[b] = a
        if self.rank.get(a, 0) == self.rank.get(b, 0):
            self.rank[a] = self.rank.get(a, 0) + 1

    def bits(self, scope, expression):
        module_name, ordinal = self.scopes[scope]
        widths = self.modules[module_name]['widths']
        expression = expression.strip()
        tokens, at = [], 0
        while at < len(expression):
            match = TOKEN.match(expression, at)
            need(match is not None, 'non-wiring expression ' + expression)
            tokens.append(match[1])
            at = match.end()
        pos = 0

        def take(value):
            nonlocal pos
            need(pos < len(tokens) and tokens[pos] == value, 'malformed expression ' + expression)
            pos += 1

        def part():
            nonlocal pos
            need(pos < len(tokens), 'truncated expression')
            token = tokens[pos]
            pos += 1
            if token == '{':
                if pos + 1 < len(tokens) and tokens[pos].isdigit() and tokens[pos + 1] == '{':
                    repeat = int(tokens[pos])
                    pos += 1
                    repeated = part()
                    take('}')
                    need(0 < repeat < 100000, 'unreasonable wire replication')
                    return repeated * repeat
                result = part()
                while pos < len(tokens) and tokens[pos] == ',':
                    pos += 1
                    result += part()
                take('}')
                return result
            literal = re.fullmatch(r"(\d+)'[sS]?([bBoOdDhH])([0-9a-fA-FxXzZ_]+)", token)
            if literal:
                width, base, digits = literal.groups()
                need(not re.search('[xXzZ]', digits), 'unknown constant in wiring')
                width = int(width)
                value = int(digits.replace('_', ''), {'b': 2, 'o': 8, 'd': 10, 'h': 16}[base.lower()])
                return [(-1, 'constant', (value >> i) & 1) for i in range(width - 1, -1, -1)]
            signal = name(token)
            need(signal in widths, 'undeclared wire ' + module_name + '.' + signal)
            indices = widths[signal]
            if pos < len(tokens) and tokens[pos] == '[':
                pos += 1
                need(pos < len(tokens) and tokens[pos].isdigit(), 'dynamic wire select')
                hi = lo = int(tokens[pos])
                pos += 1
                if pos < len(tokens) and tokens[pos] == ':':
                    pos += 1
                    need(pos < len(tokens) and tokens[pos].isdigit(), 'dynamic wire range')
                    lo = int(tokens[pos])
                    pos += 1
                take(']')
                indices = list(range(hi, lo + (-1 if hi >= lo else 1), -1 if hi >= lo else 1))
                need(set(indices) <= set(widths[signal]), 'out-of-bounds wire select')
            return [(ordinal, signal, bit) for bit in indices]

        result = part()
        need(pos == len(tokens), 'unsupported expression tail ' + expression)
        return result

    def _build(self, scope):
        module_name, ordinal = self.scopes[scope]
        module = self.modules[module_name]
        for lhs, rhs in ASSIGN.findall(module['body']):
            a, b = self.bits(scope, lhs), self.bits(scope, rhs)
            need(len(a) == len(b), 'assignment width differs in ' + module_name)
            for x, y in zip(a, b):
                self.join(x, y)
            self.alias_assignments += 1
        for instance, (ref, connections) in module['hier'].items():
            child = scope + (instance,)
            need(child not in self.scopes, 'duplicate hierarchy')
            self.scopes[child] = (ref, len(self.scopes))
            child_module = self.modules[ref]
            need(set(connections) <= set(child_module['ports']), 'unknown hierarchical port')
            for pin, expr in connections.items():
                if not expr.strip():
                    need(child_module['ports'][pin] == 'output', 'unconnected hierarchical input')
                    continue
                a, b = self.bits(scope, expr), self.bits(child, pin)
                need(len(a) == len(b), 'hierarchical port width differs: ' + ref + '.' + pin)
                for x, y in zip(a, b):
                    self.join(x, y)
                self.port_bits += len(a)
            self._build(child)

    def prepare_endpoints(self, endpoints):
        wanted = defaultdict(set)
        for path in endpoints:
            scope = tuple(path[:-2])
            need(scope in self.scopes, 'missing endpoint parent scope ' + repr(path))
            wanted[self.scopes[scope][0]].add(path[-2])
        for ref, names in wanted.items():
            module = self.modules[ref]
            for cell, instance, connections in INST.findall(module['body']):
                instance = name(instance)
                if instance in names:
                    need(cell not in self.modules, 'warned endpoint is not physical')
                    module['leaves'][instance] = (cell, pins(connections))
            need(names <= set(module['leaves']), 'missing physical leaf in ' + ref)

    def endpoint(self, path, direction, declarations):
        scope = tuple(path[:-2])
        module = self.modules[self.scopes[scope][0]]
        cell, connections = module['leaves'][path[-2]]
        pin = path[-1]
        need(declarations.get(cell, {}).get(pin) == direction, 'wrong physical pin direction ' + repr(path))
        need(pin in connections and connections[pin].strip(), 'disconnected physical endpoint')
        bits = self.bits(scope, connections[pin])
        need(len(bits) == 1, 'warned endpoint is not scalar')
        return self.root(bits[0]), cell, connections[pin]


def read_notices(case):
    compilation = (case / 'compile.log').read_text(errors='replace')
    annotation = (case / 'sdf_annotation.log').read_text(errors='replace')
    notices, by_line = [], {}
    for code in ['SDFCOM_SWC', 'SDFCOM_IWSBA']:
        blocks = _blocks(annotation, code)
        need(blocks and Counter(blocks) == Counter(_blocks(compilation, code)), 'compile/annotation notice differences')
        for block in blocks:
            line = re.search(r'(?m)^soc\.sdf, (\d+)$', block)
            need(line is not None and 'delay will still be annotated.' in ' '.join(block.split()),
                 'unrecognized wire notice')
            number = int(line[1])
            need(number not in by_line, 'duplicate warned SDF line')
            notice = {'code': code, 'sdf_line': number, 'warning': block}
            notices.append(notice)
            by_line[number] = notice
    celltype = instance = None
    last_wanted = max(by_line)
    with (case / 'soc.sdf').open() as stream:
        for number, line in enumerate(stream, 1):
            if '(CELLTYPE ' in line:
                celltype = re.search(r'\(CELLTYPE "([^\"]+)"\)', line)[1]
            if '(INSTANCE' in line:
                instance = re.search(r'\(INSTANCE\s*(.*?)\)', line)[1]
            if number not in by_line:
                continue
            need(celltype == 'soc_accel_top' and instance == '', 'warned interconnect outside root SDF cell')
            entry = re.fullmatch(r'\s*\(INTERCONNECT (\S+) (\S+) \(([^()]*)\) \(([^()]*)\)\)\s*', line)
            need(entry is not None, 'warned entry is not INTERCONNECT')
            notice = by_line[number]
            src, dst = _sdf_path(entry[1]), _sdf_path(entry[2])
            # VCS escapes identifiers and may wrap them. Check the entire
            # endpoint pair after removing only those display escapes/spaces.
            detail = re.search(r'(?:The\s+path\s+from|INTERCONNECT\s+from)\s+(.*?)\s+to\s+(.*?)(?:doesn.t\s+have|has\s+Instance)', notice['warning'], re.S)
            need(detail is not None, 'unparsed warning endpoints: ' + notice['warning'])
            display = [re.sub(r'\s+|\\', '', value) for value in detail.groups()]
            need(display == ['.'.join(src), '.'.join(dst)], 'warning endpoints differ from exact SDF line')
            rise, fall = [[Decimal(part) for part in group.split(':')] for group in entry.groups()[2:]]
            need(len(rise) == len(fall) == 3 and all(v.is_finite() and v >= 0 for v in rise + fall),
                 'nonfinite/negative interconnect delay')
            notice.update(source_path=src, destination_path=dst, sdf_entry=line.strip(),
                          rise_triplet_ns=list(map(str, rise)), fall_triplet_ns=list(map(str, fall)))
            if number == last_wanted:
                break
    need(all('source_path' in notice for notice in notices), 'missing warned SDF lines')
    return notices


def audit_wires(case):
    notices = read_notices(case)
    print('WIRE_AUDIT_NOTICES=%d' % len(notices), flush=True)
    declarations = {}
    for path in [case / 'models/standard_model.v', case / 'models/sram_model.v']:
        declarations.update(_declarations(path.read_text()))
    wires = Wires(case / 'inputs/soc_mapped.v')
    print('WIRE_AUDIT_HIERARCHY=%d union_nodes=%d' % (len(wires.scopes), len(wires.parent)), flush=True)
    constants = {wires.root((-1, 'constant', bit)) for bit in [0, 1]}
    need(len(constants) == 2, 'mapped bit wiring shorts constant zero and one')
    wires.prepare_endpoints([notice[key] for notice in notices for key in ['source_path', 'destination_path']])
    for notice in notices:
        a, source_cell, source_expr = wires.endpoint(notice['source_path'], 'output', declarations)
        b, destination_cell, destination_expr = wires.endpoint(notice['destination_path'], 'input', declarations)
        need(a == b, 'warned path does not follow pure wiring: ' + repr(notice['source_path']))
        need(a not in constants, 'warned driver connected to constant')
        notice.update(source_cell=source_cell, destination_cell=destination_cell,
                      source_local_expression=source_expr.strip(), destination_local_expression=destination_expr.strip(),
                      pure_bit_wiring_equivalence_proved=True)
        notice.pop('warning')
    reps = {}
    for notice in notices:
        if notice['code'] not in reps and all(Decimal(notice[key][-1]) > Decimal('0.000005')
                                             for key in ['rise_triplet_ns', 'fall_triplet_ns']):
            reps[notice['code']] = notice
    need(set(reps) == {'SDFCOM_SWC', 'SDFCOM_IWSBA'}, 'no observable representative for each warning class')
    return {'status': 'WARNED_INTERCONNECT_SOURCE_CONNECTIVITY_PROVED_FULL_GATE_ACCEPTANCE_OPEN',
            'warning_counts': dict(Counter(notice['code'] for notice in notices)),
            'hierarchical_scopes': len(wires.scopes), 'wire_alias_assignments': wires.alias_assignments,
            'hierarchical_port_bits': wires.port_bits, 'wire_equivalence_nodes': len(wires.parent),
            'representatives': reps, 'locations': notices,
            'source_sha256': {str(path): sha(path) for path in [Path(__file__), case / 'compile.log',
                case / 'sdf_annotation.log', case / 'soc.sdf', case / 'inputs/soc_mapped.v',
                case / 'models/standard_model.v', case / 'models/sram_model.v']},
            'scope': 'Only source proof of all SWC/IWSBA physical endpoints and exact SDF lines. Other diagnostics, complete annotation counts, unused-port review and runtime evidence remain required.'}


def audit_unused_outputs(case):
    compilation = (case / 'compile.log').read_text(errors='replace')
    netpath = case / 'inputs/soc_mapped.v'
    net = netpath.read_text()
    offsets = [0] + [match.end() for match in re.finditer('\n', net)]
    modules = _declarations(net)
    for path in [case / 'models/standard_model.v', case / 'models/sram_model.v']:
        declarations = _declarations(path.read_text())
        need(not (set(modules) & set(declarations)), 'duplicate module declarations')
        modules.update(declarations)
    warnings = _blocks(compilation, 'TFIPC')
    need(bool(warnings), 'expected unused-output diagnostics absent')
    entries, counts, seen = [], Counter(), set()
    for warning in warnings:
        location = re.search(r'(?m)^([^\n]+), (\d+)\n', warning)
        need(location is not None, 'TFIPC source location absent')
        path = location[1]
        if path.startswith('/workspace/'):
            path = str(BASE / path[11:])
        number = int(location[2])
        need(Path(path).resolve() == netpath.resolve() and 1 <= number <= len(offsets),
             'TFIPC location outside actual mapped input')
        need(number not in seen, 'duplicate TFIPC source line')
        seen.add(number)
        start = offsets[number - 1]
        end = net.find(';', start)
        need(end >= start, 'unterminated mapped instance')
        statement = net[start:end + 1]
        match = INST.fullmatch(statement.strip())
        need(match is not None, 'unsupported TFIPC mapped statement')
        ref, instance, connection_text = match.groups()
        need(ref in modules, 'TFIPC names undefined module')
        connections = pins(connection_text)
        need(set(connections) <= set(modules[ref]), 'undefined connected pin')
        missing = set(modules[ref]) - set(connections)
        need(bool(missing), 'TFIPC omits no declared port')
        for pin, direction in modules[ref].items():
            if direction in ['input', 'inout']:
                need(pin in connections and connections[pin].strip(), 'missing or empty input/inout')
        for pin in missing:
            need(modules[ref][pin] == 'output', 'omitted pin is not output')
            counts[ref + '.' + pin] += 1
        entries.append({'source_line': number, 'reference': ref, 'instance': name(instance),
                        'omitted_outputs': sorted(missing)})
    return {'status': 'ALL_TFIPC_MISSING_PORTS_PROVED_UNUSED_OUTPUTS',
            'warning_count': len(warnings), 'no_missing_input_or_inout': True,
            'omitted_output_counts': dict(sorted(counts.items())), 'locations': entries,
            'source_sha256': {str(path): sha(path) for path in [Path(__file__), netpath,
                case / 'compile.log', case / 'models/standard_model.v', case / 'models/sram_model.v']},
            'scope': 'Only missing-port source review. Every omitted connection is a declared output; no input/inout is missing or empty. Functional and full SDF acceptance require separate evidence.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--revision', default='v3')
    parser.add_argument('--output', required=True)
    parser.add_argument('--kind', choices=['wires', 'ports'], default='wires')
    args = parser.parse_args()
    need(re.fullmatch(r'[A-Za-z0-9_-]+', args.revision) is not None, 'invalid revision')
    case = ROOT / 'work/full-soc-gate/R8_C16_L4/shared' / args.revision
    output = Path(args.output)
    need(not output.exists(), 'preserve previous audit')
    result = audit_wires(case) if args.kind == 'wires' else audit_unused_outputs(case)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ['locations', 'source_sha256', 'omitted_output_counts', 'representatives']}), flush=True)
