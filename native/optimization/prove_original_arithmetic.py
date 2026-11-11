"""Prove exact combinational RTL cones before using a simulation adapter.

The original physical RTL/netlist remains unchanged. For known binary inputs
the signed-magnitude product and 33-bit sum are proved for every input. A
four-state simulator falls back to original gates if any input is unknown.
No memory, register, clock, control, latency or external arithmetic is added.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
PROJECT = BASE / 'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project'
case = ROOT / 'work/original-arithmetic-equivalence-v1'
assert not case.exists(), 'Preserve prior proof attempt'
source_mul = PROJECT / 'rtl/Exact_8bit.v'
source_add = PROJECT / 'rtl/adder.v'
case.mkdir()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


sources = {str(path): sha(path) for path in [Path(__file__), source_mul, source_add]}
original = source_mul.read_text() + '\n' + source_add.read_text()
assert len(re.findall(r'\bmodule\s+Exact_8bit\s*\(', original)) == 1
assert len(re.findall(r'\bmodule\s+CLA32bit\s*\(', original)) == 1
original = re.sub(r'\bmodule\s+Exact_8bit(?=\s*\()', 'module Exact_8bit_gate', original)
original = re.sub(r'\bmodule\s+CLA32bit(?=\s*\()', 'module CLA32bit_gate', original)
(case / 'original_gates.v').write_text(original)
adapter = r'''`timescale 1ns/1ps
`ifdef VERILATOR
 `define QINGRUAN_BASELINE_BINARY_EXPR
`endif
`ifdef BASELINE_BINARY_PROOF
 `define QINGRUAN_BASELINE_BINARY_EXPR
`endif
module Exact_8bit(input [7:0] N1,N2, output [15:0] P_APPROX);
 wire [13:0] magnitude=N1[6:0]*N2[6:0];
 wire [15:0] binary_result={N1[7]^N2[7],1'b0,magnitude};
`ifdef QINGRUAN_BASELINE_BINARY_EXPR
 assign P_APPROX=binary_result;
`else
 wire [15:0] gate_result;
 Exact_8bit_gate original(.N1(N1),.N2(N2),.P_APPROX(gate_result));
 assign P_APPROX=$isunknown({N1,N2})?gate_result:binary_result;
`endif
endmodule
module CLA32bit(input [31:0] A,B, input Cin, output [31:0] Sum, output Cout);
 wire [32:0] binary_result={1'b0,A}+{1'b0,B}+{32'b0,Cin};
`ifdef QINGRUAN_BASELINE_BINARY_EXPR
 assign {Cout,Sum}=binary_result;
`else
 wire [32:0] gate_result;
 CLA32bit_gate original(.A(A),.B(B),.Cin(Cin),.Sum(gate_result[31:0]),.Cout(gate_result[32]));
 assign {Cout,Sum}=$isunknown({A,B,Cin})?gate_result:binary_result;
`endif
endmodule
`undef QINGRUAN_BASELINE_BINARY_EXPR
'''
(case / 'simulation_arithmetic.v').write_text(adapter)
(case / 'miter.v').write_text('''module product_miter(input [7:0] N1,N2,output mismatch);
wire [15:0] gate_value,rtl_value;
Exact_8bit_gate g(.N1(N1),.N2(N2),.P_APPROX(gate_value));
Exact_8bit r(.N1(N1),.N2(N2),.P_APPROX(rtl_value));
assign mismatch=|(gate_value^rtl_value);
endmodule
module sum_miter(input [31:0] A,B,input Cin,output mismatch);
wire [32:0] gate_value,rtl_value;
CLA32bit_gate g(.A(A),.B(B),.Cin(Cin),.Sum(gate_value[31:0]),.Cout(gate_value[32]));
CLA32bit r(.A(A),.B(B),.Cin(Cin),.Sum(rtl_value[31:0]),.Cout(rtl_value[32]));
assign mismatch=|(gate_value^rtl_value);
endmodule
''')
results = []
for top, bits in [('product_miter', 16), ('sum_miter', 65)]:
    commands = ['read_verilog -sv -DBASELINE_BINARY_PROOF ' + ' '.join(
        str(case / name) for name in ['original_gates.v', 'simulation_arithmetic.v', 'miter.v']),
        'hierarchy -check -top ' + top, 'proc', 'flatten', 'opt', 'check -assert',
        'sat -verify -prove mismatch 0 -set-def-inputs -show-inputs -show-outputs']
    script = case / (top + '.ys')
    script.write_text('\n'.join(commands) + '\n')
    print('ORIGINAL_ARITHMETIC_FORMAL_PHASE ' + top, flush=True)
    start = time.monotonic()
    with (case / (top + '.log')).open('w') as stream:
        process = subprocess.run(['yosys', '-Q', '-T', '-s', str(script)], cwd=case,
                                 stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    log = (case / (top + '.log')).read_text()
    passed = process.returncode == 0 and 'SAT proof finished - no model found: SUCCESS!' in log
    record = {'top': top, 'binary_input_bits': bits, 'all_input_patterns_proved': passed,
              'returncode': process.returncode, 'elapsed_s': time.monotonic() - start,
              'script_sha256': sha(script), 'log_sha256': sha(case / (top + '.log'))}
    results.append(record)
    (case / 'progress.json').write_text(json.dumps(results, indent=2) + '\n')
    assert passed, str(case / (top + '.log'))
for path, expected in sources.items():
    assert sha(Path(path)) == expected
files = ['original_gates.v', 'simulation_arithmetic.v', 'miter.v',
         'product_miter.ys', 'product_miter.log', 'sum_miter.ys', 'sum_miter.log']
result = {'status': 'ORIGINAL_SIGNED_MAGNITUDE_PRODUCT_AND_CLA33_ALL_BINARY_INPUTS_FORMAL_PASS',
          'proofs': results, 'source_sha256': sources,
          'artifact_sha256': {name: sha(case / name) for name in files},
          'binary_product_cases': 65536, 'binary_sum_cases': str(2**65),
          'four_state_unknown_input_behavior': 'VCS branch selects original unchanged gates whenever any input is unknown; Verilator is inherently binary and uses the proved expression.',
          'registered_latency_or_state_changed': False, 'external_host_arithmetic_used': False,
          'original_physical_RTL_or_netlist_edited': False,
          'whole_CPU_PE_trace_crosscheck_pending': True,
          'full_model_latency_or_accuracy_accepted': False,
          'scope': 'Complete combinational SAT equivalence of the exact signed-magnitude8x8 output and33-bit CLA sum including carry for every binary input. This alone is not a sequential proof, full-model result, physical mapping or performance measurement. Only a simulation representation is prepared; full CPU/PE transaction/cycle crosscheck is still mandatory.'}
(case / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(result['status'], flush=True)
