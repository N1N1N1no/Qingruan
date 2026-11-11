#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$BASE/run-native.sh" --no-license bash /home/zhz/contest/task2/run_accuracy.sh "$@"
