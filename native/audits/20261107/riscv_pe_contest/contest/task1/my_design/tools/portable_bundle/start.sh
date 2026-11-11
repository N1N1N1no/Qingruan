#!/usr/bin/env bash
set -euo pipefail
bundle_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$bundle_root/contest/task1/my_design/tools/start_gui.sh" --project "$bundle_root/contest/task1/my_design" "$@"
