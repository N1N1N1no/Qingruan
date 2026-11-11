"""Recover completed reports after the collector was interrupted.

Require the final mapping marker, normal DC exit trailer, every saved artifact,
and unchanged frozen inputs. A negative timing result remains a failure.
"""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
case=ROOT/'work/soc-synthesis/R8_C32_L4'
proof=json.loads((case/'preparation.json').read_text())
log=(case/'synthesis.log').read_text(errors='replace')
assert 'ACCEL_SOC_SYNTHESIS_MAPPED\nexit' in log and 'Thank you...' in log
assert not re.search(r'(?m)^Error:',log)
names=['area.rpt','setup.rpt','hold.rpt','violations.rpt','timing_check.rpt','check_design.rpt','mapped.ddc','mapped.v','constraints.sdc','units.rpt']
for name in names:assert (case/name).is_file() and (case/name).stat().st_size>0,name
for p,want in proof['inputs_sha256'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want,p
def slack(name):return float(re.search(r'slack\s+\([^)]+\)\s+(-?[\d.]+)',(case/name).read_text())[1])
setup,hold=slack('setup.rpt'),slack('hold.rpt')
summary={**proof,'status':'MAPPED_DC_TIMING_PASS' if min(setup,hold)>=0 else 'MAPPED_DC_TIMING_FAIL',
 'area':float(re.search(r'Total cell area:\s+([\d.]+)',(case/'area.rpt').read_text())[1]),
 'setup_slack_ns':setup,'hold_slack_ns':hold,'clock_period_ns':10,'SRAMs':proof['rows']*proof['columns'],
 'collector_interrupted':True,'synthesis_returncode_not_observed':True,
 'synthesis_CPU_seconds':float(re.search(r'CPU usage for this session ([\d.]+) seconds',log)[1]),
 'artifacts_sha256':{name:hashlib.sha256((case/name).read_bytes()).hexdigest() for name in names},
 'scope':'complete CPU/router/DMA/MAC/GELU/quantizer prototype; FP/vector extensions, hold correction, functional gate simulation, signoff STA and full-RV32 inference/ADP pending',
 'formal_equivalence':False,'SDF':False,'signoff_STA':False}
(case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:summary[k]for k in ['status','area','setup_slack_ns','hold_slack_ns','SRAMs']}),flush=True)
