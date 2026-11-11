from pathlib import Path
import json
import queue
import sys

BASE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(BASE/'native/contest-gui/tools'))
from contest_gui.native import NativeController
config=json.loads((BASE/'native/contest-gui/config.json').read_text())
project=BASE/config['project']
events=queue.Queue()
c=NativeController(project,events.put,BASE)
assert c.design==project.resolve()
assert not any(c.task_unavailable(t) for t in c.tasks)
assert {'soc_sdf','conv2_full','pe_recompile'} <= c._by_id.keys()
diag=c.diagnose()
assert diag['status']=='PASS',diag
assert 'runtime/bin/dc_shell' in diag['tools']['综合工具 DC']
assert c.start('prepare')
c._thread.join(90)
assert not c.is_running
found=[]
while not events.empty():
 e=events.get()
 if e['type']=='finished':found.append(e)
assert len(found)==1 and found[0]['status']=='PASS',found
assert Path(found[0]['path']).is_relative_to(project.parent/'work')
record={'status':'PASS','project':str(project),'task_count':len(c.tasks),'diagnosis':diag,'prepare':found[0]}
(BASE/'native/validation/new-gui-controller.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print('NEW_PROJECT_CONTROLLER_PASS')
