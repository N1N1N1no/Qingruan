"""Use the selected project's own task catalogue and execution backend."""
import hashlib
from dataclasses import replace
import importlib
import importlib.util
from pathlib import Path
import subprocess
import sys


def project_controller(project, on_event):
    project = Path(project).resolve()
    design = project.parent if project.name == 'project' else project
    source = design / 'tools/contest_gui'
    if not (source / 'backend.py').is_file():
        from .backend import Controller
        return Controller(design, on_event)
    name = '_qingruan_engine_' + hashlib.sha256(str(source).encode()).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, source / '__init__.py',
                                                      submodule_search_locations=[str(source)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    engine = importlib.import_module(name + '.backend').Controller(design, on_event)
    engine.tasks = [replace(t, group='旧 AircraftCNN 示例 · ' + t.group) if t.id.startswith('conv') else t
                    for t in engine.tasks]
    engine._by_id = {t.id:t for t in engine.tasks}
    base = Path(__file__).resolve().parents[4]
    overlay = base / 'native/swin-tiny/gui_tasks.py'
    if overlay.is_file():
        spec = importlib.util.spec_from_file_location('_qingruan_swin_tasks', overlay)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        TaskSpec = importlib.import_module(name + '.catalog').TaskSpec
        additions = module.get_tasks(design / 'project', base, TaskSpec)
        engine.tasks.extend(additions)
        engine._by_id.update({task.id: task for task in additions})
    return engine


def prepare_main_sources(engine, task_id, emit):
    if task_id != 'soc_read' or (engine.project / 'build/synth.f').is_file():
        return
    env = engine._environment()
    argv = [env['TASK1_PYTHON'], str(engine.project / 'scripts/prepare.py'),
            '--rv-root', str(engine.design / 'vendor/Cores-VeeR-EL2'), '--skip-config']
    result = subprocess.run(argv, cwd=engine.project, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    emit({'type': 'log', 'text': '准备主工程源清单：' + result.stdout})
    if result.returncode:
        raise RuntimeError('主工程源清单准备失败：' + result.stdout[-2000:])
