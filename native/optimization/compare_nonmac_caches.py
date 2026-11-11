"""Compare matched normal LN/vector module commands across PE SRAM capacities.

Full Softmax totals are excluded because its 128-bank suite omits large
oracle rows. Only its fixed 49-channel alignment/broadcast cases are matched.
"""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent;source=ROOT/'work/backend-fp-pool/summary.json'
data=json.loads(source.read_text());assert data['status']=='PASS';comparisons=[]
for tag in['layernorm','vector']:
 bench=ROOT/'tests'/('tb_axi_'+tag+'_engine.sv');text=bench.read_text()
 # BANKS changes only DUT/array configuration before the transfer task, not
 # any normal command parameters, input generator or oracle selection.
 task=text.index('task automatic transfer(')
 uses=[line for line in text[task:].splitlines()if'BANKS'in line]
 assert len(uses)==1 and uses[0].strip().startswith('$display("AXI_'+tag.upper()+'_PASS banks=')
 assert'$urandom'not in text and'$random'not in text
 cases={}
 for shared in[False,True]:
  group=[next(x for x in data['completed']if x['tag']==tag and x['banks']==banks and x['shared']==shared)for banks in[128,256,512]]
  assert all(x['source_sha256']==group[0]['source_sha256']for x in group)
  assert all(len(x['cases'])==len(group[0]['cases'])for x in group)
  valid=[i for i,x in enumerate(group[0]['cases'])if x['error']==0 and x['bytes']>0]
  for proof in group:
   assert all((x['case'],x['bytes'],x['error'])==(y['case'],y['bytes'],y['error'])for x,y in zip(proof['cases'],group[0]['cases']))
   cases[proof['banks'],shared]=[proof['cases'][i]for i in valid]
 for banks in[128,256,512]:assert cases[banks,False]==cases[banks,True]
 baseline=cases[128,True];totals={str(banks):sum(x['cycles']for x in cases[banks,True])for banks in[128,256,512]}
 comparisons.append({'engine':tag,'matched_normal_commands':len(baseline),'written_bytes':sum(x['bytes']for x in baseline),'cycles_by_banks':totals,
  'normal_case_indices':[x['case']for x in baseline],'cycle_reduction_128_to_256_percent':(1-totals['256']/totals['128'])*100,
  'cycle_reduction_128_to_512_percent':(1-totals['512']/totals['128'])*100,'private_and_shared_cycles_equal':True})
softmax={};softmax_indices={}
for shared in[False,True]:
 group=[next(x for x in data['completed']if x['tag']=='softmax'and x['banks']==banks and x['shared']==shared)for banks in[128,256,512]]
 assert all(x['source_sha256']==group[0]['source_sha256']for x in group)
 for proof in group:
  mode='shared'if shared else'private';log=(ROOT/'work/backend-fp-pool'/('softmax_'+mode+'_'+str(proof['banks']))/'simulate.log').read_text()
  start=int(re.search(r'AXI_SOFTMAX_PASS oracle_rows=(\d+)',log)[1]);cases=proof['cases'][start:start+65]
  assert len(cases)==65 and all(x['error']==0 and x['bytes']==(588 if j<64 else 1372)for j,x in enumerate(cases))
  softmax[proof['banks'],shared]=[{'cycles':x['cycles'],'bytes':x['bytes'],'error':x['error']}for x in cases]
  softmax_indices[str(proof['banks'])]=[x['case']for x in cases]
for banks in[128,256,512]:assert softmax[banks,False]==softmax[banks,True]
totals={str(banks):sum(x['cycles']for x in softmax[banks,True])for banks in[128,256,512]}
comparisons.append({'engine':'softmax_49_fixed_alignment_and_broadcast','matched_normal_commands':65,'written_bytes':sum(x['bytes']for x in softmax[128,True]),
 'cycles_by_banks':totals,'normal_case_indices_by_banks':softmax_indices,'cycle_reduction_128_to_256_percent':(1-totals['256']/totals['128'])*100,
 'cycle_reduction_128_to_512_percent':(1-totals['512']/totals['128'])*100,'private_and_shared_cycles_equal':True,
 'match_basis':'after reopening the same first49 oracle row: 64 alignments of 49x3 plus one 49x7 source-row broadcast; excludes all unmatched oracle and error/cancel cases'})
summary={'status':'MATCHED_NORMAL_MODULE_CACHE_COMPARISON_PASS','results':comparisons,
 'scope':'matched normal module regression commands/inputs/oracles and reset AXI stall phases; complete backend configurations ROWS=BANKS/32,COLS=32,LANES=4; excludes invalid/fault/cancel commands, full Softmax aggregate sets, CPU and full inference; not SoC ADP',
 'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in[source,Path(__file__),ROOT/'tests/tb_axi_layernorm_engine.sv',ROOT/'tests/tb_axi_vector_engine.sv',ROOT/'tests/tb_axi_softmax_engine.sv']}}
(ROOT/'nonmac-cache-comparison.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps([{k:v for k,v in x.items()if not k.startswith('normal_case_indices')}for x in comparisons],indent=2),flush=True)
