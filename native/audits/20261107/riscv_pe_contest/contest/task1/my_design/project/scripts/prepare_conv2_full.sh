#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/../../../../env.sh"
exec "$TASK2_PYTHON" "$script_dir/prepare_conv2_full.py" "$@"
