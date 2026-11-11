#!/usr/bin/python3
from pathlib import Path
import json,os,secrets,subprocess,sys,tempfile,time,traceback
BASE=Path(__file__).resolve().parents[3]
runtime=BASE/'native/gui-tools/runtime'
temp=tempfile.TemporaryDirectory(prefix='qingruan-new-gui-')
display=next(n for n in range(110,130) if not Path(f'/tmp/.X11-unix/X{n}').exists())
auth=Path(temp.name)/'auth';auth.touch(mode=0o600)
subprocess.run(['/usr/bin/xauth','-f',str(auth),'add',f':{display}','.',secrets.token_hex(16)],check=True)
env=os.environ.copy();env['LD_LIBRARY_PATH']=str(runtime/'usr/lib/x86_64-linux-gnu')
x=subprocess.Popen([str(runtime/'usr/bin/Xvfb'),f':{display}','-screen','0','1440x1000x24','-nolisten','tcp','-auth',str(auth),'-noreset'],env=env)
os.environ.update(DISPLAY=f':{display}',XAUTHORITY=str(auth));os.environ.pop('WAYLAND_DISPLAY',None)
for _ in range(50):
 if Path(f'/tmp/.X11-unix/X{display}').exists():break
 assert x.poll() is None;time.sleep(.1)
sys.path.insert(0,str(BASE/'native/contest-gui/tools'))
from contest_gui.ui import ContestApp
from PIL import ImageGrab
cfg=json.loads((BASE/'native/contest-gui/config.json').read_text())
app=ContestApp(BASE/cfg['project'],BASE)
errors=[]
app.report_callback_exception=lambda exc,v,tb:errors.append(''.join(traceback.format_exception(exc,v,tb)))
def finish():
 try:
  assert app.controller.design==(BASE/cfg['project']).resolve()
  assert {'swin_analyze','swin_verify','swin_rtl','conv2_full'} <= app.tasks.keys()
  assert app._ui_family=='Noto Sans CJK SC',app._ui_family
  latest=json.loads((BASE/'native/swin-tiny/work/latest_verify.json').read_text())
  case=Path(latest['path'].replace('/workspace/',str(BASE)+'/'))
  app._set_result(case,json.loads((case/'summary.json').read_text()))
  assert '精度未通过' in app.result_status['text']
  app.task_tree.selection_set('task:swin_verify');app.task_tree.focus('task:swin_verify');app._on_task_selected()
  assert str(app.run_button['state'])=='normal'
  assert 'C 调度器' in app._task_title.get()
  app.notebook.select(1)
  app.update();ImageGrab.grab(xdisplay=os.environ['DISPLAY']).save(BASE/'native/validation/完整新工程_Swin界面.png')
  assert not errors,errors
  (BASE/'native/validation/new-gui-window.json').write_text(json.dumps({'status':'PASS','project':str(app.controller.design),'tasks':len(app.tasks),'title':app.title(),'errors':errors},ensure_ascii=False,indent=2))
 except Exception:errors.append(traceback.format_exc())
 finally:app._on_close()
app.after(1500,finish)
try:app.mainloop()
finally:x.terminate();x.wait(timeout=10);temp.cleanup()
if errors:raise RuntimeError('\n'.join(errors))
print('NEW_GUI_WINDOW_PASS')
