"""Phase-1 candidate comparison; never promote test-set products to SoC ADP."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
bench_path=ROOT/'work/array-module-benchmark/summary.json';area_path=ROOT/'work/array-synthesis/summary.json'
bench=json.loads(bench_path.read_text());assert bench['status']=='PASS'and len(bench['completed'])==10
areas={x['id']:x for x in json.loads(area_path.read_text())if x['status']=='PASS'}
conv_path=ROOT/'work/array-module-conv-direct/summary.json';conv={}
if conv_path.is_file():
 extra=json.loads(conv_path.read_text())
 if extra['status']=='PASS'and len(extra['completed'])==10:conv={x['id']:x for x in extra['completed']}
rows=[]
for item in bench['completed']:
 r=item['results'];useful=sum(x['useful_MACs']for x in r);issued=sum(x['issued_MAC_slots']for x in r)
 peak=item['geometry']['rows']*item['geometry']['columns']*item['geometry']['lanes']
 extra=conv[item['id']]['results']if item['id']in conv else[]
 all_useful=useful+sum(x['useful_MACs']for x in extra);all_issued=issued+sum(x['issued_MAC_slots']for x in extra)
 all_cycles=item['total_engine_cycles']+sum(x['engine_cycles']for x in extra)
 time_utilization=all_useful/(peak*all_cycles);acceptance_fraction=all_issued/(peak*all_cycles)
 assert 0<time_utilization<=acceptance_fraction<=1
 a=areas.get(item['id']);rows.append({'id':item['id'],'banks':item['geometry']['SRAMs'],'module_test_cycles':item['total_engine_cycles'],
  'slot_utilization':useful/issued,'mapped_array_area':a['area']if a else None,
  'testset_array_area_cycle_product':a['area']*item['total_engine_cycles']if a else None,
  'area_evidence_complete':a is not None,'dominated_on_aggregate_testset_by':[],'dominated_on_every_test_case_by':[],
  'extra_Conv_direct_B_tile_cycles':conv[item['id']]['total_engine_cycles']if item['id']in conv else None,
  'all12_module_test_cycles':all_cycles if extra else None,'peak_MACs_per_cycle':peak,
  'useful_MACs_all_available_cases':all_useful,'issued_MAC_slots_all_available_cases':all_issued,
  'useful_peak_utilization_over_engine_cycles':time_utilization,'tile_acceptance_fraction_over_engine_cycles':acceptance_fraction})
by_id={x['id']:x for x in bench['completed']}
for r in rows:
 if r['mapped_array_area']is None:continue
 for other in rows:
  if other['id']==r['id']or other['mapped_array_area']is None:continue
  cheaper=other['mapped_array_area']<=r['mapped_array_area'];faster=other['module_test_cycles']<=r['module_test_cycles']
  if cheaper and faster:r['dominated_on_aggregate_testset_by'].append(other['id'])
  if cheaper and all(a['engine_cycles']<=b['engine_cycles']for a,b in zip(by_id[other['id']]['results'],by_id[r['id']]['results'])):r['dominated_on_every_test_case_by'].append(other['id'])
summary={'status':'MODULE_COMPARISON_COMPLETE_AREA_SCAN_INCOMPLETE'if len(areas)<10 else'MODULE_COMPARISON_COMPLETE_NO_FINAL_SOC_SELECTION',
 'benchmark_configurations':10,'synthesis_configurations':len(areas),'rows':rows,
 'selection':'ARRAY_MODULE_EVIDENCE_READY_FOR_PHASE1_REVIEW_FINAL_SOC_ADP_IS_PHASE2'if len(areas)==10 else'PENDING_REMAINING_ARRAY_MAPPING_AND_PHASE1_ENGINEERING_SELECTION',
 'scope':'common 11 representative row tiles under the same AXI stalls; module/array area only; no full inference, CPU scheduling, non-MAC latency or complete SoC area; no final ADP/global-optimum claim',
 'extra_Conv_scope':'64x96x48 transB=1 MAC row tile for direct raw Conv B; im2col, postscale and output reorder are excluded from these MAC cycles',
 'utilization_definitions':{'slot_utilization':'original11 useful MACs / issued MAC slots, showing shape padding efficiency',
  'useful_peak_utilization_over_engine_cycles':'all available cases useful MACs / (measured GEMM command cycles * ROWS * COLS * LANES), includes command DMA/control/wait time',
  'tile_acceptance_fraction_over_engine_cycles':'issued tiles / measured command cycles; pipeline accepts one tile/cycle, verified separately; no whole-SoC/full-inference utilization claim'},
 'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in[bench_path,area_path]+([conv_path]if conv else[])}}
(ROOT/'array-module-comparison.json').write_text(json.dumps(summary,indent=2)+'\n')
table=['# 阵列模块比较（第一阶段）','','相同 11 组代表性行块；不含完整推理、RISC-V 调度和非 MAC 延迟。面积为阵列自身，缺少的面积须等待综合。不能当作完整 SoC ADP。','',
 '|配置|SRAM 宏|原 11 组周期|额外 Conv 行块周期|原 11 组填充效率|全命令周期有效利用率|阵列面积|','|---|---:|---:|---:|---:|---:|---:|']
for r in rows:table.append('|%s|%d|%d|%s|%.2f%%|%.3f%%|%s|'%(r['id'],r['banks'],r['module_test_cycles'],'待完成'if r['extra_Conv_direct_B_tile_cycles']is None else str(r['extra_Conv_direct_B_tile_cycles']),r['slot_utilization']*100,r['useful_peak_utilization_over_engine_cycles']*100,'待完成'if r['mapped_array_area']is None else'%.2f'%r['mapped_array_area']))
table += ['','额外 Conv 行块为 64×96×48、transB=1，验证直接读取原始卷积权重的接法；未计入 im2col、后缩放和输出转置，不能当作该 Conv 完整延迟。','',
 '填充效率只看已发射的计算槽，不统计等待时间。全命令周期有效利用率按“有效 MAC 数÷（命令总周期×每周期峰值 MAC 数）”计算，包含本模块 DMA、控制和等待。两者都不能替代完整推理中的阵列利用率。','',
 '面积与周期均不占优的配置，仅可称为“在这组模块测试中被支配”。每项测试的形状及独立计数保存在 `work/array-module-benchmark/` 和 `work/array-module-conv-direct/`，后续还需模块选型和最终完整系统评价。']
(ROOT/'阵列模块比较.md').write_text('\n'.join(table)+'\n');print(summary['status'],len(areas),flush=True)
