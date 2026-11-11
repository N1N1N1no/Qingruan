#!/usr/bin/env bash
set -euo pipefail
soc_constraints_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$soc_constraints_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$soc_constraints_project_dir/scripts/run_soc_constraints.py"
