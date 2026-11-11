#!/usr/bin/env bash
set -euo pipefail
soc_read_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$soc_read_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$soc_read_project_dir/scripts/run_soc_read.py"
