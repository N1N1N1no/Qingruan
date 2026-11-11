#!/usr/bin/env bash
set -euo pipefail
pe_axi_sdf_project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$pe_axi_sdf_project_dir/../../../env.sh"
exec "$TASK1_PYTHON" "$pe_axi_sdf_project_dir/scripts/run_pe_axi_sdf.py"
