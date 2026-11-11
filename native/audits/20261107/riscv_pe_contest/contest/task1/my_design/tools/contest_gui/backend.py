"""Run the existing engineering entry points without a terminal session."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import codecs
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict

from .catalog import TaskSpec, get_tasks

RunEvent = Dict[str, Any]


def _save(path: Path, value: dict) -> None:
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _tcl(value: str | Path) -> str:
    # A quoted Tcl word, including paths with spaces, brackets or dollar signs.
    text = str(value)
    for old, new in [('\\', '\\\\'), ('"', '\\"'), ('$', '\\$'), ('[', '\\['), (']', '\\]')]:
        text = text.replace(old, new)
    return '"' + text + '"'


def _processes() -> dict[int, tuple[int, str]]:
    """PID -> (parent PID, Linux start time), to avoid PID-reuse mistakes."""
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            result[int(entry.name)] = (int(fields[1]), fields[19])
        except (OSError, ValueError, IndexError):
            continue
    return result


class Controller:
    def __init__(self, project_root: Path | str, on_event: Callable[[RunEvent], None]):
        root = Path(project_root).resolve()
        self.design = root.parent if root.name == 'project' else root
        self.project = self.design / 'project'
        if not (self.project / 'Makefile').is_file():
            raise ValueError('所选目录不是本工程：缺少 project/Makefile')
        self.task_root = self.design.parent
        self.env_script = self.task_root.parent / 'env.sh'
        self.work = self.task_root / 'work'
        self.runs = self.work / 'gui_runs'
        self.runs.mkdir(parents=True, exist_ok=True)
        self.tasks = get_tasks(self.project)
        self._by_id = {task.id: task for task in self.tasks}
        self._on_event = on_event
        self._mutex = threading.Lock()
        self._running = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._env_cache: dict[str, str] | None = None
        self.last_result: Path | None = None

    @property
    def is_running(self) -> bool:
        with self._mutex:
            return self._running

    def _emit(self, kind: str, **fields: Any) -> None:
        self._on_event({'type': kind, **fields})

    def _environment(self) -> dict[str, str]:
        if self._env_cache is None:
            command = ['bash', '-c', 'source "$1" >&2 || exit; env -0', 'contest-gui', str(self.env_script)]
            inherited = os.environ.copy()
            for key in ('PYTHONPATH', 'PYTHONHOME', 'TK_LIBRARY', 'TCL_LIBRARY'):
                inherited.pop(key, None)
            private_runtimes = (str(Path.home() / '.local/share/riscv-pe-gui/runtime'),
                                str(self.task_root.parent / 'gui_runtime'))
            inherited['LD_LIBRARY_PATH'] = ':'.join(
                value for value in inherited.get('LD_LIBRARY_PATH', '').split(':')
                if value and not any(value == root or value.startswith(root + '/')
                                     for root in private_runtimes))
            result = subprocess.run(command, env=inherited, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            if result.returncode:
                raise RuntimeError('工具环境加载失败：' + result.stderr.decode(errors='replace')[-2000:])
            env = {}
            for entry in result.stdout.split(b'\0'):
                if b'=' in entry:
                    key, value = entry.split(b'=', 1)
                    env[os.fsdecode(key)] = os.fsdecode(value)
            for name in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKEFILES', 'PYTHONPATH', 'PYTHONHOME'):
                env.pop(name, None)
            for name in list(env):
                if name in {'PE_SDF_DDC', 'PE_SDF_LIBRARY_DIR', 'PE_SDF_OUTPUT_DIR'} or (
                    name.endswith('_CASE') and name.startswith(('SOC_', 'PE_'))):
                    env.pop(name, None)
            env.update(PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', LC_ALL='C.UTF-8')
            self._env_cache = env
        return self._env_cache.copy()

    def diagnose(self) -> dict:
        try:
            env = self._environment()
            names = {'综合工具 DC': 'dc_shell', '仿真工具 VCS': 'vcs', '波形查看器 DVE': 'dve',
                     '构建工具 make': 'make', 'C 编译器 GCC': env.get('RISCV_PREFIX', 'riscv-none-elf-') + 'gcc',
                     '转换工具 sv2v': 'sv2v', '通用综合 Yosys': 'yosys'}
            items = {label: shutil.which(command, path=env.get('PATH')) for label, command in names.items()}
            libraries = {name: (self.project / 'build/dc_libraries' / name).is_file()
                         for name in ('standard.db', 'sram.db')}
            return {'status': 'PASS' if all(items.values()) and all(libraries.values()) else 'FAIL',
                    'tools': items, 'libraries': libraries, 'environment': str(self.env_script),
                    'python': sys.executable, 'message': '仅检查工具和库是否可找到；运行时仍需有效许可证。'}
        except Exception as error:
            return {'status': 'FAIL', 'error': str(error)}

    def start(self, task_id: str) -> bool:
        if task_id not in self._by_id:
            self._emit('log', text='没有找到这项任务。\n')
            return False
        with self._mutex:
            if self._running:
                self._emit('log', text='已有任务运行中，请等待结束，或先点击“停止”。\n')
                return False
            self._running = True
            self._stop.clear()
        self._thread = threading.Thread(target=self._guarded_worker, args=(self._by_id[task_id],), daemon=False)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self.is_running:
            self._stop.set()
            self._emit('state', text='正在停止本界面启动的任务并清理其子进程……')

    def close(self) -> None:
        self.stop()

    def _guarded_worker(self, task: TaskSpec) -> None:
        try:
            self._worker(task)
        except BaseException as error:
            # Even a full disk during initial log creation must release the UI.
            with self._mutex:
                self._running = False
            self._emit('log', text='任务无法继续：' + str(error) + '\n')
            self._emit('finished', task_id=task.id, status='FAIL', returncode=1,
                       summary={'status': 'FAIL', 'error': str(error), 'title': task.title})

    def _prerequisites(self, task: TaskSpec) -> None:
        missing = [name for name in task.required_files if not (self.project / name).is_file()]
        if missing:
            steps = ' → '.join(self._by_id[name].title for name in task.prerequisites if name in self._by_id)
            raise RuntimeError('缺少前一步生成的文件：\n' + '\n'.join(missing) +
                               '\n请先执行：' + (steps or '任务说明中的前置步骤') + '。')

    def _dc_script(self, task: TaskSpec, run: Path) -> Path:
        library = self.project / 'build/dc_libraries'
        lines = ['proc gui_run {} {', 'set_host_options -max_cores 1']
        if task.target:
            ddc = self.project / task.target
            if not ddc.is_file():
                raise RuntimeError('找不到保存的设计，请先完成前置步骤：' + str(ddc))
            top = ('pe_1sram_4mul' if 'dc_pe/' in task.target or 'dc_pe_io/' in task.target else
                   'pe_array' if 'dc_pe_array' in task.target else 'sram_multiplier_system_ID_WIDTH3')
            lines += [f'set std_db {_tcl(library / "standard.db")}',
                      f'set sram_db {_tcl(library / "sram.db")}', f'set ddc {_tcl(ddc)}',
                      'set_app_var target_library [list $std_db]',
                      'set_app_var link_library [list * $std_db $sram_db gtech.db]',
                      'read_db $std_db', 'read_db $sram_db', 'set_app_var auto_link_disable true',
                      'set loaded [read_ddc $ddc]',
                      'set_app_var auto_link_disable true',
                      'set_app_var target_library [list $std_db]',
                      'set_app_var link_library [list * $std_db $sram_db gtech.db]',
                      'set imported ""', 'foreach name $loaded {',
                      'set obj [get_designs -quiet -exact "${ddc}:$name"]',
                      'if {[sizeof_collection $obj] != 1} {error "Cannot resolve saved design identity"}',
                      'set imported [add_to_collection $imported $obj]',
                      'if {[get_attribute -quiet $obj local_link_library] ne ""} {remove_attribute $obj local_link_library}',
                      '}', f'set top [get_designs -quiet -exact "${{ddc}}:{top}"]',
                      'if {[sizeof_collection $top] != 1} {error "Cannot resolve saved top design"}',
                      'current_design $top', 'set_app_var auto_link_disable false',
                      'if {![link]} {error "Cannot link saved design"}',
                      'if {[compare_collections [add_to_collection [get_designs -hierarchical *] $top] $imported] != 0} {error "Saved hierarchy identity differs"}']
        lines += [f'source {_tcl(self.project / step)}' for step in task.steps]
        lines += ['puts "GUI_DC_TASK_COMPLETED"', 'flush stdout', '}',
                  'if {[catch {gui_run} gui_error]} {', 'puts stderr "GUI_DC_TASK_FAILED: $gui_error"',
                  'if {[info exists ::errorInfo]} {puts stderr $::errorInfo}', 'exit 1', '}', 'exit 0']
        path = run / 'commands.tcl'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def _build_command(self, task: TaskSpec, run: Path, env: dict) -> tuple[list[str], Path]:
        python = env.get('TASK1_PYTHON', sys.executable)
        if task.kind == 'shell':
            return ['bash', str(self.project / task.target)], run
        if task.kind == 'rtl':
            command = ['bash', str(self.task_root / 'run_rtl.sh'), '--source', str(self.design),
                       '--stage', task.target]
            if task.steps:
                command += ['--case', task.steps[0]]
            return command, run
        if task.kind == 'dc':
            script = self._dc_script(task, run)
            # Only this new DC is capped. Existing runners manage their own caps.
            return ['bash', '-c', 'ulimit -v 6291456; exec dc_shell -f "$1"',
                    'contest-gui-dc', str(script)], run
        if task.kind == 'export_soc':
            source = None
            candidates = sorted(self.work.glob('soc_compile.*/summary.json'),
                                key=lambda p: p.stat().st_mtime, reverse=True)
            for p in candidates:
                data = _json(p)
                if data.get('status') != 'PASS':
                    continue
                try:
                    hashes = {**data['source_dependencies_sha256'], **data['input_sha256'],
                              **{str(p.parent / n): d for n, d in data['output_sha256'].items()},
                              str(p.parent / 'expected.tcl'): data['expected_tcl_sha256']}
                    if all(Path(name).is_file() and _sha(Path(name)) == digest for name, digest in hashes.items()):
                        source = p.parent
                        break
                except (OSError, KeyError):
                    continue
            if source is None:
                raise RuntimeError('没有与当前上游文件匹配的成功整机综合结果。请按流程重新生成约束和综合结果。')
            output = Path(tempfile.mkdtemp(prefix='soc_sdf_export.', dir=self.work))
            return [python, str(self.project / task.target), '--source', str(source), '--output', str(output)], run
        if task.kind == 'make':
            # Run the original RAM unit test with this VM's VCS compatibility
            # adapter. Nothing is added to the production RTL or Makefile.
            if task.target != 'ram-test':
                raise RuntimeError('未配置的基础测试入口：' + task.target)
            adapter = run / 'vcs'
            source = self.design / 'compat/pthread_yield_compat.c'
            obj = run / 'pthread_yield_compat.o'
            shell = '#!/usr/bin/env bash\nset -euo pipefail\n'
            shell += 'gcc -fPIC -c ' + shlex.quote(str(source)) + ' -o ' + shlex.quote(str(obj)) + '\n'
            shell += 'exec ' + shlex.quote(python) + ' ' + shlex.quote(str(self.task_root / 'scripts/vcs_vm.py'))
            shell += ' "$@" -LDFLAGS ' + shlex.quote(str(obj)) + '\n'
            adapter.write_text(shell, encoding='utf-8')
            adapter.chmod(0o755)
            return ['make', 'ram-test', 'VCS=' + str(adapter)], self.project
        raise RuntimeError('尚未实现的任务类型：' + task.kind)

    def _run_process(self, argv: list[str], cwd: Path, env: dict, task: TaskSpec,
                     run: Path, record: dict) -> tuple[int, str, list[Path]]:
        self._emit('log', text='正在执行：' + task.title + '\n')
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
        record['pid'] = process.pid
        record['argv'] = argv
        record['cwd'] = str(cwd)
        first_table = _processes()
        root_birth = first_table.get(process.pid, (0, ''))[1]
        try:
            _save(run / 'summary.json', record)
        except BaseException:
            # The tool has just been spawned: do not leave it running if its
            # PID/log metadata cannot be recorded (for example, disk full).
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            token = env.get('CONTEST_GUI_RUN_TOKEN', '')
            for pid, (_, birth) in _processes().items():
                entry = Path('/proc') / str(pid)
                try:
                    needle = os.fsencode('CONTEST_GUI_RUN_TOKEN=' + token)
                    if token and entry.stat().st_uid == os.getuid() and needle in (entry / 'environ').read_bytes().split(b'\0'):
                        if _processes().get(pid, (0, ''))[1] == birth and pid not in {os.getpid(), 45599}:
                            os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
            process.wait(timeout=10)
            raise
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        owned: dict[int, str] = {process.pid: root_birth} if root_birth else {}
        buffer, tail, paths = '', '', []
        started = time.monotonic()
        cancelled_at: float | None = None
        last_progress = started
        last_scan = 0.0
        timed_out = False

        def track() -> None:
            table = _processes()
            parents = set()
            if root_birth and table.get(process.pid, (0, ''))[1] == root_birth:
                parents.add(process.pid)
            # Retain all previously observed descendants, including new sessions.
            parents.update(pid for pid, birth in owned.items() if table.get(pid, (0, ''))[1] == birth)
            while True:
                additions = {pid for pid, (parent, _) in table.items() if parent in parents} - parents
                if not additions:
                    break
                parents.update(additions)
            for pid in parents:
                if pid in table:
                    owned[pid] = table[pid][1]
            # Some tool launchers create a new session and exit immediately.
            # A private inherited token also identifies those orphaned children.
            token = env.get('CONTEST_GUI_RUN_TOKEN')
            if token:
                needle = os.fsencode('CONTEST_GUI_RUN_TOKEN=' + token)
                for pid in table:
                    entry = Path('/proc') / str(pid)
                    try:
                        if entry.stat().st_uid == os.getuid() and needle in (entry / 'environ').read_bytes().split(b'\0'):
                            owned[pid] = table[pid][1]
                    except (OSError, ProcessLookupError):
                        continue

        def terminate_owned(sig: signal.Signals) -> None:
            table = _processes()
            # Children first, then the root; never act on a reused PID.
            for pid in sorted(owned, key=lambda p: p == process.pid):
                if pid in {os.getpid(), 45599}:
                    continue
                if table.get(pid, (0, ''))[1] == owned[pid]:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass

        def consume(text: str, stream) -> None:
            nonlocal buffer, tail
            stream.write(text)
            stream.flush()
            tail = (tail + text)[-2_000_000:]
            self._emit('log', text=text)
            buffer += text
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                if re.search(r'^\s*(?:Error(?:-\[|:)|Fatal:|FATAL:|GUI_DC_TASK_FAILED)', line):
                    record['tool_error_in_log'] = True
                if task.marker and task.marker in line:
                    record['completion_marker_seen'] = True
                if line.startswith('RESULT='):
                    path = Path(line[7:].strip()).resolve()
                    if path.is_dir() and path not in paths:
                        paths.append(path)
                        self.last_result = path
                        self._emit('result', path=str(path), task_id=task.id, status='RUNNING')

        try:
            with (run / 'run.log').open('w', encoding='utf-8') as stream:
                while selector.get_map() or process.poll() is None:
                    now = time.monotonic()
                    if now-last_scan >= 0.3:
                        track()
                        last_scan = now
                    if now-started > task.timeout and not self._stop.is_set():
                        timed_out = True
                        self._stop.set()
                        self._emit('log', text='超过本步骤的时间上限，正在停止。\n')
                    if self._stop.is_set():
                        if cancelled_at is None:
                            cancelled_at = now
                            # Existing runners catch TERM and clean their own EDA child.
                            try:
                                process.send_signal(signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                        elif now-cancelled_at > 6:
                            terminate_owned(signal.SIGKILL)
                    for key, _ in selector.select(timeout=0.2):
                        chunk = os.read(key.fd, 65536)
                        if chunk:
                            consume(decoder.decode(chunk), stream)
                        else:
                            selector.unregister(key.fileobj)
                    if now-last_progress >= 15:
                        self._emit('state', text=f'{task.title} · 已运行 {int(now-started)} 秒')
                        last_progress = now
                    if cancelled_at is not None and process.poll() is not None and now-cancelled_at > 8:
                        break
                final = decoder.decode(b'', final=True)
                if final:
                    consume(final, stream)
                if task.marker and task.marker in buffer:
                    record['completion_marker_seen'] = True
                if re.search(r'^\s*(?:Error(?:-\[|:)|Fatal:|FATAL:|GUI_DC_TASK_FAILED)', buffer):
                    record['tool_error_in_log'] = True
            code = process.wait(timeout=10)
        finally:
            selector.close()
            if process.stdout:
                process.stdout.close()
            if self._stop.is_set() or process.poll() is None:
                track()
                terminate_owned(signal.SIGKILL)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        record['timed_out'] = timed_out
        return code, tail, paths

    def _worker(self, task: TaskSpec) -> None:
        run = Path(tempfile.mkdtemp(prefix=task.id + '.', dir=self.runs))
        record = {'status': 'RUNNING', 'task_id': task.id, 'title': task.title,
                  'scope': task.description, 'started_at': datetime.now().isoformat(timespec='seconds'),
                  'result_directories': []}
        _save(run / 'summary.json', record)
        self.last_result = run
        self._emit('started', task_id=task.id, path=str(run), text=task.title)
        lock = (self.runs / 'task.lock').open('a+')
        code, status = 1, 'FAIL'
        started = time.monotonic()
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('另一个工程界面正在执行任务，请等它结束。')
            self._prerequisites(task)
            env = self._environment()
            env['CONTEST_GUI_RUN_TOKEN'] = uuid.uuid4().hex
            if self._stop.is_set():
                status = 'CANCELLED'
            elif task.kind == 'report':
                self._view_report(task, record)
                code, status = 0, 'COMPLETED'
            elif task.kind == 'wave':
                self._open_wave(env)
                code, status = 0, 'COMPLETED'
            else:
                argv, cwd = self._build_command(task, run, env)
                code, log, paths = self._run_process(argv, cwd, env, task, run, record)
                record['result_directories'] = [str(p) for p in paths]
                if self._stop.is_set():
                    status = 'FAIL' if record.get('timed_out') else 'CANCELLED'
                elif code:
                    status = 'FAIL'
                elif record.get('tool_error_in_log'):
                    status = 'FAIL'
                    record['error'] = '工具日志包含错误，请查看实时日志或打开结果目录。'
                elif task.marker and not record.get('completion_marker_seen'):
                    status = 'FAIL'
                    record['error'] = '命令已退出，但没有找到本步骤要求的完成标记。'
                else:
                    status = 'COMPLETED' if task.kind == 'dc' else 'PASS'
                    if task.kind == 'dc':
                        record['validation_scope'] = '当前读入、约束、综合或导出步骤执行完成；时序是否满足要求以实际报告为准。'
                for path in paths:
                    result = _json(path / 'summary.json')
                    if result.get('status') not in {None, 'PASS', 'SUCCESS', 'COMPLETED', 'DONE', 'EXPORTED'}:
                        status = 'FAIL'
                        record['error'] = result.get('error', '结果汇总标记为未通过。')
                    if result:
                        self._emit('result', path=str(path), task_id=task.id,
                                   status=result.get('status', status), summary=result)
                if task.kind == 'dc':
                    record['outputs'] = [str(self.project / name) for name in task.outputs]
                if status == 'FAIL' and not record.get('error'):
                    record['error'] = log[-5000:]
        except Exception as error:
            record['error'] = str(error)
            self._emit('log', text='\n未完成：' + str(error) + '\n')
            status = 'CANCELLED' if self._stop.is_set() else 'FAIL'
        finally:
            record.update(status=status, returncode=code, elapsed_s=round(time.monotonic()-started, 2),
                          finished_at=datetime.now().isoformat(timespec='seconds'))
            _save(run / 'summary.json', record)
            lock.close()
            with self._mutex:
                self._running = False
            display_path, display_summary = run, record
            for name in reversed(record.get('result_directories', [])):
                candidate = Path(name)
                if candidate.is_dir():
                    display_path = candidate
                    display_summary = _json(candidate / 'summary.json') or record
                    break
            if 'report_path' in record:
                display_path = Path(record['report_path']).parent
            elif display_path == run and task.kind == 'dc' and task.outputs:
                candidate = self.project / task.outputs[0]
                if candidate.exists():
                    display_path = candidate if candidate.is_dir() else candidate.parent
            self.last_result = display_path
            self._emit('finished', task_id=task.id, path=str(display_path), gui_run_path=str(run), status=status,
                       returncode=code if status in {'PASS', 'COMPLETED'} else (code or 1), summary=display_summary)

    def scan_results(self) -> list[dict]:
        paths = list(self.work.glob('*/summary.json')) + list(self.runs.glob('*/summary.json'))
        results = []
        for path in paths:
            data = _json(path)
            if not data:
                continue
            mtime = path.stat().st_mtime
            results.append({'path': str(path.parent), 'status': data.get('status', 'UNKNOWN'),
                'title': data.get('title', path.parent.name), 'task_id': data.get('task_id', ''),
                'time': datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M:%S'),
                'mtime': mtime, 'summary': data})
        return sorted(results, key=lambda x: x['mtime'], reverse=True)

    def _view_report(self, task: TaskSpec, record: dict) -> None:
        aliases = {'area': ['area.rpt'], 'setup': ['setup.rpt', 'timing.rpt', 'setup_initial.rpt'],
                   'hold': ['hold.rpt', 'hold_initial.rpt'], 'violations': ['violations.rpt', 'violations_initial.rpt'],
                   'ports': ['ports.rpt'], 'constraints': ['constraints_check.rpt', 'check_timing.rpt'],
                   'design': ['check_design.rpt'], 'units': ['units.rpt'], 'clocks': ['clocks.rpt']}
        names = aliases.get(task.target, [task.target])
        candidates = []
        for result in self.work.glob('soc_*'):
            if result.is_dir():
                for name in names:
                    path = result / 'dc' / name
                    if path.is_file():
                        candidates.append(path)
        for directory in (self.project / 'build').glob('dc_*'):
            for name in names:
                path = directory / name
                if path.is_file():
                    candidates.append(path)
        if not candidates:
            raise RuntimeError('还没有这个报告，请先运行相应的综合或约束步骤。')
        path = max(candidates, key=lambda p: p.stat().st_mtime)
        text = path.read_text(encoding='utf-8', errors='replace')
        record['report_path'] = str(path)
        self._emit('log', text='报告位置：' + str(path) + '\n\n' + text)
        self._emit('result', path=str(path.parent), status='COMPLETED',
                   summary={'scope': task.title, 'report_path': str(path), 'report': text})

    def _open_wave(self, env: dict) -> None:
        candidates = list((self.project / 'build').glob('*.vpd'))
        for result in self.work.glob('rtl.*'):
            candidates.extend(result.glob('*/*.vpd'))
            candidates.extend(result.glob('source/project/build/*.vpd'))
        if not candidates:
            raise RuntimeError('还没有波形文件，请先运行“整机 RTL 仿真”。')
        waveform = max(candidates, key=lambda p: p.stat().st_mtime)
        executable = shutil.which('dve', path=env.get('PATH'))
        if not executable:
            raise RuntimeError('找不到 DVE，请先点击“检查工具环境”。')
        subprocess.Popen([executable, '-full64', '-vpd', str(waveform)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self._emit('log', text='已打开波形：' + str(waveform) + '\n')

    def open_path(self, path: str | Path) -> None:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            raise FileNotFoundError('文件尚未生成：' + str(target))
        subprocess.Popen(['xdg-open', str(target)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
