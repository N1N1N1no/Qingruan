#!/usr/bin/env bash
set -euo pipefail
conv2_tile_prepare_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$conv2_tile_prepare_dir/run_conv2_tile.sh" --stage vector "$@"
