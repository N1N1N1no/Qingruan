#!/usr/bin/env bash
set -euo pipefail
conv1_prepare_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$conv1_prepare_dir/run_conv1_point.sh" --stage vector "$@"
