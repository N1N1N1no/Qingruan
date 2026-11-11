"""Functional matrix + shared-GELU DMA checks for every array candidate."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
case=ROOT/'work/controller-sweep';case.mkdir(parents=True,exist_ok=True)
configs=[(8,32,4),(16,16,4),(8,32,8),(16,16,8),(16,32,4),(32,16,4),(8,16,4),(16,8,4),(4,64,4),(16,16,16)]
results=[]
for r,c,l in configs:
 identifier='R%d_C%d_L%d'%(r,c,l)
 for op in ['gemm','gelu']:
  print('CONTROLLER_TEST '+identifier+' '+op,flush=True)
  subprocess.run([sys.executable,str(ROOT/('test_axi_'+op+'.py')),'--rows',str(r),'--cols',str(c),'--lanes',str(l),'--sweep'],check=True)
  proof=json.loads((case/identifier/op/'summary.json').read_text());assert proof['status']=='PASS'
 results.append({'id':identifier,'rows':r,'columns':c,'lanes':l,'SRAMs':r*c,'GELU_cache_channels':min(16,r*c//16),'status':'PASS'})
 (case/'summary.json').write_text(json.dumps({'status':'RUNNING','completed':results},indent=2)+'\n')
(case/'summary.json').write_text(json.dumps({'status':'PASS','completed':results,'scope':'all10 matrix/GELU DMA controllers, functional only; not full SoC area or end-to-end ADP'},indent=2)+'\n')
print('ALL10_CONTROLLER_CONFIGURATIONS_PASS',flush=True)
