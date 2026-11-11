#!/usr/bin/env python3
"""Desktop entry point: use start_gui.sh to select the installed Tk Python."""
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description='RISC-V 与 PE 工程助手')
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--native-root', type=Path)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--action', choices=('diagnose', 'run'), default='diagnose')
    parser.add_argument('--task')
    args = parser.parse_args()
    if args.worker:
        from contest_gui.native import worker
        worker(args.project, args.action, args.task)
    else:
        from contest_gui.ui import launch
        launch(args.project, args.native_root)


if __name__ == '__main__':
    main()
