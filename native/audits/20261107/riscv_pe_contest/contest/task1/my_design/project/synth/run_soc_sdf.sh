#!/usr/bin/env bash
set -euo pipefail
soc_sdf_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$soc_sdf_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$soc_sdf_project_dir/scripts/run_soc_sdf.py" "$@"
