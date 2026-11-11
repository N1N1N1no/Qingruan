#!/usr/bin/env bash
set -euo pipefail
pe_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$pe_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$pe_project_dir/scripts/run_pe_functional.py"
