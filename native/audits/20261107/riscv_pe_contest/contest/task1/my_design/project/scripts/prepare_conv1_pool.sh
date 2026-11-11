#!/usr/bin/env bash
set -euo pipefail
conv1_pool_prepare_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$conv1_pool_prepare_dir/run_conv1_pool.sh" --stage vector "$@"
