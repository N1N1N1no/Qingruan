#!/usr/bin/env python3
"""Generate an isolated no-memory-macro VeeR configuration and source lists."""
import argparse, os, re, subprocess
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument('--rv-root',required=True)
ap.add_argument('--skip-config',action='store_true',help='Use existing snapshots/soc (for validation)')
a=ap.parse_args()
project=Path(__file__).resolve().parents[1]
root=Path(a.rv_root).resolve()
os.chdir(project)
build=project/'build'; build.mkdir(exist_ok=True)
env=os.environ.copy(); env.update(RV_ROOT=str(root),PWD=str(project))
if not a.skip_config:
    subprocess.run(['perl',str(root/'configs/veer.config'),'-snapshot=soc',
                    '-set=build_axi4','-set=iccm_enable=0','-set=dccm_enable=0',
                    '-set=icache_enable=0','-set=reset_vec=0x80000000'],env=env,check=True)
snap=project/'snapshots/soc'
defs=(snap/'common_defines.vh').read_text(encoding='utf-8')
for name in ['RV_ICCM_ENABLE','RV_DCCM_ENABLE','RV_ICACHE_ENABLE','RV_LOCKSTEP_ENABLE']:
    if re.search(r'^\s*`define\s+'+name+r'\b',defs,re.M):
        raise SystemExit('Unsupported enabled feature: '+name)
if not re.search(r'^\s*`define\s+RV_BUILD_AXI4\b',defs,re.M):
    raise SystemExit('This top requires AXI4')
wrapper=(root/'testbench/veer_wrapper.sv').read_text(encoding='utf-8')
header=wrapper[wrapper.index('\nmodule '):wrapper.index('\n);')]
core_id=bool(re.search(r'\binput\s+logic\s+\[31:4\]\s+core_id\b',header))
prefix=['+libext+.v+.sv','+define+RV_OPENSOURCE']
if core_id: prefix.append('+define+SOC_CPU_HAS_CORE_ID')
prefix += ['+incdir+'+p.as_posix() for p in [snap,root/'design/include',root/'design/lib',root/'testbench']]
# Keep upstream compile order. Do not import testbench/DPI/fault-injection sources.
files=[(snap/'common_defines.vh').as_posix(),(root/'design/include/el2_def.sv').as_posix(),(snap/'el2_pdef.vh').as_posix()]
for line in (root/'testbench/flist').read_text(encoding='utf-8').splitlines():
    line=line.strip()
    if not line or line.startswith(('//','+','-y')): continue
    line=line.replace('$RV_ROOT',root.as_posix())
    if '/verification/' in line or 'lockstep' in line: continue
    if '/design/' not in line and not line.endswith('/testbench/veer_wrapper.sv'): continue
    path=line[3:] if line.startswith('-v ') else line
    if not Path(path).is_file(): raise SystemExit('Missing source '+path)
    if path not in files: files.append(path)
if (root/'testbench/veer_wrapper.sv').as_posix() not in files:
    raise SystemExit('Official flat veer_wrapper is missing from file list')
# The upstream flist supplies these definitions via -y design/lib. sv2v
# needs them explicitly to expand .* even inside parameter-controlled branches.
# Keep both simulation and synthesis lists identical apart from SYNTHESIS.
for ahb_name in ('axi4_to_ahb.sv', 'ahb_to_axi4.sv'):
    ahb_source = root/'design/lib'/ahb_name
    if not ahb_source.is_file():
        raise SystemExit('Missing required sv2v library source '+str(ahb_source))
    if ahb_source.as_posix() not in files:
        files.append(ahb_source.as_posix())
files += [(project/line).as_posix() for line in (project/'rtl/rtl.f').read_text(encoding='utf-8').splitlines() if line]
for name,extra in [('rtl.f',[]),('synth.f',['+define+SYNTHESIS'])]:
    (build/name).write_text('\n'.join(prefix+extra+files)+'\n',encoding='utf-8')
try:
    rev=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
except (OSError,subprocess.CalledProcessError): rev='not a Git checkout'
(build/'source_info.txt').write_text(f'RV_ROOT={root}\nrevision={rev}\nreset=0x80000000\nPE_BASE=0x90000000\nICCM=DCCM=ICACHE=0\n',encoding='utf-8')
print('Prepared build/rtl.f and build/synth.f; source:',root)
