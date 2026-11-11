#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOTFS="$BASE/native/rootfs"
# Entry points can also be used from an already-open native shell.
if [[ "${QINGRUAN_NATIVE:-}" == '1' ]]; then
    if [[ "${1:-}" == '--no-license' ]]; then shift; fi
    if [[ "$#" -eq 0 ]]; then
        exec /bin/bash --rcfile /workspace/native/bashrc -i
    fi
    exec "$@"
fi
if [[ ! -e "$BASE/migration/export-status.json" ]]; then
    echo '本机工程仍在迁移，请等待迁移完成。' >&2
    exit 1
fi
if [[ "${1:-}" == '--no-license' ]]; then
    shift
elif [[ -f "$BASE/native/license-mode" ]] && [[ "$(cat "$BASE/native/license-mode")" == 'original-server' ]]; then
    python3 "$BASE/native/ensure-license.py"
fi
mkdir -p "$BASE/native/tmp" "$BASE/native/run" "$BASE/native/logs"
WORKDIR=/home/zhz/contest
case "$PWD" in "$BASE"|"$BASE"/*) WORKDIR="$PWD";; esac
ARGS=(--die-with-parent --clearenv --ro-bind "$ROOTFS" /)
# Git checkouts may live below a path absent from the read-only legacy rootfs.
# Create the absolute workspace alias in a temporary mount, keeping rootfs intact.
if [[ ! -d "$ROOTFS$BASE" ]]; then
    ALIAS_TOP="${BASE#/}"
    ALIAS_TOP="/${ALIAS_TOP%%/*}"
    ARGS+=(--tmpfs "$ALIAS_TOP")
fi
# Runtime symlinks can point at a different local workspace. Keep their targets
# visible after the temporary parent mount, read-only, while this checkout stays writable.
if [[ -f "$BASE/local-runtime.json" ]]; then
    RUNTIME_WORKSPACE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_workspace"])' "$BASE/local-runtime.json")"
    if [[ "$RUNTIME_WORKSPACE" != "$BASE" ]]; then
        ARGS+=(--ro-bind "$RUNTIME_WORKSPACE" "$RUNTIME_WORKSPACE")
    fi
fi
ARGS+=(--bind "$ROOTFS/home/zhz" /home/zhz
      --bind "$BASE" /workspace --bind "$BASE" "$BASE"
      --bind "$BASE/native/tmp" /tmp --bind "$BASE/native/run" /run
      --proc /proc --dev /dev --tmpfs /dev/shm --ro-bind /sys /sys
      --ro-bind /etc/resolv.conf /etc/resolv.conf
      --setenv HOME /home/zhz --setenv USER zhz --setenv LOGNAME zhz
      --setenv QINGRUAN_NATIVE 1
      --setenv PATH /usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
      --setenv LANG C.UTF-8 --setenv LC_ALL C.UTF-8
      --setenv TERM "${TERM:-xterm-256color}" --chdir "$WORKDIR")
if [[ -d /run/user/"$(id -u)" ]]; then
    ARGS+=(--bind /run/user/"$(id -u)" /run/user/"$(id -u)"
           --setenv XDG_RUNTIME_DIR /run/user/"$(id -u)")
fi
if [[ -d /tmp/qingruan-migration-1000 ]]; then
    ARGS+=(--bind /tmp/qingruan-migration-1000 /tmp/qingruan-migration-1000)
fi
if [[ -d /tmp/.X11-unix ]]; then ARGS+=(--ro-bind /tmp/.X11-unix /tmp/.X11-unix); fi
for NAME in DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS; do
    if [[ -n "${!NAME:-}" ]]; then ARGS+=(--setenv "$NAME" "${!NAME}"); fi
done
exec /usr/bin/bwrap "${ARGS[@]}" /bin/bash --noprofile --norc -c '
source "$HOME/contest/env.sh"
if [ -r /workspace/native/site-env.sh ]; then source /workspace/native/site-env.sh; fi
if [ "$#" -eq 0 ]; then
    exec /bin/bash --rcfile /workspace/native/bashrc -i
fi
exec "$@"
' qingruan-native "$@"
