#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$BASE/run-native.sh" bash /home/zhz/contest/task1/run_rtl.sh "$@"
