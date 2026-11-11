#!/usr/bin/env bash
# Copy this file to the exported contest/env.sh. Source it before running tools.
# Installed compilers, commercial programs and licences stay on the recipient's machine.
_contest_env_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export CONTEST_ROOT="$_contest_env_root"
if [[ -r "$_contest_env_root/runtime/env.local.sh" ]]; then
    source "$_contest_env_root/runtime/env.local.sh" || return 1
fi
if [[ -r "$_contest_env_root/env.local.sh" ]]; then
    source "$_contest_env_root/env.local.sh" || return 1
fi
# A vendor setup script may set its own CONTEST_ROOT. Keep data paths attached
# to this extracted project while preserving its tool and licence variables.
export CONTEST_ROOT="$_contest_env_root"

_contest_add_path() {
    [[ -d "$1" ]] || return 0
    case ":${PATH-}:" in *":$1:"*) ;; *) PATH="$1${PATH:+:$PATH}" ;; esac
}
_contest_find_tool() {
    local _contest_target=$1 _contest_program=$2 _contest_candidate
    shift 2
    [[ -z ${!_contest_target-} ]] || return 0
    _contest_candidate=$(command -v "$_contest_program" 2>/dev/null || true)
    if [[ -n "$_contest_candidate" ]]; then
        printf -v "$_contest_target" '%s' "$_contest_candidate"
        export "$_contest_target"
        return 0
    fi
    for _contest_candidate in "$@"; do
        if [[ -x "$_contest_candidate" && ! -d "$_contest_candidate" ]]; then
            printf -v "$_contest_target" '%s' "$_contest_candidate"
            export "$_contest_target"
            return 0
        fi
    done
    printf -v "$_contest_target" '%s' "$_contest_program"
    export "$_contest_target"
}

# Prefer an explicitly configured interpreter. Task1 runners need Python 3.9+.
if [[ -z ${TASK1_PYTHON-} ]]; then
    for _contest_candidate in python3 "$HOME/miniforge3/bin/python3" /usr/bin/python3; do
        if "$_contest_candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
            export TASK1_PYTHON="$_contest_candidate"
            break
        fi
    done
    : "${TASK1_PYTHON:=python3}"
    export TASK1_PYTHON
fi
if [[ -z ${TASK2_PYTHON-} ]]; then
    if [[ -x "$HOME/miniforge3/envs/task2-cnn/bin/python" ]]; then
        export TASK2_PYTHON="$HOME/miniforge3/envs/task2-cnn/bin/python"
    else
        export TASK2_PYTHON="$TASK1_PYTHON"
    fi
fi
if [[ -z ${RISCV_PREFIX-} ]]; then
    for _contest_program in riscv-none-elf-gcc riscv64-unknown-elf-gcc riscv32-unknown-elf-gcc; do
        _contest_candidate=$(command -v "$_contest_program" 2>/dev/null || true)
        if [[ -n "$_contest_candidate" ]]; then
            export RISCV_PREFIX="${_contest_candidate%gcc}"
            break
        fi
    done
    if [[ -z ${RISCV_PREFIX-} ]]; then
        for _contest_candidate in "$HOME"/rtl_tools/xpack-riscv-none-elf-gcc-*/bin/riscv-none-elf-gcc; do
            if [[ -x "$_contest_candidate" ]]; then
                export RISCV_PREFIX="${_contest_candidate%gcc}"
                break
            fi
        done
    fi
    : "${RISCV_PREFIX:=riscv-none-elf-}"
    export RISCV_PREFIX
fi
_contest_find_tool TASK1_YOSYS yosys "$HOME/miniforge3/envs/rtl-yosys/bin/yosys"
_contest_find_tool TASK1_ABC yosys-abc "$HOME/miniforge3/envs/rtl-yosys/bin/yosys-abc"
_contest_find_tool TASK1_SV2V sv2v "$HOME"/rtl_tools/sv2v-source-*/bin/sv2v
_contest_find_tool TASK1_STA sta "$HOME"/rtl_tools/opensta-build.*/sta-install/bin/sta
for _contest_candidate in "$TASK1_PYTHON" "$TASK1_YOSYS" "$TASK1_SV2V" "${RISCV_PREFIX}gcc"; do
    [[ "$_contest_candidate" != */* ]] || _contest_add_path "${_contest_candidate%/*}"
done
if [[ -n ${VCS_HOME-} ]]; then
    _contest_add_path "$VCS_HOME/bin"
    _contest_add_path "$VCS_HOME/gui/dve/bin"
fi
if [[ -n ${DC_HOME-} ]]; then _contest_add_path "$DC_HOME/bin"; fi
if [[ -n ${PT_HOME-} ]]; then _contest_add_path "$PT_HOME/bin"; fi

export TASK1_BASE="$CONTEST_ROOT/task1/base"
export TASK1_DATA="$CONTEST_ROOT/task1/libraries"
export TASK1_STD_LIB="$TASK1_DATA/standard.lib"
export TASK1_SRAM_LIB="$TASK1_DATA/sram.lib"
export TASK1_STD_MODEL="$TASK1_DATA/standard_timing.v"
export TASK1_SRAM_MODEL="$TASK1_DATA/sram_timing.v"
export TASK1_SDC_REFERENCE="$TASK1_DATA/soc_10ns.sdc"
export TASK2_BASE="$CONTEST_ROOT/task2/base"
_contest_add_path "$CONTEST_ROOT/tools/bin"
export PATH
hash -r
unset -f _contest_add_path _contest_find_tool
unset _contest_env_root _contest_candidate _contest_program
