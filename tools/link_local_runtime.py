#!/usr/bin/env python3
"""Link an existing local tool installation; never download or copy tools/keys."""
import argparse,json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--workspace',required=True,type=Path,help='已有可运行环境的工作区，例如 /mnt/data/Ubuntu/qingruan')
a=p.parse_args();source=a.workspace.expanduser().resolve()
assert source!=ROOT and (source/'native/rootfs').is_dir(),'需要已有本机兼容环境工作区，不是当前源码仓库'
links=['native/rootfs','native/cache','native/swin-tiny/runtime','native/optimization/gpu-runtime','native/optimization/toolchain-cache','migration/export-status.json','migration/source-vm']
created=[];missing=[]
for name in links:
 src=source/name;dst=ROOT/name
 if not src.exists():missing.append(name);continue
 dst.parent.mkdir(parents=True,exist_ok=True)
 if dst.is_symlink():
  assert dst.resolve()==src.resolve(),'现有链接指向不同位置: '+str(dst)
 elif dst.exists():raise SystemExit('保留现有路径，请先核对: '+str(dst))
 else:dst.symlink_to(src,target_is_directory=src.is_dir())
 created.append(name)
result={'runtime_workspace':str(source),'repository':str(ROOT),'linked':created,'unavailable_optional_paths':missing,'note':'只连接本机已有环境；没有复制商业工具、许可内容或运行中work目录。'}
(ROOT/'local-runtime.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
