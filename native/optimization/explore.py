"""Derive design-space bounds from the actual complete Swin graph.

These are traffic/compute bounds, not measured end-to-end latency or ADP.
"""
import collections
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parents[1]

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ceil(n,d):return (n+d-1)//d

def main():
    latest=json.loads((BASE/'native/swin-tiny/work/latest_verify.json').read_text())
    source=Path(latest['path'].replace('/workspace/',str(BASE)+'/'))
    plan=json.loads((source/'plan.json').read_text())
    macs=[n['job'] for n in plan['schedule'] if n['executor']=='pe']
    cpu=collections.defaultdict(lambda:{'nodes':0,'output_elements':0,'input_bytes':0,'output_bytes':0})
    for n in plan['schedule']:
        if n['executor']!='riscv':continue
        e=cpu[n['op']];e['nodes']+=1
        e['output_elements']+=sum(math.prod(plan['tensors'][i]['shape']) for i in n['outputs'])
        e['input_bytes']+=sum(plan['tensors'][i]['bytes'] for i in n['inputs'])
        e['output_bytes']+=sum(plan['tensors'][i]['bytes'] for i in n['outputs'])
    configs=[]
    for rows,cols,lanes in [(16,16,4),(8,16,4),(16,8,4),(8,32,4),(16,32,4),(16,16,8),(16,16,16),(32,16,4)]:
        for mode in (['legacy_4plane_mmio','affine_dma'] if (rows,cols,lanes)==(16,16,4) else ['affine_dma']):
            planes=4 if mode.startswith('legacy') else 1;cache_k=rows*16//(2 if planes==4 else 1)
            tiles=passes=weight_loads=outputs=raw_macs=slots=0
            dma_A=dma_B=dma_partials=0;dma_bursts=0
            for j in macs:
                b,m,n,k=[j[x] for x in ['batch','M','N','K']]
                mt,nt,kt=ceil(m,lanes),ceil(n,cols),ceil(k,rows)
                tiles+=b*mt*nt*kt;passes+=b*mt*nt*kt*planes
                weight_loads+=b*nt*kt*(2 if planes==4 else 1)
                outputs+=b*m*n;raw_macs+=b*m*n*k;slots+=b*mt*nt*kt*rows*cols*lanes
                groups=ceil(k,cache_k)
                # Strided, 64-bit rounded rows; no full im2col intermediate is counted.
                dma_A+=b*nt*m*ceil(k,8)*8
                dma_B+=b*k*sum(ceil(min(cols,n-n0),8)*8 for n0 in range(0,n,cols))
                dma_partials+=b*m*n*4*(2*groups-1)
                dma_bursts+=b*(k*nt+nt*m*groups+m*nt*(2*groups-1))
            if mode.startswith('legacy'):
                writes=65*len(macs)+(1+rows*(ceil(cols,4)+1))*weight_loads+(ceil(rows*lanes,4)//2+1)*passes
                reads=cols*lanes*passes
                compute_cycles=passes*(2*rows+cols+4)
            else:
                writes=20*len(macs);reads=2*len(macs)
                compute_cycles=tiles*(ceil(rows*lanes,8)+math.ceil(math.log2(rows))+4)
            byte_count=dma_A+dma_B+dma_partials
            configs.append({'id':f'{mode}_R{rows}_C{cols}_L{lanes}','mode':mode,'rows':rows,'columns':cols,'lanes':lanes,
                            'MAC_units':rows*cols*lanes,'SRAM_macros':rows*cols,'cache_K':cache_k,
                            'logical_MACs':raw_macs,'padded_slots':slots,'geometric_utilization':raw_macs/slots,
                            'matrix_tiles':tiles,'PE_passes':passes,'weight_loads':weight_loads,
                            'CPU_MMIO_writes':writes,'CPU_MMIO_reads':reads,
                            'estimated_DMA_A_bytes':dma_A,'estimated_DMA_B_bytes':dma_B,
                            'estimated_DMA_partial_and_output_bytes':dma_partials,'estimated_DMA_total_bytes':byte_count,
                            'estimated_DMA_bursts':dma_bursts,'estimated_DMA_cycles_at_1beat_per_cycle_plus_2setup':ceil(byte_count,8)+2*dma_bursts,
                            'estimated_PE_cycles_without_overlap':compute_cycles,
                            'scope':'analytical MAC traffic bound; excludes non-MAC time, DMA arbitration/backpressure, im2col gathers and full-SoC physical fanout; not end-to-end performance'})
    baseline=next(c for c in configs if c['mode'].startswith('legacy'))
    assert baseline['PE_passes']==plan['pe_schedule']['PE_passes_max']
    files=[BASE/'native/swin-tiny/c/graph.c',BASE/'native/swin-tiny/c/pe_driver.c',source/'plan.json']
    rtl=BASE/'native/audits/20261107/riscv_pe_contest/contest/task1/my_design/project/rtl'
    files+=list(rtl.glob('*'))
    data={'status':'ANALYSIS_COMPLETE_DECISION_PENDING_MEASUREMENTS','source_plan':str(source/'plan.json'),
          'model_sha256':plan['model_sha256'],'baseline':baseline,'configurations':configs,'non_MAC':dict(cpu),
          'baseline_SoC_area':3189674.284657,'baseline_SRAM_area':1805272.75,
          'reference_PE_cell_area':11108.612712,'source_sha256':{str(p.relative_to(BASE)):sha(p) for p in files if p.is_file()}}
    (ROOT/'design-space.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    lines=['# Swin 设计空间：可行候选与待测项','',
           '以下基于实际 77 个 MAC 作业推导。带宽和计算周期是分析模型，不是整图实测延迟或 ADP。',
           '', '|候选|MAC单元|SRAM数|几何利用率|PE次数|CPU MMIO读|DMA估算字节|',
           '|---|---:|---:|---:|---:|---:|---:|']
    for c in configs:lines.append('|%s|%d|%d|%.2f%%|%d|%d|%d|'%(c['id'],c['MAC_units'],c['SRAM_macros'],100*c['geometric_utilization'],c['PE_passes'],c['CPU_MMIO_reads'],c['estimated_DMA_total_bytes']))
    lines+=['','## 非 MAC 工作量','','|算子|节点|输出元素|输入/输出字节（未融合）|','|---|---:|---:|---:|']
    for op,e in sorted(cpu.items(),key=lambda p:-p[1]['output_elements']):lines.append('|%s|%d|%d|%d|'%(op,e['nodes'],e['output_elements'],e['input_bytes']+e['output_bytes']))
    lines+=['','## 决策门槛','',
            '先测原生仿射 INT8 PE 单元与 4/8/16 通道共享 SRAM 的映射面积、时序；同时测真实 RV32 非 MAC 周期。',
            '比较直接复制阵列、扩展共享权重通道，以及 DMA/片上累加/分块。精度未通过的候选不得进入最终 ADP 排名。',
            '非 MAC 使用共享 FP32 运算单元及向量/DMA 控制作为候选，量化/DQ、重排优先保持精确，LayerNorm/Softmax/GELU 要独立验证。',
            '最终选择在同一时钟、外部内存与完整 Swin 工作负载下完成面积/延迟实测后确定；当前不宣称全局最优。']
    (ROOT/'可行方案与设计空间.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'configs':len(configs),'baseline_PE_passes':baseline['PE_passes'],
                      'baseline_MMIO_reads':baseline['CPU_MMIO_reads'],'baseline_MMIO_writes':baseline['CPU_MMIO_writes'],
                      'non_MAC_output_elements':sum(x['output_elements'] for x in cpu.values())}))

if __name__=='__main__':main()
