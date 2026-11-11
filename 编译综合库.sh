#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$BASE/native/scripts/prepare-sram-compat.py"
exec "$BASE/run-native.sh" dc_shell -no_gui -f /workspace/native/scripts/build-libraries.tcl
