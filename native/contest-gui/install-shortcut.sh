#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
DESKTOP="$(xdg-user-dir DESKTOP)"
mkdir -p "$DESKTOP" "$HOME/.local/share/applications"
install -m 755 "$BASE/RISC-V与PE工程助手.desktop" "$DESKTOP/RISC-V与PE工程助手.desktop"
install -m 644 "$BASE/RISC-V与PE工程助手.desktop" "$HOME/.local/share/applications/riscv-pe-assistant.desktop"
gio set "$DESKTOP/RISC-V与PE工程助手.desktop" metadata::trusted true || true
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications"
fi
echo "桌面快捷方式：$DESKTOP/RISC-V与PE工程助手.desktop"
