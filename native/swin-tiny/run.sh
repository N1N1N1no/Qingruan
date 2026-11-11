#!/usr/bin/env bash
set -euo pipefail
SWIN_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SWIN_DIR/runtime${PYTHONPATH:+:$PYTHONPATH}"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
exec /home/zhz/miniforge3/envs/task2-cnn/bin/python -B "$SWIN_DIR/main.py" "$@"
