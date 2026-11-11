#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$BASE/编译综合库.sh" > "$BASE/native/validation/library-build.log" 2>&1
exec "$BASE/run-native.sh" bash /workspace/native/scripts/validate-toolchain.sh
