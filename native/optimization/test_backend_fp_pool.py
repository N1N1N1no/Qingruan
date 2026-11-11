"""Same complete-backend module suites with private vs one pooled FP32 ALU."""
import argparse,hashlib,json,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1]
TASK=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1';HF=ROOT/'vendor/HardFloat-1/source'
parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');args=parser.parse_args()
case=ROOT/'work/backend-fp-pool';case.mkdir(parents=True,exist_ok=True)
for flag in[[],['--shared-fp32']]:subprocess.run([sys.executable,str(ROOT/'build_accel_backend.py'),'--with-reorder',*flag],check=True)
axi_fields=['awaddr','awid','awlen','awsize','awburst','awvalid','awready','wdata','wstrb','wlast','wvalid','wready','bid','bresp','bvalid','bready','araddr','arid','arlen','arsize','arburst','arvalid','arready','rdata','rid','rresp','rlast','rvalid','rready']
definitions={
 'softmax':['source','destination','channels','rows','source_stride','destination_stride'],
 'layernorm':['source','destination','scale','bias','elements','rows','source_stride','destination_stride','epsilon'],
 'vector':['a','b','c','destination','elements','rows','a_stride','b_stride','c_stride','destination_stride','operation','flags','zero','scalar_b','scalar_c','scalar_d']}
common=[Path(x)for x in json.loads((ROOT/'hardfloat-files.json').read_text())]+[ROOT/'rtl'/x for x in[
 'fp32_exp.sv','fp32_softmax_row.sv','fp32_layernorm.sv','fp32_vector.sv','axi_softmax_engine.sv','axi_layernorm_engine.sv','axi_vector_engine.sv','axi_reorder_engine.sv',
 'axi_dma_reader.sv','axi_dma_writer.sv','axi_dma_copy.sv','axi_gelu_engine.sv','gelu_lut_stream.sv','axi_affine_gemm.sv','quant_lookup8.v','axi_quant_engine.sv','affine_array.v']]+[
 TASK/'my_design/project/rtl/rfsp16x8.v',TASK/'my_design/project/tb/sram_reference_model.v']
