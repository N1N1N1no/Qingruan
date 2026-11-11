#!/usr/bin/env python3
"""Desktop entry point; report missing engineering files before creating a UI."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback


def resolve_design(project_root):
    root = Path(project_root).expanduser().resolve()
    design = root.parent if root.name == 'project' else root
    required = design / 'project/Makefile'
    if not required.is_file():
        raise ValueError('没有找到完整工程。\n当前目录：' + str(design)
                         + '\n缺少文件：' + str(required)
                         + '\n请解压“完整工程包”后从包内启动；只复制 tools 界面文件不能运行工程。')
    return design


def startup_log(design):
    design = design.parent if design.name == 'project' else design
    configured = os.environ.get('CONTEST_GUI_LOG')
    if configured:
        return Path(configured).expanduser()
    candidate = design.parent / 'work/gui_runs/launcher.log'
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        candidate = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'riscv-pe-gui/launcher.log'
        candidate.parent.mkdir(parents=True, exist_ok=True)
    os.environ['CONTEST_GUI_LOG'] = str(candidate)
    return candidate


def report_startup_error(error, log_path, show_dialog=True):
    details = traceback.format_exc()
    print(details, file=sys.stderr)
    if os.environ.get('CONTEST_GUI_STDERR_LOGGED') != '1':
        try:
            with log_path.open('a', encoding='utf-8') as stream:
                stream.write(details + '\n')
        except OSError:
            pass
    message = str(error) + '\n\n启动日志：' + str(log_path)
    if not show_dialog:
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('RISC-V 与 PE 工程助手无法启动', message, parent=root)
        root.destroy()
    except Exception:
        print('工程助手无法启动：' + message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='RISC-V 与 PE 工程助手')
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check', action='store_true', help='检查工程和步骤列表后退出，不打开窗口或运行工程')
    args = parser.parse_args()
    log_path = Path(os.environ.get('CONTEST_GUI_LOG', str(Path.home() / '.cache/riscv-pe-gui/launcher.log')))
    try:
        log_path = startup_log(args.project.expanduser().resolve())
        design = resolve_design(args.project)
        if args.check:
            from contest_gui.catalog import get_tasks
            tasks = get_tasks(design / 'project')
            identifiers = [task.id for task in tasks]
            if not tasks or len(identifiers) != len(set(identifiers)):
                raise ValueError('工程步骤列表为空或包含重复项，请重新解压完整工程包。')
            warning = ''
            if any(ord(character) > 127 or character.isspace() for character in str(design)):
                warning = '部分 RTL 工具要求英文且不含空格的路径，建议解压到 ~/riscv_pe_contest。'
            print(json.dumps({'status': 'PASS', 'project': str(design), 'tasks': len(tasks),
                              'groups': len({task.group for task in tasks}), 'warning': warning}, ensure_ascii=False))
        else:
            from contest_gui.ui import launch
            launch(design)
        return 0
    except Exception as error:
        report_startup_error(error, log_path, show_dialog=not args.check)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
