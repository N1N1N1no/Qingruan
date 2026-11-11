#!/usr/bin/env python3
"""Reuse a passing generic netlist only when its design inputs still match."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
DESIGN = PROJECT.parent
TASK = DESIGN.parent
OUTPUT = PROJECT / 'build/dc_read_design'
OUTPUT.mkdir(parents=True, exist_ok=True)

subprocess.run([
    'python3', str(PROJECT / 'scripts/prepare.py'),
    '--rv-root', str(DESIGN / 'vendor/Cores-VeeR-EL2'), '--skip-config',
], cwd=PROJECT, check=True)

# Match the exact dependency collection used by the public generic check,
# including include-directory contents that could shadow an older header.
current_inputs = {
    PROJECT / 'synth/sram_blackbox.v', PROJECT / 'tb/sram_reference_model.v',
    PROJECT / 'scripts/check_frontend_synthesis.py',
    PROJECT / 'scripts/fold_verilog_literal_slices.py', PROJECT / 'Makefile.frontend',
}
for line in (PROJECT / 'build/synth.f').read_text().splitlines():
    if line.startswith('+incdir+'):
        current_inputs.update(p for p in Path(line[len('+incdir+'):]).rglob('*')
                              if p.is_file() and p.suffix in ('.v', '.sv', '.vh', '.svh'))
    elif line and not line.startswith(('+', '//')):
        current_inputs.add(Path(line))
current_input_names = {str(path.resolve()) for path in current_inputs}

def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

selected = None
for run in sorted((TASK / 'work').glob('generic.*'),
                  key=lambda p: p.stat().st_mtime, reverse=True):
    summary_file = run / 'summary.json'
    if not summary_file.is_file():
        continue
    summary = json.loads(summary_file.read_text())
    if (summary.get('status') != 'PASS' or summary.get('scope') != 'generic'
            or Path(summary.get('source', '/')).resolve() != DESIGN):
        continue
    working = run / 'source'
    reports = list((working / 'project/build/frontend_synth').glob('*/report.json'))
    if len(reports) != 1:
        continue
    folder = reports[0].parent
    report = json.loads(reports[0].read_text())
    netlist = folder / 'soc_generic.v'
    if (report.get('pass') is not True or report.get('sram_blackbox_count') != 256
            or not netlist.is_file()
            or sha(netlist) != report.get('generic_netlist_sha256')):
        continue

    # Check source order, macros, and recorded inputs. New synthesis scripts
    # do not invalidate a netlist; RTL and configuration changes do.
    prior_list = (working / 'project/build/synth.f').read_text()
    if prior_list.replace(str(working), str(DESIGN)) != (PROJECT / 'build/synth.f').read_text():
        continue
    original_hashes = json.loads((folder / 'source_hashes.json').read_text())
    current_hashes = {}
    matches = True
    for original_path, expected in original_hashes.items():
        try:
            current_path = DESIGN / Path(original_path).relative_to(working)
        except ValueError:
            matches = False
            break
        if not current_path.is_file() or sha(current_path) != expected:
            matches = False
            break
        current_hashes[str(current_path.resolve())] = expected
    if matches and set(current_hashes) == current_input_names:
        selected = (run, folder, netlist, current_hashes, report['generic_netlist_sha256'])
        break

if selected is None:
    raise SystemExit(
        'No passing generic netlist matches the current RTL/configuration. Run:\n'
        f'bash {TASK}/run_rtl.sh --source {DESIGN} --stage generic'
    )

run, folder, netlist, current_hashes, expected_netlist_sha = selected
target = OUTPUT / 'soc_dc.v'
shutil.copyfile(netlist, target)
if sha(target) != expected_netlist_sha:
    raise SystemExit('Copied generic netlist does not match its source')
if any(sha(Path(path)) != expected for path, expected in current_hashes.items()):
    raise SystemExit('Source changed while preparing the DC input')
(OUTPUT / 'conversion.json').write_text(json.dumps({
    'scope': 'Yosys generic synthesis output; DC technology mapping has not run',
    'source_run': str(run),
    'source_report': str(folder / 'report.json'),
    'input_sha256': current_hashes,
    'verilog_sha256': sha(target),
    'sram_definition': 'resolved from sram.db, not an empty HDL stub',
    'formal_equivalence_checked': False,
}, indent=2) + '\n')
print('DC_RTL_PREPARED ' + str(target))
