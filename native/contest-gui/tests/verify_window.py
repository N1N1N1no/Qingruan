from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback

BASE = Path(__file__).resolve().parents[3]
display_process = None
temporary = None
if '--headless' in sys.argv:
    temporary = tempfile.TemporaryDirectory(prefix='qingruan-gui-test-')
    display = next(n for n in range(105, 120) if not Path(f'/tmp/.X11-unix/X{n}').exists())
    auth = Path(temporary.name) / 'auth'
    auth.touch(mode=0o600)
    import secrets
    subprocess.run(['/usr/bin/xauth', '-f', str(auth), 'add', f':{display}', '.', secrets.token_hex(16)], check=True)
    runtime = BASE / 'native/gui-tools/runtime'
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = str(runtime / 'usr/lib/x86_64-linux-gnu')
    display_process = subprocess.Popen([str(runtime / 'usr/bin/Xvfb'), f':{display}',
        '-screen', '0', '1280x960x24', '-nolisten', 'tcp', '-auth', str(auth), '-noreset'], env=env)
    os.environ.update(DISPLAY=f':{display}', XAUTHORITY=str(auth))
    os.environ.pop('WAYLAND_DISPLAY', None)
    for _ in range(50):
        if Path(f'/tmp/.X11-unix/X{display}').exists():
            break
        assert display_process.poll() is None
        time.sleep(.1)
sys.path.insert(0, str(BASE / 'native/contest-gui/tools'))
from contest_gui.ui import ContestApp
from PIL import ImageGrab

app = ContestApp(BASE / 'contest/task1/my_design', BASE)
errors = []
def callback_error(exc, value, tb):
    errors.append(''.join(traceback.format_exception(exc, value, tb)))
app.report_callback_exception = callback_error

def check():
    try:
        assert {'prepare', 'pe_read', 'rtl_run', 'wave'} <= app.tasks.keys()
        app.task_tree.selection_set('task:pe_read')
        app.task_tree.focus('task:pe_read')
        app._on_task_selected()
        assert str(app.run_button['state']) == 'disabled'
        assert '缺少' in app.detail_text.get('1.0', 'end')
        app.task_tree.selection_set('task:prepare')
        app.task_tree.focus('task:prepare')
        app._on_task_selected()
        assert str(app.run_button['state']) == 'normal'
        assert app._ui_family != 'TkDefaultFont', app._ui_family
        app._diagnose()
    except Exception:
        errors.append(traceback.format_exc())
    app.after(3000, finish)

def finish():
    try:
        assert not errors, errors
        assert 'status：PASS' in app.summary_text.get('1.0', 'end'), app.summary_text.get('1.0', 'end')[:1000]
        app.notebook.select(0)
        app.update()
        if '--headless' in sys.argv:
            ImageGrab.grab(xdisplay=os.environ['DISPLAY']).save(BASE / 'native/validation/本机工程助手验收.png')
        (BASE / 'native/validation/gui-window.json').write_text(json.dumps({
            'status': 'PASS', 'tasks': len(app.tasks), 'font': app._ui_family,
            'tk': app.tk.call('info', 'patchlevel'), 'errors': errors}, ensure_ascii=False, indent=2))
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        app._on_close()

app.after(1000, check)
app.mainloop()
if display_process:
    display_process.terminate()
    display_process.wait(timeout=10)
    temporary.cleanup()
if errors:
    raise RuntimeError('\n'.join(errors))
print('WINDOW_PASS')
