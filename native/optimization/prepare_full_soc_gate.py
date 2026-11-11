"""Prepare all8 complete-SoC SDF gate fixtures without claiming execution.

Reuse the baseline's audited value-preserving SDF/model compatibility logic.
Adapt its top name, SRAM count and verified alias-only hierarchy handling in
an isolated helper copy. All physical leaves and timing values remain checked.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
PROJECT = TASK / 'my_design/project'
SCRIPTS = PROJECT / 'scripts'
parser = argparse.ArgumentParser()
parser.add_argument('--geometry', choices=['R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4'], default='R8_C16_L4')
parser.add_argument('--mode', choices=['shared', 'private'], default='shared')
parser.add_argument('--timing-revision', default='v1')
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
assert all(re.fullmatch(r'[A-Za-z0-9_-]+', x) for x in [args.timing_revision, args.revision])
rows, cols, lanes = map(int, re.fullmatch(r'R(\d+)_C(\d+)_L(\d+)', args.geometry).groups())
source = ROOT / 'work/full-soc-timing' / args.geometry / args.mode / args.timing_revision
gold = ROOT / ('work/complete-soc-' + args.geometry) / args.mode
case = ROOT / 'work/full-soc-gate' / args.geometry / args.mode / args.revision
assert not case.exists(), 'Preserve prior attempt; use a new revision'


def local(name):
    return BASE / name[11:] if name.startswith('/workspace/') else Path(name)


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def pure_alias_modules(netlist):
    """Allow only declarations, constant bit wiring and other pure aliases.

    Arithmetic, boolean operators, dynamic selects and behavioral statements
    cannot enter this zero-physical-cell list. Physical leaf counting remains
    the original SDF verifier's responsibility.
    """
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', netlist.read_text(), flags=re.S)
    modules = dict(re.findall(r'(?ms)^\s*module\s+(\w+)\s*\(.*?;(.*?)^\s*endmodule\b', text))
    instance = re.compile(r'(?m)^[ \t]*(\w+)[ \t]+(\\\S+|[A-Za-z_$][\w$]*)[ \t]*\([^;]*\);')
    assignment = re.compile(r'(?m)^[ \t]*assign[ \t]+([^;]+);')
    atom = re.compile(r"\d+'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+|[A-Za-z_$][\w$]*|\d+")
    validated, rejected = {}, set()

    def check(name, active):
        if name in validated:
            return True
        if name in rejected or name not in modules or name in active:
            return False
        body = modules[name]
        children = instance.findall(body)
        if not all(check(ref, active | {name}) for ref, _ in children):
            rejected.add(name)
            return False
        remainder = instance.sub('', body)
        remainder = re.sub(r'(?m)^[ \t]*(?:input|output|wire)[ \t]+[^;]+;', '', remainder)
        assigns = assignment.findall(remainder)
        if not assigns and not children:
            rejected.add(name)
            return False
        for statement in assigns:
            if statement.count('=') != 1:
                rejected.add(name)
                return False
            lhs, rhs = statement.split('=')
            for expression in [lhs, rhs]:
                expression = re.sub(r'\\\S+', 'ESCAPED_WIRE', expression)
                selects = re.findall(r'\[([^\]]*)\]', expression)
                if any(not re.fullmatch(r'\s*\d+\s*(?::\s*\d+\s*)?', select) for select in selects):
                    rejected.add(name)
                    return False
                if re.sub(r'[\s{}\[\]:,]', '', atom.sub('', expression)):
                    rejected.add(name)
                    return False
        if assignment.sub('', remainder).strip():
            rejected.add(name)
            return False
        validated[name] = {'constant_bit_alias_assignments': len(assigns),
                           'alias_child_instances': len(children),
                           'physical_cells': 0,
                           'body_sha256': hashlib.sha256(body.encode()).hexdigest()}
        return True

    for name in modules:
        check(name, set())
    return validated


proof = json.loads((source / 'summary.json').read_text())
assert proof['status'] == 'FULL_ALL8_SOC_MAPPED_TIMING_PASS'
assert proof['clock_period_ns'] == 10 and proof['SRAMs'] == rows * cols
for name, wanted in proof['artifact_sha256'].items():
    assert sha(source / name) == wanted, name
gold_proof = json.loads((gold / 'summary.json').read_text())
assert gold_proof['status'] == 'PASS'
for name, wanted in gold_proof['source_sha256'].items():
    assert sha(local(name)) == wanted, name
for name, wanted in gold_proof['fixture_sha256'].items():
    assert sha(gold / name) == wanted, name
case.mkdir(parents=True)
inputs = case / 'inputs'
inputs.mkdir()
originals = [Path(__file__), source / 'summary.json', source / 'mapped.v',
             source / 'mapped.sdf', source / 'constraints.sdc', gold / 'summary.json',
             gold / 'report.txt', *(gold / name for name in ['program.hex', 'tb_soc.sv', 'axi_ram_model.sv', 'monitor.sv']),
             TASK / 'libraries/standard_timing.v', TASK / 'libraries/sram_timing.v',
             SCRIPTS / 'prepare_soc_sdf.py', SCRIPTS / 'prepare_pe_sdf_models.py',
             SCRIPTS / 'prepare_pe_axi_sdf.py', PROJECT / 'snapshots/soc/common_defines.vh']
hashes = {str(path): sha(path) for path in originals}
for old, new in [(source / 'mapped.v', inputs / 'soc_mapped.v'),
                 (gold / 'program.hex', inputs / 'program.hex'),
                 (gold / 'tb_soc.sv', inputs / 'tb_soc.sv'),
                 (gold / 'axi_ram_model.sv', inputs / 'axi_ram_model.sv')]:
    shutil.copyfile(old, new)
helper = (SCRIPTS / 'prepare_soc_sdf.py').read_text()
assert helper.count("TOP = 'soc_top'") == 1
assert helper.count('if self.expected_leaves[SRAM] != 256:') == 1
helper = helper.replace("TOP = 'soc_top'", "TOP = 'soc_accel_top'")
helper = helper.replace('if self.expected_leaves[SRAM] != 256:',
                        'if self.expected_leaves[SRAM] != %d:' % (rows * cols))
helper = helper.replace('Expected 256 physical SRAM leaves in soc_top',
                        'Expected %d physical SRAM leaves in soc_accel_top' % (rows * cols))
aliases = pure_alias_modules(inputs / 'soc_mapped.v')
assert 'reverse_width23_0' in aliases, 'Expected pure HardFloat bit-reversal wiring'
helper = helper.replace("TOP = 'soc_accel_top'", "TOP = 'soc_accel_top'\nALIAS_ONLY_MODULES = " + repr(set(aliases)))
assert helper.count('if not counts:') == 1
helper = helper.replace('if not counts:', 'if not counts and name not in ALIAS_ONLY_MODULES:')
(inputs / 'prepare_soc_sdf.py').write_text(helper)
sys.path.insert(0, str(SCRIPTS))
from prepare_pe_sdf_models import prepare_models
spec = importlib.util.spec_from_file_location('all8_soc_sdf_preparation', inputs / 'prepare_soc_sdf.py')
preparer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparer)
print('FULL_SOC_GATE_PREPARE_MODELS', flush=True)
models = prepare_models(TASK / 'libraries/standard_timing.v',
                        TASK / 'libraries/sram_timing.v', case / 'models')
monitor = (gold / 'monitor.sv').read_text()
# Observe preserved mapped module ports where synthesis has removed top aliases.
taps = ['request_valid', 'request_ready', 'request_opcode', 'engine_done',
        'engine_cycles', 'engine_bytes', 'engine_error']
for name in taps:
    monitor = monitor.replace('tb_soc.dut.' + name, 'tb_soc.dut.u_backend.' + name)
for channel in ['arvalid', 'arready', 'awvalid', 'awready', 'wvalid', 'wready']:
    monitor = monitor.replace('tb_soc.dut.dma_axi_' + channel,
                              'tb_soc.dut.u_backend.m_axi_' + channel)
monitor = monitor.replace('tb_soc.dut.u_backend.fp_valid',
                          'tb_soc.dut.u_backend.u_shared_fp32.out_valid')
assert args.mode == 'shared', 'Private FP monitor taps need their own explicit adaptation'
monitor = monitor.replace('module complete_soc_monitor;',
                          'module complete_soc_monitor;\nreg gate_completed=0;')
needle = '$fclose(fd);$display("COMPLETE_SOC_PASS commands=22 all8_engines=1");'
assert monitor.count(needle) == 1
monitor = monitor.replace(needle, '$fclose(fd);gate_completed=1;$display("COMPLETE_SOC_PASS commands=22 all8_engines=1");')
(inputs / 'monitor.sv').write_text(monitor)
macro = r'tb_soc.dut.u_backend.u_array.\R[0].C[0].u_weight .u_macro'
observer = r'''`timescale 1ns/1fs
module full_soc_sdf_observer;
realtime last_macro_clock=0,delay_ns=0,first_delay_ns=0;
integer delayed_macro_events=0;
realtime swc_source_time=0,iwsba_source_time=0;
realtime swc_first_delay=0,iwsba_first_delay=0;
integer swc_delay_events=0,iwsba_delay_events=0;
bit timing_probe=0;
initial begin
 timing_probe=$test$plusargs("FULL_SOC_SDF_TIMING_PROBE");
 $sdf_annotate("soc.sdf",tb_soc.dut,,"sdf_annotation.log","MAXIMUM");
 $display("FULL_SOC_SDF_ANNOTATION_REQUESTED selection=MAXIMUM");
end
// These physical endpoints are taken from the prior compile's exact SDF
// notices. The new compile/auditor must independently prove their presence,
// connection and triplets. Observe actual pin delays on this same executable.
always@(tb_soc.dut.u_backend.u_reorder.writer.U1511.ZN)
 swc_source_time=$realtime;
always@(tb_soc.dut.u_backend.u_reorder.writer.sll_66.U320.A1)begin
 if(!timing_probe&&swc_source_time>0&&$realtime-swc_source_time>0&&$realtime-swc_source_time<0.5)begin
  if(swc_delay_events==0)swc_first_delay=$realtime-swc_source_time;
  swc_delay_events=swc_delay_events+1;
 end
end
always@(tb_soc.dut.u_backend.u_reorder.writer.sll_66.U224.ZN)
 iwsba_source_time=$realtime;
always@(tb_soc.dut.u_backend.u_reorder.writer.U1268.A1)begin
 if(!timing_probe&&iwsba_source_time>0&&$realtime-iwsba_source_time>0&&$realtime-iwsba_source_time<0.5)begin
  if(iwsba_delay_events==0)iwsba_first_delay=$realtime-iwsba_source_time;
  iwsba_delay_events=iwsba_delay_events+1;
 end
end
always@(posedge MACRO.clk)last_macro_clock=$realtime;
always@(MACRO.rd_out)begin
 delay_ns=$realtime-last_macro_clock;
 if(!timing_probe&&last_macro_clock>0&&!$isunknown(MACRO.rd_out)&&delay_ns>0.5&&delay_ns<5.0)begin
  if(delayed_macro_events==0)first_delay_ns=delay_ns;
  delayed_macro_events=delayed_macro_events+1;
 end
end
always@(posedge complete_soc_monitor.gate_completed)begin
 if(delayed_macro_events==0)$fatal(1,"FULL_SOC_SDF_OBSERVER_FAIL no annotated SRAM delay");
 if(swc_delay_events==0||iwsba_delay_events==0)$fatal(1,"FULL_SOC_SDF_OBSERVER_FAIL no warned interconnect delay");
 $display("FULL_SOC_SDF_DELAY_OBSERVED events=%0d first_sram_ns=%0.9f",delayed_macro_events,first_delay_ns);
 $display("FULL_SOC_SDF_WARNED_DELAY_OBSERVED SWC_events=%0d SWC_first_ns=%0.9f IWSBA_events=%0d IWSBA_first_ns=%0.9f",swc_delay_events,swc_first_delay,iwsba_delay_events,iwsba_first_delay);
end
initial if($test$plusargs("FULL_SOC_SDF_TIMING_PROBE"))begin
 @(negedge MACRO.clk);
 if(tb_soc.rst_n!==1'b0)$fatal(1,"FULL_SOC_SDF_PROBE_FAIL CPU not in reset");
 force MACRO.wd_in[0]=1'b0;
 @(posedge MACRO.clk);
 #0.0001;
 force MACRO.wd_in[0]=1'b1;
 #1;
 $display("FULL_SOC_SDF_TIMING_PROBE_DONE");$finish;
end
endmodule
'''.replace('MACRO', macro)
(inputs / 'observer.sv').write_text(observer)
(case / 'preparation.json').write_text(json.dumps({
    'status': 'FULL_SOC_GATE_FIXTURES_READY_SDF_PREPARATION_RUNNING',
    'source_sha256': hashes, 'geometry': args.geometry, 'mode': args.mode,
    'source_case': str(source), 'gold_RTL_case': str(gold),
    'expected_RTL_engine_trace': gold_proof['engine_trace'],
    'original_RTL_normal_argv': next(command['argv'] for command in gold_proof['commands'] if command['label'] == 'normal'),
    'model_adaptation': models, 'verified_zero_cell_alias_modules': aliases,
    'scope': 'Fixture and model preparation only; no gate execution or SDF annotation acceptance.'
}, indent=2) + '\n')
print('FULL_SOC_GATE_PREPARE_VALUE_PRESERVING_SDF', flush=True)
adaptation = preparer.prepare_soc_sdf(source / 'mapped.sdf', case / 'soc.sdf', inputs / 'soc_mapped.v')
for name, wanted in hashes.items():
    assert sha(Path(name)) == wanted, name
assert adaptation['top'] == 'soc_accel_top' and adaptation['sram_instances'] == rows * cols
assert adaptation['value_triplets_preserved_exactly'] and adaptation['instance_paths_and_celltypes_checked']
record = json.loads((case / 'preparation.json').read_text())
record.update(status='FULL_SOC_GATE_FIXTURES_AND_SDF_READY_EXECUTION_PENDING',
              sdf_adaptation=adaptation,
              compatibility_helper_changes='isolated top name, exact SRAM count and explicitly verified zero-cell bit-wiring module handling; physical leaf, timing conversion/value/instance checks unchanged',
              prepared_sha256={str(path): sha(path) for path in [*inputs.iterdir(),
                  case / 'models/standard_model.v', case / 'models/sram_model.v', case / 'soc.sdf',
                  case / 'soc.sdf.changes.jsonl', case / 'soc.sdf.compatibility.json']})
(case / 'preparation.json').write_text(json.dumps(record, indent=2) + '\n')
print('FULL_SOC_GATE_FIXTURES_AND_SDF_READY_NOT_EXECUTED', flush=True)
