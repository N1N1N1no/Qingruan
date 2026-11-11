"""One diagnostic image: ORT teacher inputs isolate every C operator's error."""
import json
from pathlib import Path
import numpy as np
import onnx
from swin.model import Model
from swin.reference import session

root=Path(__file__).resolve().parents[1]
case=root/'work/teacher-check';case.mkdir(exist_ok=True)
plan=json.loads((root/'work/plan.zk8_dxns/plan.json').read_text())
model=Model(root.parents[1]/'competition')
del model.model.graph.output[:]
names=[]
for t in plan['tensors']:
    if t['storage']=='activation' and t['name']!='input':
        model.model.graph.output.append(onnx.helper.make_tensor_value_info(t['name'],model.dtypes[t['name']],t['shape']))
        names.append(t)
onnx.save(model.model,case/'teacher.onnx')
x=np.load(root/'work/reference.p75zvgn2/ILSVRC2012_val_00000274.JPEG.input.npy')
values=session(case/'teacher.onnx').run(None,{'input':x})
td=case/'teacher';td.mkdir(exist_ok=True)
for t,value in zip(names,values):value.tofile(td/('tensor_%04d.bin'%t['id']))
inp=next(t for t in plan['tensors'] if t['name']=='input');x.tofile(td/('tensor_%04d.bin'%inp['id']))
print('TEACHER_READY',len(values),flush=True)
