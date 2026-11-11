#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == rtl ]]; then
    exec "$BASE/run-native.sh" bash /workspace/native/swin-tiny/run.sh "$@"
fi
exec "$BASE/run-native.sh" --no-license bash /workspace/native/swin-tiny/run.sh "$@"
