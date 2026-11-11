#!/usr/bin/env bash
set -euo pipefail
soc_functional_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$soc_functional_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$soc_functional_project_dir/scripts/run_soc_functional.py" "$@"
