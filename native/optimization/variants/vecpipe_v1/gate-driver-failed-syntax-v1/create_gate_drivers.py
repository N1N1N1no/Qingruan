"""Create/verify isolated gate drivers; preserve an identical partial creation."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def once(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)


def rebase(text):
    return once(text, 'ROOT = Path(__file__).resolve().parent\nBASE = ROOT.parents[1]',
                'HERE = Path(__file__).resolve().parent\nROOT = HERE.parents[1]\nBASE = ROOT.parents[1]')


pairs = []


def write(source, target, text):
    ast.parse(text)
    if target.exists():
        assert target.read_text() == text, 'Preserve differing partial driver: ' + str(target)
    else:
        target.write_text(text)
    pairs.append((source, target))


source = ROOT / 'prepare_full_soc_gate.py'
text = rebase(source.read_text())
text = once(text, "source = ROOT / 'work/full-soc-timing' / args.geometry / args.mode / args.timing_revision\ngold = ROOT / ('work/complete-soc-' + args.geometry) / args.mode\ncase = ROOT / 'work/full-soc-gate' / args.geometry / args.mode / args.revision",
            "assert args.geometry == 'R8_C16_L4' and args.mode == 'shared'\nsource = ROOT / 'work/vecpipe-full-soc-loads-v1'\ngold = ROOT / 'work/vecpipe-soc-v1'\ncase = ROOT / 'work/vecpipe-full-soc-gate' / args.revision")
text = once(text, "assert proof['status'] == 'FULL_ALL8_SOC_MAPPED_TIMING_PASS'",
            "assert proof['status'] == 'VECPIPE_FULL_ALL8_SOC_SDF_LOAD_REPAIR_PASS'\nassert proof['selected_MAXIMUM_IOPATH_nonnegative'] and proof['normalized_SDC_identical_to_source']")
text = once(text, "assert gold_proof['status'] == 'PASS'",
            "assert gold_proof['status'] == 'VECPIPE_REAL_RV32_ALL8_SHARED_BACKEND_FROZEN_FIRMWARE_AND_CPU_NEGATIVE_PASS'")
text = once(text, "for name, wanted in gold_proof['fixture_sha256'].items():\n    assert sha(gold / name) == wanted, name\n", '')
text = once(text, 'hashes = {str(path): sha(path) for path in originals}',
            "generation_path = HERE / 'gate-drivers-generation.json'\ngeneration = json.loads(generation_path.read_text())\nfor path, expected in generation['source_sha256'].items():\n    assert sha(local(path)) == expected, path\noriginals += [generation_path, *map(local, generation['source_sha256'])]\nhashes = {str(path): sha(path) for path in originals}")
write(source, HERE / 'prepare_gate.py', text)
for old, new in [('run_full_soc_gate_extended.py', 'run_gate.py'), ('run_full_soc_gate_corrupt_parallel.py', 'run_gate_corrupt.py')]:
    source = ROOT / old
    text = rebase(source.read_text())
    text = once(text, "case = ROOT / 'work/full-soc-gate/R8_C16_L4/shared' / args.revision",
                "case = ROOT / 'work/vecpipe-full-soc-gate' / args.revision")
    text = text.replace('28421011', '28414932')
    write(source, HERE / new, text)
prefix = 'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[2]))\n'
for old, new in [('audit_full_soc_annotation.py', 'audit_gate_annotation.py'),
                 ('run_full_soc_timing_probe.py', 'run_gate_timing_probe.py'),
                 ('review_full_soc_gate.py', 'review_gate.py')]:
    source = ROOT / old
    text = once(source.read_text(), "case = ROOT / 'work/full-soc-gate/R8_C16_L4/shared' / args.revision",
                "case = ROOT / 'work/vecpipe-full-soc-gate' / args.revision")
    write(source, HERE / new, prefix + text)
source = ROOT / 'audit_gate_corruption.py'
text = once(source.read_text(), "case = ROOT / 'work/full-soc-gate/R8_C16_L4/shared/v5'",
            "case = ROOT / 'work/vecpipe-full-soc-gate/v1'")
write(source, HERE / 'audit_gate_corruption.py', prefix + text)
paths = [path for pair in pairs for path in pair]
paths += [Path(__file__), HERE / 'audit_gate_connections.py', HERE / 'run_gate_workflow.py']
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
record = {'status': 'ISOLATED_PIPELINE_GATE_DRIVERS_GENERATED_NOT_EXECUTED',
          'source_sha256': {str(path): sha(path) for path in paths},
          'adaptations': 'Original qualified gate drivers isolated to current pipeline source/gold case, correct workspace imports and complete source guards. Same physical alias/warning/count checks, actual MIPD-compensated timing probe, frozen simulator and86400s runtime gates. Corruption reviewer retains strict Fatal exception/negative controls.'}
target = HERE / 'gate-drivers-generation.json'
encoded = json.dumps(record, indent=2) + '\n'
assert not target.exists(), 'Preserve existing generator manifest'
target.write_text(encoded)
print('ISOLATED_PIPELINE_GATE_DRIVERS_GENERATED_PARSE_PASS', flush=True)