all_results=[]
for tag,opcode in [('softmax',5),('layernorm',6),('vector',7)]:
 source=ROOT/'tests'/('tb_axi_'+tag+'_engine.sv');original=source.read_text()
 for shared in[False,True]:
  mode='shared'if shared else'private';identifier=tag+'_'+mode
  text=original.replace('module tb_axi_'+tag+'_engine;', 'module tb_backend_'+tag+';')
  if tag=='softmax':
   text=text.replace('module tb_backend_softmax;', '`ifndef FP_BACKEND_BANKS\n`define FP_BACKEND_BANKS 256\n`endif\nmodule tb_backend_softmax;\nlocalparam BANKS=`FP_BACKEND_BANKS;')
   text=text.replace('transfer(n,1,oracle_rows%8,(oracle_rows*3)%8,0,0,0,0,0,0);oracle_rows=oracle_rows+1;', 'if(n<=BANKS*4)begin transfer(n,1,oracle_rows%8,(oracle_rows*3)%8,0,0,0,0,0,0);oracle_rows=oracle_rows+1;end')
   text=text.replace('3:cmd_channels=1025;', '3:cmd_channels=BANKS*4+1;')
  start=text.index('axi_'+tag+'_engine ');end=text.index('));',text.index('affine_array',start))+3
  exprs=["32'b0"]*(16-len(definitions[tag]))+['cmd_'+x for x in reversed(definitions[tag])]
  ports=[('clk','clk'),('rst_n','rst_n'),('request_valid','cmd_valid'),('request_ready','cmd_ready'),('request_opcode',"8'd"+str(opcode)),
   ('request_arguments','{'+','.join(exprs)+'}'),('cancel','cancel'),('engine_busy','busy'),('engine_done','done'),('engine_error','error'),('engine_cycles','cycles'),('engine_bytes','streamed_bytes')]
  ports += [('m_axi_'+f,'m_axi_'+f)for f in axi_fields]
  inst='axi_accel_backend #(.ROWS(BANKS/32),.COLS(32),.LANES(4))dut(\n '+',\n '.join('.%s(%s)'%x for x in ports)+'\n);'
  text=text[:start]+inst+text[end:]
  text=text.replace('dut.','dut.u_'+tag+'.')
  if shared:
   path='dut.u_'+tag+('.core'if tag=='softmax'else'')+'.PRIVATE.u_alu.'
   text=text.replace(path,'dut.u_shared_fp32.')
  text=text.replace('memory.delete();','tick=0;memory.delete();')
  text=text.replace(' commands=commands+1;', ' $display("BACKEND_FP_CASE case=%0d cycles=%0d bytes=%0d error=%0d",commands,cycles,streamed_bytes,error);\n commands=commands+1;')
  if shared:
   text=text.replace('initial begin\n if(!$value$plusargs', 'always@(posedge clk)if(rst_n&&dut.fp_valid&&dut.owner!=5&&dut.owner!=6&&dut.owner!=7)$fatal(1,"BACKEND_FP_FAIL orphaned FP response");\ninitial begin\n if(!$value$plusargs')
  bench=case/('tb_'+identifier+'.sv');bench.write_text(text)
  vector_path=ROOT/'work'/({'softmax':'softmax-reference','layernorm':'layernorm-reference','vector':'vector-dma-reference'}[tag])/'vectors.txt'
  backend=ROOT/'rtl'/('axi_accel_shared_backend.sv'if shared else'axi_accel_complete_backend.sv');files=common+[backend,bench]
  hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files+[vector_path]}
  for banks in[128,256,512]:
   directory=case/(identifier+'_'+str(banks));directory.mkdir(exist_ok=True)
   if args.resume and(directory/'summary.json').exists():
    proof=json.loads((directory/'summary.json').read_text());assert proof['source_sha256']==hashes and proof['status']=='PASS';all_results.append(proof);continue
   assert not(directory/'summary.json').exists(),'use --resume for completed proof'
   def run(argv,label):
    print('FP_POOL_PHASE '+directory.name+' '+label,flush=True);started=time.monotonic()
    with(directory/(label+'.log')).open('w')as log:
     p=subprocess.Popen(list(map(str,argv)),cwd=directory,stdout=log,stderr=subprocess.STDOUT)
     while True:
      try:code=p.wait(timeout=30);break
      except subprocess.TimeoutExpired:
       print('FP_POOL_RUNNING '+directory.name+' '+label,flush=True)
       if time.monotonic()-started>1800:p.terminate();p.wait(timeout=20);raise TimeoutError(directory.name)
    if code:raise RuntimeError(str(directory/(label+'.log')))
    return(directory/(label+'.log')).read_text(errors='replace')
   run(['/usr/bin/gcc','-fPIC','-c',TASK/'my_design/compat/pthread_yield_compat.c','-o',directory/'compat.o'],'compat')
   defines=['+define+'+name+'='+str(banks)for name in['FP_BACKEND_BANKS','LAYERNORM_BANKS','VECTOR_BANKS']]
   run(['/home/zhz/miniforge3/bin/python3',TASK/'scripts/vcs_vm.py','-full64','-sverilog','-timescale=1ns/1ps','+define+PE_REFERENCE_MACRO',*defines,
    '+incdir+'+str(HF),'+incdir+'+str(HF/'RISCV'),*files,'-top','tb_backend_'+tag,'-Mdir='+str(directory/'csrc'),'-o',directory/'simv','-LDFLAGS',directory/'compat.o'],'compile')
   log=run([directory/'simv','+VECTORS='+str(vector_path)],'simulate');assert'AXI_'+tag.upper()+'_PASS'in log and'Fatal:'not in log
   cases=[dict(zip(['case','cycles','bytes','error'],map(int,m.groups())))for m in re.finditer(r'BACKEND_FP_CASE case=(\d+) cycles=(\d+) bytes=(\d+) error=(\d+)',log)]
   assert cases and all(x['case']==i for i,x in enumerate(cases))
   for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
   proof={'status':'PASS','tag':tag,'shared':shared,'banks':banks,'cases':cases,'source_sha256':hashes,'simulator_sha256':hashlib.sha256((directory/'simv').read_bytes()).hexdigest()}
   (directory/'summary.json').write_text(json.dumps(proof,indent=2)+'\n');all_results.append(proof)
   (case/'summary.json').write_text(json.dumps({'status':'RUNNING','completed':all_results},indent=2)+'\n')
comparisons=[]
for tag in definitions:
 for banks in[128,256,512]:
  a=next(x for x in all_results if x['tag']==tag and x['banks']==banks and not x['shared']);b=next(x for x in all_results if x['tag']==tag and x['banks']==banks and x['shared'])
  assert len(a['cases'])==len(b['cases'])and all((x['bytes'],x['error'])==(y['bytes'],y['error'])for x,y in zip(a['cases'],b['cases']))
  comparisons.append({'tag':tag,'banks':banks,'commands':len(a['cases']),'identical_engine_cycles':all(x['cycles']==y['cycles']for x,y in zip(a['cases'],b['cases'])),
   'private_cycles':sum(x['cycles']for x in a['cases']),'shared_cycles':sum(x['cycles']for x in b['cases'])})
(case/'summary.json').write_text(json.dumps({'status':'PASS','completed':all_results,'comparisons':comparisons,
 'scope':'all module suites through complete backend, private3 vs shared1 FP32, 128/256/512 PE SRAM; actual numeric/AXI fault/cancel checks, cycle comparison; mixed-owner/RV32/backend synthesis still separate; no full inference'},indent=2)+'\n')
print('FP_POOL_MODULE_COMPARISON_PASS',flush=True)
