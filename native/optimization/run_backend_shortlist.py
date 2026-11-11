"""Bounded, serial continuation of phase1 backend mappings, no inference.

Waits for the two existing collectors before launching another large DC job.
This avoids duplicate mapping and reserves memory for the L16 array scan.
"""
import json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
needed=[('R8_C32_L4','private'),('R8_C16_L4','shared')]
deadline=time.monotonic()+10800
while True:
 missing=[]
 for identifier,mode in needed:
  case=ROOT/'work/backend-synthesis'/identifier/mode;p=case/'summary.json'
  if p.exists():
   proof=json.loads(p.read_text());assert proof['status']=='MAPPED_UNIT_SETUP_PASS',str(p)
  else:
   log=ROOT/('backend-resource-r8c32.log'if identifier=='R8_C32_L4'else'backend-resource-r8c16-sized.log')
   if log.exists()and'Traceback (most recent call last)'in log.read_text(errors='replace'):raise RuntimeError('existing collector failed: '+str(log))
   missing.append(identifier+'/'+mode)
 if not missing:break
 if time.monotonic()>deadline:raise TimeoutError('waiting for existing mapping collectors: '+','.join(missing))
 print('SHORTLIST_WAIT_EXISTING '+','.join(missing),flush=True);time.sleep(30)
for rows,cols in[(8,32),(16,8)]:
 identifier='R%d_C%d_L4'%(rows,cols);print('SHORTLIST_MAPPING '+identifier+' both',flush=True)
 # Each mapping script verifies source hashes, skips completed cases and owns
 # its DC process group, address-space cap and two-hour per-case deadline.
 subprocess.run([sys.executable,str(ROOT/'synthesize_backend_choices.py'),'--rows',str(rows),'--cols',str(cols),'--lanes','4','--resume'],check=True)
print('BACKEND_SHORTLIST_PHASE1_MAPPING_COMPLETE_NOT_SOC_ADP',flush=True)
