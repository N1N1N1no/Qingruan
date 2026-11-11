#!/usr/bin/env bash
set -euo pipefail
conv1_tile_prepare_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$conv1_tile_prepare_dir/run_conv1_tile.sh" --stage vector "$@"
