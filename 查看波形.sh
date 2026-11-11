#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then
    echo '用法：查看波形.sh [VPD文件]；不指定文件时打开最近一次通过的 RTL 前仿波形。'
    exit 0
fi
if [[ "$#" -gt 1 ]]; then echo '只能指定一个 VPD 文件。' >&2; exit 2; fi
if [[ -z "${DISPLAY:-}" ]]; then echo '请在 Ubuntu 图形桌面的终端中运行此命令。' >&2; exit 2; fi
if [[ "$#" -eq 1 ]]; then
    VPD="$(realpath -- "$1")"
else
    VPD="$(python3 - "$BASE" <<'PY'
from pathlib import Path
import json,sys
b=Path(sys.argv[1]);task=b/'contest/task1';candidates=[]
for p in (task/'work').glob('rtl.*/summary.json'):
 try:
  v=p.parent/'delay0/soc.vpd'
  if json.loads(p.read_text()).get('status')=='PASS' and v.is_file() and v.stat().st_size:candidates.append(v)
 except (OSError,ValueError):pass
v=max(candidates,key=lambda x:x.stat().st_mtime) if candidates else task/'reference_rtl/delay0/soc.vpd'
print(v.resolve())
PY
)"
fi
if [[ ! -f "$VPD" ]]; then echo "找不到波形文件：$VPD" >&2; exit 2; fi
exec "$BASE/run-native.sh" dve -full64 -vpd "$VPD"
