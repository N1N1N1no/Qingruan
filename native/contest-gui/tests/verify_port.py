"""Exercise the real UI controller, compatibility runner and stop protocol."""
import json
from pathlib import Path
import sys
import threading
import time

BASE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE / 'native/contest-gui/tools'))
from contest_gui.native import NativeController

checks = {}
events = []
def emit(event):
    events.append(event)
    if event['type'] in {'started', 'finished'}:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    elif event['type'] == 'log':
        print(event.get('text', '').strip(), flush=True)

controller = NativeController(BASE / 'contest/task1/my_design', emit, BASE)
internal = '/home/zhz/contest/task1/my_design'
host = str((BASE / 'contest/task1/my_design').resolve())
assert controller._host_paths(internal) == host
assert controller._host_paths(host) == host
assert controller._host_paths(controller._host_paths({'path': internal})) == {'path': host}
checks['paths'] = 'PASS'
checks['diagnosis'] = controller.diagnose()
assert checks['diagnosis']['status'] == 'PASS', checks['diagnosis']
print('DIAGNOSIS_PASS', flush=True)
missing = {task.id: controller.task_unavailable(task) for task in controller.tasks
           if controller.task_unavailable(task)}
checks['unavailable_tasks'] = missing
assert 'pe_compile' in missing and 'soc_sdf' in missing
assert not controller.task_unavailable(controller._by_id['rtl_run'])

def run(task, cancel=False):
    events.clear()
    assert controller.start(task)
    assert not controller.start(task), 'Concurrent task should be rejected'
    if cancel:
        deadline = time.monotonic() + 40
        while not any(e['type'] == 'started' for e in events):
            assert controller.is_running and time.monotonic() < deadline
            time.sleep(.1)
        time.sleep(2)
        controller.stop()
    controller._thread.join(timeout=600)
    assert not controller.is_running, 'Worker did not stop'
    finishes = [e for e in events if e['type'] == 'finished']
    assert len(finishes) == 1, finishes
    result = finishes[0]
    assert result['status'] == ('CANCELLED' if cancel else 'PASS'), result
    for key in ['path', 'gui_run_path']:
        assert Path(result[key]).is_dir(), result
    checks[task + ('_cancel' if cancel else '')] = result

try:
    run('prepare')
    run('software')
    run('generic_check', cancel=True)
    run('rtl_run')
    checks['history_count'] = len(controller.scan_results())
    checks['status'] = 'PASS'
finally:
    target = BASE / 'native/validation/gui-port.json'
    target.write_text(json.dumps(checks, ensure_ascii=False, indent=2) + '\n')
    print('VALIDATION=' + str(target), flush=True)
