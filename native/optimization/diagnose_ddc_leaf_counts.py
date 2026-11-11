"""Read-only reconciliation of DC leaf classification with mapped SDF leaves."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
source = ROOT / 'work/full-soc-timing/R8_C16_L4/shared/v1'
gate = ROOT / 'work/full-soc-gate/R8_C16_L4/shared/v3'
case = ROOT / 'work/ddc-leaf-classification-v1'
assert not case.exists()


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


proof = json.loads((source / 'summary.json').read_text())
assert sha(source / 'mapped.ddc') == proof['artifact_sha256']['mapped.ddc']
expected = json.loads((gate / 'preparation.json').read_text())['sdf_adaptation']['physical_leaf_celltypes']
case.mkdir()
tcl = '''if {[catch {
set_app_var target_library [list {STD}]
set_app_var link_library [list * {STD} {SRAM}]
read_ddc {DDC}
current_design soc_accel_top
if {![link]} {error "link failed"}
set leaves [get_cells -hier -filter "is_hierarchical == false"]
array set counts {}
foreach ref [get_attribute $leaves ref_name] {incr counts($ref)}
set stream [open {COUNTS} w]
puts $stream "ref_name\\tcount\\tlibrary_cells\\tmapped_design"
foreach ref [lsort [array names counts]] {
 set lib [get_object_name [get_lib_cells -quiet */$ref]]
 set design [get_object_name [get_designs -quiet $ref]]
 puts $stream "$ref\\t$counts($ref)\\t$lib\\t$design"
}
close $stream
puts "READONLY_LEAF_CLASSIFICATION_PASS total=[sizeof_collection $leaves]"
} problem]} {puts stderr "READONLY_LEAF_CLASSIFICATION_FAILED $problem"; exit 1}
exit 0
'''
for key, path in {'STD': PROJECT / 'build/dc_libraries/standard.db',
                  'SRAM': PROJECT / 'build/dc_libraries/sram.db',
                  'DDC': source / 'mapped.ddc', 'COUNTS': case / 'counts.tsv'}.items():
    tcl = tcl.replace('{' + key + '}', '{' + str(path) + '}')
(case / 'run.tcl').write_text(tcl)
with (case / 'run.log').open('w') as stream:
    result = subprocess.run(['dc_shell', '-f', str(case / 'run.tcl')], cwd=case,
                            stdout=stream, stderr=subprocess.STDOUT, timeout=300)
assert result.returncode == 0
actual = {}
bindings = {}
for line in (case / 'counts.tsv').read_text().splitlines()[1:]:
    name, count, lib, design = line.split('\t')
    actual[name] = int(count)
    bindings[name] = {'library_cells': lib, 'mapped_design': design}
differences = {name: {'DC': actual.get(name, 0), 'mapped_SDF': expected.get(name, 0),
                     **bindings.get(name, {})}
               for name in sorted(set(actual) | set(expected))
               if actual.get(name, 0) != expected.get(name, 0)}
assert sha(source / 'mapped.ddc') == proof['artifact_sha256']['mapped.ddc']
summary = {'status': 'READONLY_CLASSIFICATION_DIAGNOSTIC_COMPLETE',
           'DC_leaf_total': sum(actual.values()), 'physical_SDF_total': sum(expected.values()),
           'differences': differences,
           'source_ddc_sha256': sha(source / 'mapped.ddc'),
           'driver_sha256': sha(Path(__file__)),
           'scope': 'Diagnostic only. Source DDC read unchanged; no mapping, count waivers or acceptance.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary), flush=True)
