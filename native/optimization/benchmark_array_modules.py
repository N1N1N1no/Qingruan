"""Compare array/control module cycles/utilization on common row-tile shapes.

This is phase-1 module evaluation, not full inference. Constant full-range
operands permit a closed-form oracle for every partial/final output without
making the simulator itself perform billions of reference multiplications.
The existing varied-data/fault suite remains the separate correctness gate.
"""
import argparse,hashlib,json,re,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1'
parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');parser.add_argument('--conv-only',action='store_true');args=parser.parse_args()
case=ROOT/'work'/('array-module-conv-direct'if args.conv_only else'array-module-benchmark');case.mkdir(parents=True,exist_ok=True)
shapes=[(49,49,32,1),(49,32,49,0),(64,96,96,0),(64,384,96,0),(64,96,384,0),
 (64,192,192,0),(64,768,192,0),(64,1536,384,0),(64,384,1536,0),(64,768,768,0),(1,1000,768,1)]
if args.conv_only:shapes=[(64,96,48,1)]
source=ROOT/'tests/tb_axi_affine_gemm.sv';tb=source.read_text().replace('tb_axi_affine_gemm','tb_array_module_benchmark')
tb=tb.replace('initial_byte=address*37+(address>>7)+13','initial_byte=8\'h80')
start=tb.index('function automatic[31:0]golden(');end=tb.index('endfunction',start)+len('endfunction')
tb=tb[:start]+'''function automatic[31:0]golden(input integer row,col,end_k);
 reg signed[63:0]sum;begin
  sum=(-128-$signed(cmd_a_zero))*(-128-$signed(cmd_b_zero));sum=sum*end_k;
  if(sum>64'sd2147483647||sum< -64'sd2147483648)$fatal(1,"BENCHMARK_FAIL oracle overflow");
  golden=sum[31:0];
 end
endfunction'''+tb[end:]
tb=tb.replace('integer waited,bb;','integer waited,bb,tiles_before,reads_before,writes_before;')
tb=tb.replace('memory.delete();fault=inject;', 'tick=0;tiles_before=compute_tiles;reads_before=read_bursts;writes_before=write_bursts;memory.delete();fault=inject;')
tb=tb.replace('waited>1000000','waited>20000000')
tb=tb.replace('  commands=commands+1;', '''  $display("ARRAY_MODULE_CASE index=%0d M=%0d N=%0d K=%0d TRANS=%0d CYCLES=%0d TILES=%0d READS=%0d WRITES=%0d BYTES=%0d",commands,mm,nn,kk,trans,cycles,compute_tiles-tiles_before,read_bursts-reads_before,write_bursts-writes_before,output_bytes);
  commands=commands+1;''')
start=tb.index('integer a,b,t,kcase,kk;')
tb=tb[:start]+'initial begin\n repeat(4)@(negedge clk);rst_n=1;\n'+''.join(' transfer(%d,%d,%d,%d,127,127,3,5,7,0,0,0);\n'%s for s in shapes)+''' $display("ARRAY_MODULE_BENCHMARK_PASS commands=%0d outputs=%0d",commands,outputs_checked);$finish;
end
endmodule
'''
bench=case/'tb_array_module_benchmark.sv';bench.write_text(tb)
gate=json.loads((ROOT/'work/controller-sweep/summary.json').read_text());assert gate['status']=='PASS'and len(gate['completed'])==10
files=[ROOT/'rtl'/n for n in ['axi_dma_reader.sv','axi_dma_writer.sv','axi_affine_gemm.sv','affine_array.v']]+[
 TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v',bench]
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files};results=[]
for config in gate['completed']:
 identifier=config['id'];directory=case/identifier;directory.mkdir(exist_ok=True)
 if args.resume and(directory/'summary.json').exists():
  proof=json.loads((directory/'summary.json').read_text());assert proof['status']=='PASS'and proof['source_sha256']==hashes
  assert proof['simulator_sha256']==hashlib.sha256((directory/'simv').read_bytes()).hexdigest()
  results.append(proof);print('ARRAY_MODULE_SKIP '+identifier,flush=True);continue
 assert not(directory/'summary.json').exists(),'use --resume for completed cases'
 def run(argv,label):
  print('ARRAY_MODULE_PHASE '+identifier+' '+label,flush=True);begin=time.monotonic()
  with(directory/(label+'.log')).open('w')as log:
   p=subprocess.Popen(list(map(str,argv)),cwd=directory,stdout=log,stderr=subprocess.STDOUT)
   while True:
    try:code=p.wait(timeout=30);break
    except subprocess.TimeoutExpired:
     print('ARRAY_MODULE_RUNNING '+identifier+' '+label,flush=True)
     if time.monotonic()-begin>1800:p.terminate();p.wait(timeout=20);raise TimeoutError(identifier)
  if code:raise RuntimeError(str(directory/(label+'.log')))
  return(directory/(label+'.log')).read_text(errors='replace')
 run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',directory/'compat.o'],'compat')
 run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+define+PE_REFERENCE_MACRO',
  '+define+ARRAY_ROWS='+str(config['rows']),'+define+ARRAY_COLS='+str(config['columns']),'+define+ARRAY_LANES='+str(config['lanes']),*files,
  '-top','tb_array_module_benchmark','-Mdir='+str(directory/'csrc'),'-o',directory/'simv','-LDFLAGS',directory/'compat.o'],'compile')
 log=run([directory/'simv'],'simulate');assert'ARRAY_MODULE_BENCHMARK_PASS commands='+str(len(shapes))in log and'Fatal:'not in log
 rows=[]
 for match in re.finditer(r'ARRAY_MODULE_CASE index=(\d+) M=(\d+) N=(\d+) K=(\d+) TRANS=(\d+) CYCLES=(\d+) TILES=(\d+) READS=(\d+) WRITES=(\d+) BYTES=(\d+)',log):
  index,m,n,k,tr,cycles,tiles,reads,writes,nb=map(int,match.groups());assert(m,n,k,tr)==shapes[index]and cycles>0 and tiles>0
  useful=m*n*k;issued=tiles*config['rows']*config['columns']*config['lanes'];assert useful<=issued
  rows.append({'case':index,'M':m,'N':n,'K':k,'trans_B':tr,'engine_cycles':cycles,'compute_tiles':tiles,'ar_bursts':reads,'aw_bursts':writes,'written_bytes':nb,'useful_MACs':useful,'issued_MAC_slots':issued,'slot_utilization':useful/issued})
 assert len(rows)==len(shapes)
 for path,want in hashes.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==want
 proof={'status':'PASS','id':identifier,'geometry':config,'results':rows,'total_engine_cycles':sum(x['engine_cycles']for x in rows),
  'source_sha256':hashes,'simulator_sha256':hashlib.sha256((directory/'simv').read_bytes()).hexdigest(),
  'scope':'common representative row-tile module workloads; actual controller/array/AXI with periodic stalls; constant full-range data checked against every partial/final result; excludes RV32/full inference/other operators/whole SoC ADP'}
 (directory/'summary.json').write_text(json.dumps(proof,indent=2)+'\n');results.append(proof)
 (case/'summary.json').write_text(json.dumps({'status':'RUNNING','completed':results},indent=2)+'\n')
(case/'summary.json').write_text(json.dumps({'status':'PASS','completed':results,'scope':'phase1 module comparison; final inference/SoC ADP selection remains phase2'},indent=2)+'\n');print('ALL10_ARRAY_MODULE_BENCHMARK_PASS',flush=True)
