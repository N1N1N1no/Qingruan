#!/usr/bin/env python3
"""Locate the installed VCS, preserving original no-PIE/linker compatibility."""
import os
from pathlib import Path
import shutil
import sys


def arguments(original):
    kept, links = [], ['-no-pie']
    i = 0
    while i < len(original):
        if original[i] == '-LDFLAGS':
            if i + 1 >= len(original):
                raise ValueError('-LDFLAGS requires an argument')
            links.append(original[i + 1])
            i += 2
        else:
            kept.append(original[i])
            i += 1
    if '-cc' not in kept:
        kept += ['-cc', shutil.which('gcc') or '/usr/bin/gcc']
    if '-cpp' not in kept:
        kept += ['-cpp', shutil.which('g++') or '/usr/bin/g++']
    return kept + ['-LDFLAGS', ' '.join(links)]


def installed_vcs():
    myself = Path(__file__).resolve()
    package_root = Path(os.environ.get('CONTEST_ROOT', str(myself.parents[2])))
    blocked = {myself, (package_root / 'tools/bin/vcs').resolve()}
    configured = os.environ.get('TASK1_REAL_VCS')
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise RuntimeError('TASK1_REAL_VCS is not executable: ' + str(candidate))
        if candidate.resolve() in blocked:
            raise RuntimeError('TASK1_REAL_VCS must point to installed VCS, not the bundled adapter')
        return candidate.resolve()
    home = os.environ.get('VCS_HOME')
    if home:
        candidate = Path(home).expanduser() / 'bin/vcs'
        if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.resolve() not in blocked:
            return candidate.resolve()
    for directory in os.get_exec_path():
        candidate = Path(directory) / 'vcs'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            real = candidate.resolve()
            if real not in blocked:
                return real
    # Common user installations, without pinning a user name or tool version.
    candidates = sorted((Path.home() / 'eda/synopsys').glob('vcs*/*/bin/vcs'), reverse=True)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise RuntimeError('VCS not found. Configure VCS_HOME or TASK1_REAL_VCS in env.local.sh')


def main():
    real = installed_vcs()
    if real.parent.name == 'bin':
        os.environ['VCS_HOME'] = str(real.parent.parent)
    os.execv(str(real), [str(real), *arguments(sys.argv[1:])])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('VCS_VM_LAUNCH_ERROR=' + str(error), file=sys.stderr)
        raise SystemExit(2)
