#!/usr/bin/env python3
"""Build a complete Linux engineering archive, with a file-level manifest."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

DESIGN = Path(__file__).resolve().parents[1]
TASK = DESIGN.parent
CONTEST = TASK.parent
TEMPLATES = DESIGN / 'tools/portable_bundle'
SKIP = {'build', 'work', '__pycache__', '.git', '.pytest_cache', '.mypy_cache'}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def copy_tree(source, target, dereference_internal=False):
    for folder, directories, files in os.walk(source):
        directories[:] = sorted(name for name in directories if name not in SKIP)
        relative = Path(folder).relative_to(source)
        for name in sorted(files):
            if name.endswith(('.pyc', '.pyo', '.vpd', '.vcd', '.fsdb')) or name in {'ucli.key', 'core'}:
                continue
            original = Path(folder) / name
            if original.is_symlink():
                try:
                    original.resolve().relative_to(source.resolve())
                    internal = True
                except ValueError:
                    internal = False
                if not (dereference_internal and internal):
                    raise RuntimeError('Unexpected source symlink: ' + str(original))
            destination = target / relative / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path.home() / '桌面/RISC-V与PE完整工程_Linux.zip')
    parser.add_argument('--staging-dir', type=Path)
    args = parser.parse_args()
    parent = args.staging_dir or Path(tempfile.mkdtemp(prefix='package_linux.', dir=TASK / 'work'))
    root = parent / 'riscv_pe_contest'
    if root.exists():
        raise RuntimeError('Use a fresh staging directory: ' + str(root))
    contest = root / 'contest'
    design = contest / 'task1/my_design'
    copy_tree(DESIGN, design)
    for name in ('base', 'libraries', 'scripts', 'reference_rtl'):
        copy_tree(TASK / name, contest / 'task1' / name)
    for name in ('run_rtl.sh', 'SOURCE_MANIFEST.json', 'PROVENANCE.json', 'README.md'):
        shutil.copy2(TASK / name, contest / 'task1' / name)
    copy_tree(CONTEST / 'task2/base', contest / 'task2/base')
    # The converted public libraries are small; mapped checkpoints and old
    # waveforms are deliberately rebuilt on the receiving machine.
    library_names = ('standard.db', 'sram.db', 'check_dc.tcl', 'prepare.tcl',
                     'prepare_sram.py', 'sram_dc_compatible.lib', 'README.md')
    library = design / 'project/build/dc_libraries'
    library.mkdir(parents=True)
    for name in library_names:
        shutil.copy2(DESIGN / 'project/build/dc_libraries' / name, library / name)
    path = library / 'prepare.tcl'
    text = path.read_text().replace('/home/zhz/contest/task1/libraries/standard.lib',
        '[file normalize [file join $output_dir .. .. .. .. libraries standard.lib]]')
    path.write_text(text)
    path = library / 'prepare_sram.py'
    text = path.read_text().replace("Path('/home/zhz/contest/task1/libraries/sram.lib')",
                                    "Path(__file__).resolve().parents[4] / 'libraries/sram.lib'")
    path.write_text(text)
    for name in ('env.sh', 'env.local.sh.example', 'README_environment.md'):
        shutil.copy2(TEMPLATES / name, contest / name)
    copy_tree(TEMPLATES / 'runtime', contest / 'runtime')
    copy_tree(TEMPLATES / 'tools_bin', contest / 'tools/bin')
    shutil.copy2(TEMPLATES / 'vcs_vm.py', contest / 'task1/scripts/vcs_vm.py')
    runtime = Path.home() / '.local/share/riscv-pe-gui/runtime'
    if runtime.is_dir():
        copy_tree(runtime, contest / 'gui_runtime', dereference_internal=True)
    font = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if font.is_file():
        (contest / 'fonts').mkdir()
        shutil.copy2(font, contest / 'fonts' / font.name)
        copyright_file = Path('/usr/share/doc/fonts-noto-cjk/copyright')
        if copyright_file.is_file():
            shutil.copy2(copyright_file, contest / 'fonts/LICENSE.txt')
    docs = contest / 'docs'
    docs.mkdir()
    for name in ('RISC-V与PE工程入门笔记.md', 'RISC-V与PE工程入门笔记.pdf',
                 'RISC-V与PE工程基础命令与排错指南.md', 'RISC-V与PE工程基础命令与排错指南.pdf'):
        source = Path.home() / '桌面' / name
        if source.is_file():
            shutil.copy2(source, docs / name)
    for source, target in (('start.sh', 'start.sh'), ('install_launcher.sh', 'install_launcher.sh'),
                           ('verify_package.py', 'verify_package.py'), ('README_package.md', 'README_先看这里.md')):
        shutil.copy2(TEMPLATES / source, root / target)
    required = [
        'start.sh', 'install_launcher.sh', 'contest/env.sh', 'contest/runtime/env.vm.sh',
        'contest/task1/run_rtl.sh', 'contest/task1/scripts/run_rtl.py', 'contest/task1/scripts/vcs_vm.py',
        'contest/task1/my_design/project/Makefile', 'contest/task1/my_design/project/rtl/soc_top.sv',
        'contest/task1/my_design/project/sw/pe_smoke.c',
        'contest/task1/my_design/project/sw/conv2_full.c',
        'contest/task1/my_design/project/scripts/run_conv1_full.py',
        'contest/task1/my_design/project/synth/run_soc_sdf.sh',
        'contest/task1/my_design/project/build/dc_libraries/standard.db',
        'contest/task1/my_design/project/build/dc_libraries/sram.db',
        'contest/task1/my_design/vendor/Cores-VeeR-EL2/design/el2_veer.sv',
        'contest/task1/my_design/tools/contest_gui/catalog.py',
        'contest/task2/base/quant_output/aircraft_cnn_quantized.npz',
    ]
    sys.path.insert(0, str(DESIGN / 'tools'))
    from contest_gui.catalog import get_tasks
    tasks = get_tasks(design / 'project')
    for task in tasks:
        entries = ([task.target] if task.kind == 'shell' else [])
        if task.kind == 'dc':
            entries.extend(task.steps)
        entries.extend(name for name in task.required_files
                       if not name.startswith('build/') or name.startswith('build/dc_libraries/'))
        required.extend('contest/task1/my_design/project/' + name for name in entries)
    required = sorted(set(required))
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if path.suffix in ('.sh', '.py') or path.parent == contest / 'tools/bin':
                path.chmod(0o755)
            files[name] = dict(bytes=path.stat().st_size, sha256=sha(path))
    if any(name not in files for name in required):
        raise RuntimeError('Required engineering files missing')
    manifest = dict(kind='FULL_RISCV_PE_LINUX_ENGINEERING_BUNDLE',
                    created_utc=datetime.now(timezone.utc).isoformat(),
                    required_files=required, tasks=len(tasks), groups=len({task.group for task in tasks}), files=files,
                    included='GUI, RTL, CPU vendor source, C, build/verification scripts, public libraries, CNN model and dataset, portable environment templates, docs',
                    excluded='EDA executables and licenses, Python environments, old generated runs, mapped DDC/netlists/SDF/waveforms',
                    portable_copy_adjustments=['relative environment and VCS discovery templates',
                                               'relative DC Liberty conversion paths'])
    (root / 'PACKAGE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    checksums = [record['sha256'] + '  ' + name for name, record in files.items()]
    checksums.append(sha(root / 'PACKAGE_MANIFEST.json') + '  PACKAGE_MANIFEST.json')
    (root / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.partial')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                archive.write(path, Path(root.name) / path.relative_to(root))
    with zipfile.ZipFile(temporary) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError('ZIP integrity error: ' + bad)
    temporary.replace(args.output)
    sidecar = args.output.with_suffix(args.output.suffix + '.sha256')
    sidecar.write_text(sha(args.output) + '  ' + args.output.name + '\n')
    print(json.dumps(dict(status='PASS', archive=str(args.output), bytes=args.output.stat().st_size,
                          files=len(files), staging=str(root), sha256=sha(args.output)), ensure_ascii=False))


if __name__ == '__main__':
    main()
