#!/usr/bin/env bash
set -euo pipefail
conv1_full_prepare_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$conv1_full_prepare_dir/run_conv1_full.sh" --stage vector "$@"
