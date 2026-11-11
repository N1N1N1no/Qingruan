#!/usr/bin/env bash
set -euo pipefail
conv1_tile_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$conv1_tile_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$conv1_tile_project_dir/scripts/run_conv1_tile.py" "$@"
