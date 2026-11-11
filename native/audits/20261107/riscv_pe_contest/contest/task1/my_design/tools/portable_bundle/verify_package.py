#!/usr/bin/env python3
"""Verify the packaged engineering files without invoking EDA tools."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'PACKAGE_MANIFEST.json').read_text())
    failed = []
    for name, record in manifest['files'].items():
        path = root / name
        if not path.is_file():
            failed.append('缺少文件：' + name)
            continue
        if path.stat().st_size != record['bytes']:
            failed.append('大小不符：' + name)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != record['sha256']:
            failed.append('内容不符：' + name)
    for name in manifest['required_files']:
        if name not in manifest['files']:
            failed.append('打包遗漏工程入口：' + name)
    if failed:
        print('\n'.join(failed))
        raise SystemExit(1)
    print('完整工程文件检查通过：%d 份文件。' % len(manifest['files']))


if __name__ == '__main__':
    main()
