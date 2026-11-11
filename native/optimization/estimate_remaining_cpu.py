"""Prioritize remaining kernels, never substitute extrapolation for timing."""
import collections,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
plan=json.loads((ROOT/'work/fused-plan/plan.json').read_text())
fusions=json.loads((ROOT/'work/lookup-tables/gelu-fusions.json').read_text())
skip={i for f in fusions for i in range(f['start'],f['end']+1)}
profile=json.loads((ROOT/'work/cpu-profile/summary.json').read_text())
rates={r['op']:r['cycles_per_element'] for r in profile['probes']}
counts=collections.Counter();elements=collections.Counter();post_elements=0
for n in plan['schedule']:
 if n['executor']=='pe':post_elements+=math.prod(plan['tensors'][n['outputs'][0]]['shape'])
 if n['node_index']in skip or n['executor']!='riscv' or n['op']=='QuantizeLinear':continue
 counts[n['op']]+=1;elements[n['op']]+=math.prod(plan['tensors'][n['outputs'][0]]['shape'])
rows=[{'op':op,'nodes':counts[op],'elements':n,'representative_cycles_per_element':rates.get(op),
       'illustrative_cycles_not_full_model_measurement':n*rates[op] if op in rates else None}for op,n in elements.items()]
rows.sort(key=lambda r:r['illustrative_cycles_not_full_model_measurement'] or -1,reverse=True)
result={'status':'ANALYTICAL_PRIORITIZATION_ONLY','remaining_CPU_operators':rows,'MAC_postscale_elements_without_separate_cycle_probe':post_elements,
 'scope':'microkernel rates extrapolated to counts; excludes CPU packing/dispatch, shape-dependent cost differences, contention and unprofiled operators; not complete latency, ADP or candidate selection',
 'next_hardware':['exact FP32 Softmax/LayerNorm and arithmetic/DQ/postscale using shared HardFloat; preserve operation order',
 'multidimensional stride copy for Transpose; existing 2D DMA for materialized Slice/Concat/Gather where valid']}
(ROOT/'remaining-cpu-work.json').write_text(json.dumps(result,indent=2)+'\n')
for r in rows:print(r['op'],r['elements'],r['illustrative_cycles_not_full_model_measurement'])
