"""Collect current phase1 backend mappings with explicit module-only scope."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];cache={}
def local(name):
 p=Path(name)
 return BASE/str(p)[11:]if str(p).startswith('/workspace/')else p if p.is_absolute()else BASE/p
def digest(p):
 if p not in cache:cache[p]=hashlib.sha256(p.read_bytes()).hexdigest()
 return cache[p]
bench=json.loads((ROOT/'array-module-comparison.json').read_text());cycles={x['id']:x['all12_module_test_cycles']for x in bench['rows']}
completed=[];pending=[];savings=[]
for identifier in['R8_C16_L4','R8_C32_L4','R16_C8_L4']:
 current={}
 for shared in[False,True]:
  mode='shared'if shared else'private';case=ROOT/'work/backend-synthesis'/identifier/mode;p=case/'summary.json'
  if not p.exists():pending.append(identifier+'/'+mode);continue
  proof=json.loads(p.read_text());assert proof['status']=='MAPPED_UNIT_SETUP_PASS'and proof['clock_period_ns']==10 and proof['input_output_delay_ns']==1
  for name,want in proof['source_sha256'].items():assert digest(local(name))==want,name
  area=float(re.search(r'Total cell area:\s+([\d.]+)',(case/'area.rpt').read_text())[1]);assert abs(area-proof['area'])<0.01
  for name in['mapped.ddc','mapped.v','constraints.sdc']:assert(case/name).is_file()and(case/name).stat().st_size>0
  c=cycles[identifier];entry={k:proof[k]for k in['id','area','setup_slack_ns','hold_slack_ns','SRAMs','FP32_instances_before_mapping','shared_FP32']}
  entry.update(module_test_cycles_12=c,module_testset_area_cycle_product=area*c if c else None,proof=str(p),source_hashes_match_current=True)
  completed.append(entry);current[mode]=entry
 if len(current)==2:
  a=current['private']['area'];b=current['shared']['area'];savings.append({'id':identifier,'area_saved':a-b,'area_saved_percent':(a-b)/a*100})
pool_path=ROOT/'work/backend-fp-pool/summary.json';rv_path=ROOT/'work/complete-soc/summary.json'
pool=json.loads(pool_path.read_text())if pool_path.exists()else{};rv=json.loads(rv_path.read_text())if rv_path.exists()else{}
equal=pool.get('status')=='PASS'and all(x['identical_engine_cycles']for x in pool['comparisons'])and rv.get('status')=='PASS'and rv['identical_engine_cycles']
summary={'status':'PARTIAL_MODULE_COMPARISON'if pending else'MODULE_COMPARISON_READY_FOR_PHASE1_ENGINEERING_REVIEW','completed':completed,'pending':pending,'FP32_area_savings':savings,'FP32_cycles_equal_in_recorded_module_and_RV32_suites':equal,
 'selection':'PENDING_PHASE1_REVIEW_FINAL_SOC_ADP_IS_PHASE2','scope':'mapped complete accelerator backend with all8 engines and fixed mapped array; 10ns/1ns IO; excludes CPU/MMIO/router and nonMAC/CPU/full-inference latency. Twelve MAC module samples are a finite engineering proxy, never complete SoC ADP.'}
(ROOT/'backend-module-comparison.json').write_text(json.dumps(summary,indent=2)+'\n')
lines=['# 完整加速后端模块比较','','全部八引擎和阵列在同一 10ns/1ns IO 条件下映射。面积未含 CPU/MMIO/router；周期是 12 个 MAC 模块样本，不含非 MAC、调度及完整推理，不能当作完整 SoC ADP。','',
 '|配置|FP32 套数|SRAM 宏|后端面积|setup/ns|hold/ns|','|---|---:|---:|---:|---:|---:|']
for x in completed:lines.append('|%s|%d|%d|%.6f|%.9f|%.9f|'%(x['id'],x['FP32_instances_before_mapping'],x['SRAMs'],x['area'],x['setup_slack_ns'],x['hold_slack_ns']))
lines+=['','待完成：'+('、'.join(pending)if pending else'无。仍需第一阶段工程选型与第二阶段完整 SoC/推理验收。'),'',
 '18 组模块对照及独立/共享各 22 命令的实际 RV32 对照记录：'+('周期完全相同。'if equal else'尚未全部完成。')]
for x in savings:lines+=['',x['id']+' 共享 FP32 减少面积 %.6f（%.4f%%）。'%(x['area_saved'],x['area_saved_percent'])]
(ROOT/'后端模块比较.md').write_text('\n'.join(lines)+'\n');print(summary['status'],'completed='+str(len(completed)),'pending='+str(len(pending)),flush=True)
