#!/usr/bin/env bash
set -euo pipefail
bundle_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1-} == --desktop-dir && -n ${2-} ]]; then
    bundle_desktop=$2
elif command -v xdg-user-dir >/dev/null 2>&1; then
    bundle_desktop=$(xdg-user-dir DESKTOP)
elif [[ -d "$HOME/桌面" ]]; then
    bundle_desktop="$HOME/桌面"
else
    bundle_desktop="$HOME/Desktop"
fi
mkdir -p "$bundle_desktop"
bundle_python=$(command -v python3 || true)
if [[ -z "$bundle_python" ]]; then
    printf '%s\n' '请先安装 Python 3，再创建快捷方式。' >&2
    exit 1
fi
"$bundle_python" - "$bundle_root" "$bundle_desktop" <<'PY'
from pathlib import Path
import sys

root, desktop = map(Path, sys.argv[1:])
def quoted(value):
    text = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    return '"' + text + '"'
path = desktop / 'RISC-V与PE完整工程.desktop'
path.write_text('[Desktop Entry]\nVersion=1.0\nType=Application\nName=RISC-V与PE完整工程\n'
                'Comment=启动完整工程的仿真、综合与模型部署界面\n'
                'Exec=bash ' + quoted(root / 'start.sh') + '\n'
                'Icon=' + str(root / 'contest/task1/my_design/tools/contest_gui/icon.svg') + '\n'
                'Terminal=false\nCategories=Development;Electronics;\nStartupNotify=true\n')
path.chmod(0o755)
print('桌面快捷方式：' + str(path))
PY
bundle_font_families=''
if command -v fc-list >/dev/null 2>&1; then bundle_font_families=$(fc-list : family); fi
if [[ "$bundle_font_families" != *'Noto Sans CJK SC'* && "$bundle_font_families" != *WenQuanYi* ]]; then
    if [[ -f "$bundle_root/contest/fonts/NotoSansCJK-Regular.ttc" ]]; then
        bundle_fonts="$HOME/.local/share/fonts/riscv-pe"
        mkdir -p "$bundle_fonts"
        cp "$bundle_root/contest/fonts/NotoSansCJK-Regular.ttc" "$bundle_fonts/"
        if command -v fc-cache >/dev/null 2>&1; then fc-cache "$bundle_fonts"; fi
    fi
fi
if command -v gio >/dev/null 2>&1; then
    gio set "$bundle_desktop/RISC-V与PE完整工程.desktop" metadata::trusted true 2>/dev/null || true
fi
printf '%s\n' '已创建快捷方式；如桌面提示“允许启动”，右键允许后再双击。'
