"""Reject corrupt/mismatched node checkpoints before issuing any RTL command."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
compiled=ROOT/'work/rtl-model-checkpoint-r8-v1';image='ILSVRC2012_val_00000274.JPEG'
source=compiled/image/'uninterrupted.bin';raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
identity=json.loads(source.with_suffix('.bin.json').read_text())['identity']
case=ROOT/'work/checkpoint-rejection';case.mkdir(parents=True,exist_ok=True)
corrupt=bytearray(raw);corrupt[224+17]^=1
cases=[('payload_checksum',bytes(corrupt),identity),('truncated_header',raw[:100],identity),('trailing_bytes',raw+b'X',identity),('wrong_identity',raw,'0'*64)]
results=[]
for name,payload,who in cases:
 checkpoint=case/(name+'.bin');checkpoint.write_bytes(payload)
 output=case/(name+'.logits');report=case/(name+'.json')
 assert not output.exists()and not report.exists()
 argv=[compiled/'simv','+PLAN='+str(compiled/'assets'),'+INPUT='+str(ROOT/'work/fusion-proof'/(image+'.input')),
  '+EXPECTED='+str(ROOT/'work/fusion-proof'/(image+'.baseline')),'+OUTPUT='+str(output),'+REPORT='+str(report),'+CHECKPOINT='+str(checkpoint),'+IDENTITY='+who]
 with(case/(name+'.log')).open('w')as log:r=subprocess.run(list(map(str,argv)),cwd=compiled,stdout=log,stderr=subprocess.STDOUT,timeout=60)
 log=(case/(name+'.log')).read_text();assert'RTL_MODEL_BAD_CHECKPOINT'in log and'RTL_MODEL_FAIL C graph or output'in log
 assert'RTL_MODEL_AXI_PASS'not in log and'RTL_MODEL_PROGRESS'not in log and not output.exists()and not report.exists()
 results.append({'test':name,'rejected_before_graph_execution':True,'returncode':r.returncode})
assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
summary={'status':'PASS','results':results,'source_checkpoint_sha256':digest,'simulator_sha256':hashlib.sha256((compiled/'simv').read_bytes()).hexdigest(),
 'scope':'hybrid node-checkpoint integrity/identity rejection, not RV32 checkpoints or whole-inference timing'}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('CHECKPOINT_REJECTION_PASS cases=4',flush=True)
