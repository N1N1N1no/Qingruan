#!/usr/bin/env bash
set -euo pipefail
contest_gui_tools=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
contest_gui_design=$(cd -- "$contest_gui_tools/.." && pwd)
contest_gui_contest=$(cd -- "$contest_gui_design/../.." && pwd)
contest_gui_work="$contest_gui_design/../work/gui_runs"
if ! mkdir -p "$contest_gui_work"; then
    contest_gui_work="${XDG_CACHE_HOME:-$HOME/.cache}/riscv-pe-gui"
    mkdir -p "$contest_gui_work"
fi
export CONTEST_GUI_LOG="$contest_gui_work/launcher.log"
export CONTEST_GUI_STDERR_LOGGED=1
export PYTHONDONTWRITEBYTECODE=1
contest_gui_python=''
contest_gui_mode='native'
contest_gui_runtime=''
contest_gui_fallback=''
contest_gui_fallback_mode='native'
contest_gui_fallback_runtime=''
contest_gui_previous_ld=${LD_LIBRARY_PATH-}
# Prefer a working native Tk which can see Chinese fonts. Some older Conda
# builds import successfully but cannot discover the system's CJK fonts.
contest_gui_probe='import os, sys, tkinter as tk
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    sys.exit(0)
from tkinter import font
root = tk.Tk(); root.withdraw()
families = set(font.families(root)); root.destroy()
known = {"Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Serif CJK SC", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Microsoft YaHei", "SimSun"}
sys.exit(0 if families.intersection(known) else 2)'
contest_gui_candidates=("${CONTEST_GUI_PYTHON-}" /usr/bin/python3 "$HOME/miniforge3/bin/python3" "$HOME/miniconda3/bin/python3" python3)
for contest_gui_candidate in "${contest_gui_candidates[@]}"; do
    [[ -n "$contest_gui_candidate" ]] || continue
    if ! command -v "$contest_gui_candidate" >/dev/null 2>&1; then continue; fi
    if env -u PYTHONPATH -u PYTHONHOME -u TK_LIBRARY -u TCL_LIBRARY "$contest_gui_candidate" -c "$contest_gui_probe" >/dev/null 2>&1; then
        contest_gui_python="$contest_gui_candidate"
        break
    else
        contest_gui_probe_status=$?
        if [[ "$contest_gui_probe_status" == 2 && -z "$contest_gui_fallback" ]]; then
            contest_gui_fallback="$contest_gui_candidate"
        fi
    fi
done
# The optional component is used only with the exact matching Python ABI.
# Nothing is copied into /usr or the recipient's Python installation.
if [[ -z "$contest_gui_python" ]]; then
    contest_gui_runtimes=("$contest_gui_contest/gui_runtime" "$HOME/.local/share/riscv-pe-gui/runtime")
    for contest_gui_runtime_candidate in "${contest_gui_runtimes[@]}"; do
        for contest_gui_candidate in "${contest_gui_candidates[@]}"; do
            [[ -n "$contest_gui_candidate" ]] || continue
            if ! command -v "$contest_gui_candidate" >/dev/null 2>&1; then continue; fi
            contest_gui_version=$(env -u PYTHONPATH -u PYTHONHOME "$contest_gui_candidate" -c 'import sys; print("%s.%s" % sys.version_info[:2])' 2>/dev/null) || continue
            contest_gui_python_lib="$contest_gui_runtime_candidate/usr/lib/python$contest_gui_version"
            [[ -f "$contest_gui_python_lib/tkinter/__init__.py" ]] || continue
            if env -u PYTHONHOME -u TCL_LIBRARY PYTHONPATH="$contest_gui_python_lib:$contest_gui_python_lib/lib-dynload" LD_LIBRARY_PATH="$contest_gui_runtime_candidate/usr/lib/x86_64-linux-gnu:$contest_gui_runtime_candidate/usr/lib${contest_gui_previous_ld:+:$contest_gui_previous_ld}" TK_LIBRARY="$contest_gui_runtime_candidate/usr/share/tcltk/tk8.6" "$contest_gui_candidate" -c "$contest_gui_probe" >/dev/null 2>&1; then
                contest_gui_python="$contest_gui_candidate"
                contest_gui_runtime="$contest_gui_runtime_candidate"
                contest_gui_mode='private'
                break 2
            else
                contest_gui_probe_status=$?
                if [[ "$contest_gui_probe_status" == 2 && -z "$contest_gui_fallback" ]]; then
                    contest_gui_fallback="$contest_gui_candidate"
                    contest_gui_fallback_mode='private'
                    contest_gui_fallback_runtime="$contest_gui_runtime_candidate"
                fi
            fi
        done
    done
fi
if [[ -z "$contest_gui_python" && -n "$contest_gui_fallback" ]]; then
    contest_gui_python="$contest_gui_fallback"
    contest_gui_mode="$contest_gui_fallback_mode"
    contest_gui_runtime="$contest_gui_fallback_runtime"
    printf '%s\n' '提示：当前 Tk 尚未发现中文字体；可安装 fonts-noto-cjk 后重新启动。' >>"$CONTEST_GUI_LOG"
fi
if [[ -z "$contest_gui_python" ]]; then
    contest_gui_error='未找到可用的 Python/Tk 图形环境。Linux 请安装 Python 3、python3-tk 和 fonts-noto-cjk；完整工程仍需配套的编译、综合及仿真工具。'
    printf '%s\n' "$contest_gui_error" | tee -a "$CONTEST_GUI_LOG" >&2
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title='工程助手无法启动' --text="$contest_gui_error\n日志：$CONTEST_GUI_LOG" || true
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage -center 'Cannot start: install Python 3 and python3-tk. See launcher.log.' || true
    fi
    exit 1
fi
unset PYTHONPATH PYTHONHOME TK_LIBRARY TCL_LIBRARY
if [[ "$contest_gui_mode" == private ]]; then
    contest_gui_version=$("$contest_gui_python" -c 'import sys; print("%s.%s" % sys.version_info[:2])')
    export PYTHONPATH="$contest_gui_runtime/usr/lib/python$contest_gui_version:$contest_gui_runtime/usr/lib/python$contest_gui_version/lib-dynload"
    export LD_LIBRARY_PATH="$contest_gui_runtime/usr/lib/x86_64-linux-gnu:$contest_gui_runtime/usr/lib${contest_gui_previous_ld:+:$contest_gui_previous_ld}"
    export TK_LIBRARY="$contest_gui_runtime/usr/share/tcltk/tk8.6"
fi
printf '启动 Python：%s；Tk 模式：%s\n' "$contest_gui_python" "$contest_gui_mode" >>"$CONTEST_GUI_LOG"
for contest_gui_argument in "$@"; do
    if [[ "$contest_gui_argument" == --check ]]; then
        # Keep the explicit inspection command useful in terminals and CI.
        unset CONTEST_GUI_STDERR_LOGGED
        exec "$contest_gui_python" -B "$contest_gui_tools/run_contest_gui.py" "$@"
    fi
done
exec "$contest_gui_python" -B "$contest_gui_tools/run_contest_gui.py" "$@" >>"$CONTEST_GUI_LOG" 2>&1
