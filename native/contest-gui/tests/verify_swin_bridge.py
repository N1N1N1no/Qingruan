"""Exercise one Swin job through the actual desktop-to-runtime worker bridge."""
from pathlib import Path
import json,queue,sys
BASE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(BASE/'native/contest-gui/tools'))
from contest_gui.native import NativeController
cfg=json.loads((BASE/'native/contest-gui/config.json').read_text());events=queue.Queue()
c=NativeController(BASE/cfg['project'],events.put,BASE)
assert c.start('swin_analyze');c._thread.join(120);assert not c.is_running
finished=[]
while not events.empty():
 e=events.get()
 if e['type']=='finished':finished.append(e)
assert len(finished)==1 and finished[0]['status']=='PASS',finished
p=Path(finished[0]['path']);assert p.is_relative_to(BASE/'native/swin-tiny/work')
assert json.loads((p/'summary.json').read_text())['MAC_nodes']==77
(BASE/'native/validation/swin-gui-bridge.json').write_text(json.dumps({'status':'PASS','finished':finished[0]},ensure_ascii=False,indent=2)+'\n')
print('SWIN_GUI_BRIDGE_PASS')
