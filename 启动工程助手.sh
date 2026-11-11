#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH TK_LIBRARY TCL_LIBRARY
PROJECT="$(/usr/bin/python3 -c 'import json,sys; from pathlib import Path; b=Path(sys.argv[1]); print(b/json.loads((b/"native/contest-gui/config.json").read_text())["project"])' "$BASE")"
LOG="$PROJECT/../work/gui_runs/launcher.log"
mkdir -p "$(dirname -- "$LOG")"
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    echo '请在本机 Ubuntu 图形桌面启动工程助手。' >&2
    exit 2
fi
# Use Ubuntu's Tk, independently of Conda and the old VM's Python 3.8 libraries.
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH TK_LIBRARY TCL_LIBRARY
exec /usr/bin/python3 -B "$BASE/native/contest-gui/tools/run_contest_gui.py" \
    --project "$PROJECT" --native-root "$BASE" "$@" >>"$LOG" 2>&1
