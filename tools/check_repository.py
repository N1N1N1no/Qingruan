#!/usr/bin/env python3
"""Offline checks of committed inputs, dependency closure and source syntax."""
import ast,hashlib,json,re,sys,tokenize
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
errors=[];snapshots={}
for name in ['rtl','inference','conversion']:
 base=ROOT/'repro'/name;d=json.loads((base/'manifest.json').read_text());count=0
 for relative,h in d['files_sha256'].items():
  p=(base/relative).resolve()
  if not p.is_relative_to(base.resolve()) or not p.is_file() or sha(p)!=h:errors.append('repro/'+name+'/'+relative)
  count+=1
 snapshots[name]=count
for line in (ROOT/'competition/SHA256SUMS').read_text().splitlines():
 if not line.strip():continue
 h,relative=line.split(maxsplit=1);p=ROOT/'competition'/relative.lstrip('*')
 # This repository selects Swin. Four unrelated organizer models stay local.
 if not p.exists() and relative.startswith('models/') and 'swin_tiny' not in relative:continue
 if not p.is_file() or sha(p)!=h:errors.append('competition/'+relative)
parsed=0
for p in ROOT.rglob('*.py'):
 if p.is_symlink() or any(x in p.relative_to(ROOT).parts for x in ['rootfs','runtime','gpu-runtime','toolchain-cache','work','runs','results']):continue
 # One preserved failed-syntax fixture is an explicitly historical diagnostic.
 if 'gate-driver-failed-syntax-v1' in p.parts:continue
 try:
  with tokenize.open(p) as f:ast.parse(f.read(),filename=str(p))
  parsed+=1
 except (SyntaxError,UnicodeError) as e:errors.append(str(p.relative_to(ROOT))+': '+str(e))
for e in json.loads((ROOT/'evidence/index.json').read_text())['entries']:
 if sha(ROOT/e['archived_path'])!=e['sha256']:errors.append(e['archived_path'])
for p in [ROOT/'README.md',ROOT/'docs/HANDOFF.md']:
 text=p.read_text()
 for label,target in re.findall(r'\[([^\]]*)\]\(([^)]+)\)',text):
  target=target.strip('<>').split('#',1)[0]
  if not target or re.match(r'^[a-z]+:',target):continue
  if not (p.parent/target).exists():errors.append(str(p.relative_to(ROOT))+': missing link '+target)
result={'status':'PASS' if not errors else 'FAIL','repro_manifest_files':snapshots,'python_files_parsed':parsed,'errors':errors,'scope':'离线源码/模型图片/三个组件的哈希闭包与入口文档链接；不是新的RTL、门级、20图精度或完整ADP验收。'}
print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(bool(errors))
