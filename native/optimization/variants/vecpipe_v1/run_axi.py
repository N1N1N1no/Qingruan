"""Qualify the isolated streaming engine against the existing AXI suite.

The scalar engine runs the identical reference cases and deterministic bus
stalls. Extra fast-pipeline cancellation and reset checks apply to the variant.
No existing RTL, firmware, reference vectors or completed proof is rewritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT.parents[1]
TASK = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1'
HF = ROOT / 'vendor/HardFloat-1/source'
parser = argparse.ArgumentParser()
parser.add_argument('--revision', default='v1')
args = parser.parse_args()
case = ROOT / ('work/vecpipe-axi-' + args.revision)
assert not case.exists(), 'Preserve prior attempt; use a fresh revision'
case.mkdir()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(name):
    for prefix in ['/workspace/', '/mnt/data/Ubuntu/qingruan/']:
        if name.startswith(prefix):
            return BASE / name[len(prefix):]
    return Path(name)


proofs = [ROOT / ('work/vecpipe-' + tag + '-v1/summary.json')
          for tag in ['arithmetic', 'protocol']]
for path in proofs:
    proof = json.loads(path.read_text())
    assert proof['status'].endswith('_UNIT_PASS')
    for name, expected in proof['source_sha256'].items():
        assert sha(local(name)) == expected, name
generation = json.loads((HERE / 'engine-generation.json').read_text())
assert sha(ROOT / 'rtl/axi_vector_engine.sv') == generation['original_sha256']
assert sha(HERE / 'axi_vector_engine_vecpipe.sv') == generation['generated_sha256']
assert sha(HERE / 'create_engine.py') == generation['generator_sha256']

original_tb = ROOT / 'tests/tb_axi_vector_engine.sv'
template = original_tb.read_text()
template = template.replace('memory.delete();', 'tick=0;memory.delete();')
template = template.replace(' commands=commands+1;', ''' $display("VECPIPE_AXI_CASE case=%0d op=%0d cycles=%0d bytes=%0d error=%0d",commands,op_value,cycles,streamed_bytes,error);
 commands=commands+1;''')
fast_checks = '''
 // Stream POST with both vector parameters; exceed the DMA write buffer.
 fd=$fopen(vector_path,"r");load_case();
 while(op_value!=7||flags_value!=0||n_value!=1536)load_case();$fclose(fd);
 for(t=7;t<=8;t=t+1)begin transfer(3,7,0,t,0,4);transfer(0,0,0,0,0,0);end
 reset_stream(1);transfer(0,0,0,0,0,0);
 reset_stream(2);transfer(0,0,0,0,0,0);
 $display("VECPIPE_AXI_EXTRA_PASS fast_cancel=2 reset=2");
'''
reset_task = '''
task automatic reset_stream(input integer mode);
 integer waited;begin
 @(negedge clk);if(!cmd_ready)$fatal(1,"VECPIPE_AXI_FAIL reset start not ready");
 if(mode==2)w_stall=200000;
 cmd_valid=1;@(negedge clk);cmd_valid=0;waited=0;
 while(!(mode==1?dut.fetch_pending:(dut.core.valid[3]&&!dut.core_out_ready)))begin
  @(negedge clk);waited=waited+1;
  if(done||waited>200000)$fatal(1,"VECPIPE_AXI_FAIL reset condition not reached");
 end
 rst_n=0;repeat(4)@(negedge clk);rst_n=1;w_stall=0;
 repeat(2)@(negedge clk);
 if(busy||done||!cmd_ready||m_axi_arvalid||m_axi_awvalid||m_axi_wvalid||dut.core_busy||dut.fifo_count||dut.fetch_pending)
  $fatal(1,"VECPIPE_AXI_FAIL reset did not flush transaction");
 end
endtask
'''
common = list(dict.fromkeys([local(name) for name in json.loads((ROOT / 'hardfloat-files.json').read_text())] +
    [ROOT / 'rtl' / name for name in ['fp32_vector.sv', 'axi_dma_reader.sv', 'axi_dma_writer.sv', 'affine_array.v']] +
    [TASK / 'my_design/project/rtl/rfsp16x8.v', TASK / 'my_design/project/tb/sram_reference_model.v']))
vectors = ROOT / 'work/vector-dma-reference/vectors.txt'
inputs = [Path(__file__), original_tb, vectors, ROOT / 'work/vector-dma-reference/summary.json',
          HERE / 'engine-generation.json', HERE / 'create_engine.py', *proofs, *common,
          ROOT / 'rtl/axi_vector_engine.sv', HERE / 'axi_vector_engine_vecpipe.sv', HERE / 'fp32_vector_pipeline.sv']
hashes = {str(path): sha(path) for path in inputs}
results = []
records = []


def run(argv, label, directory, limit=1800):
    argv = list(map(str, argv))
    started = time.monotonic()
    print('VECPIPE_AXI_PHASE ' + label, flush=True)
    def memory_limit():
        resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    with (directory / (label + '.log')).open('w') as stream:
        child = subprocess.Popen(argv, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=memory_limit)
        try:
            while True:
                try:
                    code = child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print('VECPIPE_AXI_RUNNING %s elapsed_s=%d' % (label, time.monotonic() - started), flush=True)
                    if time.monotonic() - started > limit:
                        raise TimeoutError(label)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
    records.append({'label': label, 'argv': argv, 'returncode': code, 'elapsed_s': time.monotonic() - started})
    assert code == 0, str(directory / (label + '.log'))
    return (directory / (label + '.log')).read_text(errors='replace')


run(['/usr/bin/gcc', '-fPIC', '-c', TASK / 'my_design/compat/pthread_yield_compat.c',
     '-o', case / 'compat.o'], 'compat', case, 120)
for mode in ['scalar', 'pipeline']:
    text = template.replace('module tb_axi_vector_engine;', 'module tb_vecpipe_axi;')
    files = list(common)
    if mode == 'pipeline':
        text = text.replace('axi_vector_engine #', 'axi_vector_engine_vecpipe #')
        text = text.replace('dut.core.state', 'dut.core.scalar_div.state')
        text = text.replace('if(cancel_mode==3)w_stall=200000;', 'if(cancel_mode==3||cancel_mode==8)w_stall=200000;')
        text = text.replace('(cancel_mode==6&&dut.plane==1&&dut.read_valid)',
            '(cancel_mode==6&&dut.plane==1&&dut.read_valid)||(cancel_mode==7&&|dut.core.valid[2:0])||(cancel_mode==8&&dut.core.valid[3]&&!dut.core_out_ready)')
        text = text.replace('string vector_path;', reset_task + '\nstring vector_path;')
        text = text.replace(' $display("AXI_VECTOR_PASS', fast_checks + '\n $display("AXI_VECTOR_PASS')
        files += [HERE / 'fp32_vector_pipeline.sv', HERE / 'axi_vector_engine_vecpipe.sv']
    else:
        files += [ROOT / 'rtl/axi_vector_engine.sv']
    bench = case / ('tb_' + mode + '.sv')
    bench.write_text(text)
    hashes[str(bench)] = sha(bench)
    for banks in [128, 256, 512]:
        directory = case / (mode + '_' + str(banks))
        directory.mkdir()
        run(['/home/zhz/miniforge3/bin/python3', TASK / 'scripts/vcs_vm.py', '-full64', '-sverilog',
             '-timescale=1ns/1ps', '+define+PE_REFERENCE_MACRO', '+define+VECTOR_BANKS=' + str(banks),
             '+incdir+' + str(HF), '+incdir+' + str(HF / 'RISCV'), *files, bench,
             '-top', 'tb_vecpipe_axi', '-Mdir=' + str(directory / 'csrc'), '-o', directory / 'simv',
             '-LDFLAGS', case / 'compat.o'], 'compile', directory, 600)
        simulator = sha(directory / 'simv')
        log = run([directory / 'simv', '+VECTORS=' + str(vectors)], 'normal', directory)
        assert len(re.findall(r'AXI_VECTOR_PASS banks=', log)) == 1
        assert not re.search(r'Fatal:|Error-\[|VECPIPE_(?:AXI|FIFO)_FAIL|AXI_VECTOR_FAIL', log)
        if mode == 'pipeline':
            assert log.count('VECPIPE_AXI_EXTRA_PASS fast_cancel=2 reset=2') == 1
        cases = [dict(zip(['case', 'op', 'cycles', 'bytes', 'error'], map(int, match.groups())))
                 for match in re.finditer(r'VECPIPE_AXI_CASE case=(\d+) op=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)', log)]
        assert cases and all(item['case'] == i for i, item in enumerate(cases))
        assert sha(directory / 'simv') == simulator
        result = {'mode': mode, 'banks': banks, 'cases': cases, 'simulator_sha256': simulator,
                  'marker': re.search(r'AXI_VECTOR_PASS[^\n]+', log)[0]}
        (directory / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        results.append(result)
comparisons = []
for banks in [128, 256, 512]:
    old = next(item for item in results if item['mode'] == 'scalar' and item['banks'] == banks)
    new = next(item for item in results if item['mode'] == 'pipeline' and item['banks'] == banks)
    assert len(new['cases']) == len(old['cases']) + 6
    for left, right in zip(old['cases'], new['cases']):
        assert all(left[key] == right[key] for key in ['case', 'op', 'bytes', 'error'])
    # Finite successful reference workload; failures/cancellation excluded.
    before = sum(item['cycles'] for item in old['cases'][:120])
    after = sum(item['cycles'] for item in new['cases'][:120])
    comparisons.append({'banks': banks, 'identical_reference_commands': 120,
                        'scalar_reference_cycles': before, 'pipeline_reference_cycles': after,
                        'reference_workload_speedup': before / after})
for name, expected in hashes.items():
    assert sha(Path(name)) == expected, name
summary = {'status': 'VECPIPE_AXI_REFERENCE_FAULT_CANCEL_RESET_THREE_SRAM_GEOMETRIES_PASS',
           'results': results, 'comparisons': comparisons, 'source_sha256': hashes, 'commands': records,
           'real_RV32_accepted': False, 'full_model_inference_accepted': False,
           'SoC_area_timing_accepted': False, 'official_accuracy_accepted': False, 'final_ADP_accepted': False,
           'scope': 'Isolated AXI engine with actual existing PE SRAM models; same 120 cases including all67 model DQ groups, all alignments, broadcasts, tails, bus stalls, invalid commands and AXI faults. Scalar and pipeline share deterministic bus phases. Six original cancellation modes plus two fast-pipeline cancels and pending-read/stalled-output resets. Unit workload speedup is not complete inference latency.'}
(case / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(summary['status'], flush=True)
