#!/usr/bin/env bash
set -euo pipefail
task_contest_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$task_contest_root/env.sh"
exec "${TASK1_PYTHON}" "$task_contest_root/task1/scripts/run_rtl.py" "$@"
