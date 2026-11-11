"""Host desktop UI with engineering jobs in the existing native tool runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import select
import selectors
import signal
import subprocess
import sys
import time

from .backend import Controller
from .project_engine import project_controller, prepare_main_sources

PREFIX = 'CONTEST_GUI_EVENT '


def worker(project: Path, action: str, task_id: str | None) -> None:
    def emit(event):
        print(PREFIX + json.dumps(event, ensure_ascii=False), flush=True)

    controller = project_controller(project, emit)
    if action == 'diagnose':
        emit({'type': 'diagnosis', 'result': controller.diagnose()})
        return
    prepare_main_sources(controller, task_id, emit)
    if not controller.start(task_id):
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, lambda *_: controller.stop())
    signal.signal(signal.SIGINT, lambda *_: controller.stop())
    input_open = True
    while controller.is_running:
        if input_open and select.select([sys.stdin], [], [], 0.1)[0]:
            line = sys.stdin.readline()
            if not line or line.strip() == 'stop':
                controller.stop()
            if not line:
                input_open = False
        else:
            time.sleep(0.05)
    controller._thread.join()


class NativeController(Controller):
    def __init__(self, project_root, on_event, native_root):
        self.native_root = Path(native_root).resolve()
        self.rootfs = self.native_root / 'native/rootfs'
        super().__init__(project_root, on_event)
        if self.design.is_relative_to(self.rootfs):
            self.internal_design = '/' + self.design.relative_to(self.rootfs).as_posix()
        else:
            self.internal_design = '/workspace/' + self.design.relative_to(self.native_root).as_posix()
        selected = project_controller(self.design, on_event)
        self.tasks = selected.tasks
        self._by_id = {task.id: task for task in self.tasks}

    def _host_paths(self, value):
        if isinstance(value, dict):
            return {self._host_paths(k): self._host_paths(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._host_paths(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r'(?<![\w./-])/home/zhz(?=/|$)',
                           lambda _: str(self.rootfs / 'home/zhz'), value)
            return re.sub(r'(?<![\w./-])/workspace(?=/|$)', lambda _: str(self.native_root), value)
        return value

    def _command(self, action, task=None):
        argv = [str(self.native_root / 'run-native.sh')]
        # Preparing sources, compiling firmware and open-source checks need no license.
        if task is None or task.kind == 'report' or (task.kind == 'rtl' and task.target != 'rtl') or (
                task.id.startswith('swin_') and task.id != 'swin_rtl'):
            argv.append('--no-license')
        argv += ['/home/zhz/miniforge3/bin/python3', '-B',
                 '/workspace/native/contest-gui/tools/run_contest_gui.py',
                 '--worker', '--project', self.internal_design, '--action', action]
        if task:
            argv += ['--task', task.id]
        return argv

    def _host_environment(self):
        env = os.environ.copy()
        for key in ('PYTHONPATH', 'PYTHONHOME', 'LD_LIBRARY_PATH', 'TK_LIBRARY', 'TCL_LIBRARY', 'QINGRUAN_NATIVE'):
            env.pop(key, None)
        return env

    def diagnose(self):
        try:
            result = subprocess.run(self._command('diagnose'), env=self._host_environment(),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=35)
            for line in result.stdout.splitlines():
                if line.startswith(PREFIX):
                    diagnosis = self._host_paths(json.loads(line[len(PREFIX):])['result'])
                    diagnosis['missing_task_scripts'] = {
                        task.title: reason for task in self.tasks
                        if (reason := self.task_unavailable(task))}
                    diagnosis['message'] = '工具和本机库检测；缺失的扩展工程步骤已在左侧标注。商业工具运行时自动连接许可服务。'
                    return diagnosis
            raise RuntimeError(result.stdout[-4000:] or '工具检测未返回结果')
        except Exception as error:
            return {'status': 'FAIL', 'error': str(error)}

    def _worker(self, task):
        finished = None
        process = None
        try:
            reason = self.task_unavailable(task)
            if reason:
                raise RuntimeError(reason)
            if task.kind == 'wave':
                self._open_wave({})
                finished = {'type': 'finished', 'task_id': task.id, 'status': 'COMPLETED', 'returncode': 0,
                            'summary': {'status': 'COMPLETED', 'title': task.title}}
                return
            self._emit('log', text='正在加载本机兼容工具环境；商业工具会自动启动或复用原许可服务。\n')
            process = subprocess.Popen(self._command('run', task), env=self._host_environment(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True, bufsize=0)
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            buffer = b''
            cancelled_at = None

            def consume(raw):
                nonlocal finished
                line = raw.decode('utf-8', errors='replace')
                if line.startswith(PREFIX):
                    event = self._host_paths(json.loads(line[len(PREFIX):]))
                    if event.get('path'):
                        self.last_result = Path(event['path'])
                    if event['type'] == 'finished':
                        finished = event
                    else:
                        self._on_event(event)
                elif line:
                    self._emit('log', text=line + '\n')

            try:
                while selector.get_map() or process.poll() is None:
                    if self._stop.is_set():
                        if cancelled_at is None:
                            cancelled_at = time.monotonic()
                            try:
                                process.stdin.write(b'stop\n')
                                process.stdin.flush()
                            except (BrokenPipeError, OSError):
                                pass
                        if time.monotonic() - cancelled_at > 15 and process.poll() is None:
                            os.killpg(process.pid, signal.SIGTERM)
                    for key, _ in selector.select(timeout=0.2):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            buffer += chunk
                            while b'\n' in buffer:
                                line, buffer = buffer.split(b'\n', 1)
                                consume(line)
                if buffer:
                    consume(buffer)
                process.wait(timeout=10)
            finally:
                selector.close()
            if finished is None:
                raise RuntimeError('工程进程未返回完成记录（退出码 {}），请查看实时日志。'.format(process.returncode))
        except Exception as error:
            self._emit('log', text=str(error) + '\n')
            finished = {'type': 'finished', 'task_id': task.id,
                        'status': 'CANCELLED' if self._stop.is_set() else 'FAIL', 'returncode': 1,
                        'summary': {'status': 'CANCELLED' if self._stop.is_set() else 'FAIL', 'error': str(error)}}
        finally:
            if process:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
                process.stdin.close()
                process.stdout.close()
            with self._mutex:
                self._running = False
            if finished:
                self._on_event(finished)

    def scan_results(self):
        results = self._host_paths(super().scan_results())
        # A terminated upstream runner cannot always finalize its own summary.
        # Its GUI record still knows that the task was stopped by the user.
        cancelled = {path for item in results if item['status'] == 'CANCELLED'
                     for path in item['summary'].get('result_directories', [])}
        for item in results:
            if item['path'] in cancelled and item['status'] == 'RUNNING':
                item['status'] = 'CANCELLED'
                item['summary']['status'] = 'CANCELLED'
        return results

    def _open_wave(self, env):
        config_path = self.native_root / 'native/contest-gui/config.json'
        if config_path.is_file():
            config = json.loads(config_path.read_text())
            preferred = self.native_root / config.get('verified_wave', '')
            if preferred.is_file():
                self.open_path(preferred)
                self._emit('log', text='已启动已验证整机 SDF 波形：' + str(preferred) + '\n')
                return
        candidates = list((self.project / 'build').glob('*.vpd'))
        for result in self.work.glob('rtl.*'):
            candidates.extend(result.glob('*/*.vpd'))
            candidates.extend(result.glob('source/project/build/*.vpd'))
        if not candidates:
            candidates = list((self.task_root / 'reference_rtl').glob('*/*.vpd'))
        if not candidates:
            raise RuntimeError('尚无 VPD 波形，请先运行整机 RTL 检查。')
        waveform = max(candidates, key=lambda path: path.stat().st_mtime)
        self.open_path(waveform)
        self._emit('log', text='已启动 DVE：' + str(waveform) + '\n')

    def open_path(self, path):
        target = Path(self._host_paths(str(path))).expanduser().resolve()
        if not target.exists():
            raise FileNotFoundError('文件尚未生成：' + str(target))
        if target.suffix.lower() == '.vpd':
            command = [str(self.native_root / 'run-native.sh'), 'dve', '-full64', '-vpd', str(target)]
        else:
            command = ['/usr/bin/xdg-open', str(target)]
        with (self.runs / 'desktop-open.log').open('ab') as stream:
            subprocess.Popen(command, env=self._host_environment(), stdout=stream, stderr=stream,
                             start_new_session=True)
