#!/usr/bin/env bash
set -euo pipefail
conv2_full_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$conv2_full_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$conv2_full_project_dir/scripts/run_conv2_full.py" "$@"
