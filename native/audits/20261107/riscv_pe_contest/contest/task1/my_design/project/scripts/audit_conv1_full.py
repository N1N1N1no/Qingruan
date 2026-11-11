#!/usr/bin/env python3
"""Independently recompute all Conv1 results via direct kernel-slice sums."""
import argparse,hashlib,json,re
from pathlib import Path
import numpy as np
from PIL import Image

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def tile(values):return values.reshape(2,16,64,2,64,2).transpose(0,2,4,1,3,5).reshape(8192,64)
def readbin(root,name,dtype,shape):return np.fromfile(root/name,dtype=dtype).reshape(shape)
def math_audit(root, return_data=False):
 d=json.loads((root/'conv1_full.json').read_text())
 for name,meta in d['provenance'].items():
  if 'path' in meta:assert sha(meta['path'])==meta['sha256'],name
 for name,meta in d['artifacts'].items():
  p=root/name;assert p.stat().st_size==meta['bytes'] and sha(p)==meta['sha256'],name
 with Image.open(d['provenance']['image']['path']) as image:
  rgb=np.asarray(image.convert('RGB').resize((130,130),Image.Resampling.BILINEAR))
 q=np.rint((rgb.astype(np.float32)/np.float32(255)).astype(np.float64)*127).clip(0,127).astype(np.int64).transpose(2,0,1)
 assert hashlib.sha256(q.astype('<i8').tobytes()).hexdigest()==d['provenance']['quantized_image_chw_int64_le']['sha256']
 with np.load(d['provenance']['quantized_model']['path'],allow_pickle=False) as z:
  w=z['conv1_weight_q'].astype(np.int64);hw=z['conv1_weight_hw'];bias=z['conv1_bias_q'].astype(np.int64)
  mult=int(z['conv1_requant_mult']);shift=int(z['conv1_requant_shift'])
 assert d['requant_multiplier']==mult and d['requant_shift']==shift==24
 encoded=np.where(w<0,128+(-w),w).astype(np.uint8);assert np.array_equal(hw,encoded)
 assert np.array_equal(readbin(root,'input.bin','u1',(3,130,130)),q)
 assert np.array_equal(readbin(root,'weights_hw.bin','u1',(27,32)),hw.reshape(32,27).T)
 assert np.array_equal(readbin(root,'bias.bin','<i4',(32,)),bias)
 partial=[np.zeros((32,128,128),dtype=np.int64) for _ in range(2)]
 k=0
 for channel in range(3):
  for ky in range(3):
   for kx in range(3):
    partial[k//16]+=w[:,channel,ky,kx,None,None]*q[channel,ky:ky+128,kx:kx+128]
    k+=1
 raw=partial[0]+partial[1]+bias[:,None,None]
 assert np.array_equal(readbin(root,'partials.bin','<i4',(8192,2,64)),np.stack([tile(x) for x in partial],axis=1))
 assert np.array_equal(readbin(root,'raw.bin','<i4',(8192,64)),tile(raw))
 def quant(x):
  quotient,remainder=divmod(max(0,int(x))*mult,1<<shift)
  return min(127,quotient+int(remainder>=(1<<(shift-1))))
 quantized=np.fromiter((quant(x) for x in raw.flat),dtype=np.uint8,count=raw.size).reshape(32,128,128)
 pooled=np.maximum.reduce([quantized[:,0::2,0::2],quantized[:,0::2,1::2],quantized[:,1::2,0::2],quantized[:,1::2,1::2]])
 assert np.array_equal(readbin(root,'requant.bin','u1',(8192,64)),tile(quantized))
 assert np.array_equal(readbin(root,'pool.bin','u1',(32,64,64)),pooled)
 header=(root/'conv1_full_data.h').read_text();assert 'GOLDEN' not in header and 'expected' not in header.lower()
 for name,expected in [('C1F_INPUT_HW',q),('C1F_WEIGHT_HW',hw.reshape(32,27).T),('C1F_BIAS',bias)]:
  match=re.search(name+r'\s*\[[^=]+?=\s*\{(.*?)\};',header,re.S);assert match,name
  values=[int(value,0) for value in re.findall(r'-?0x[0-9a-fA-F]+|-?\d+',match[1])]
  assert values==expected.reshape(-1).tolist(),name
 result={'status':'PASS','scope':'independent full-image mathematical oracle audit; CPU execution audited separately','sample':d['sample_relative_path'],'input_bytes':50700,'weight_bytes':864,'partial_values':1048576,'raw_values':524288,'requant_values':524288,'pool_values':131072,'raw_min':int(raw.min()),'raw_max':int(raw.max()),'pool_sha256':sha(root/'pool.bin'),'checks':{'source_and_artifact_hashes':True,'independent_pil_numpy_preprocessing':True,'all27_kernel_terms_and_partial_batches':True,'all_scalar_divmod_rounding_and_pooling':True,'header_contains_exact_input_weight_bias_only':True}}
 if return_data:
  return result, np.stack([tile(x) for x in partial],axis=1).astype('<i4'), pooled
 return result


def trace_rows(path):
 lines=path.read_text().splitlines()
 assert lines and lines[0]=='CONV1_FULL_TILE_TRACE_V1', 'Invalid trace header'
 rows=[]
 for line in lines[1:]:
  fields=line.split('\t');item={'cycles':int(fields[0]),'event':fields[1]}
  item.update({key:int(value) for key,value in (field.split('=',1) for field in fields[2:])})
  rows.append(item)
 return rows


def output_indices(start,count):
 for tile_id in range(start,start+count):
  group,position=divmod(tile_id,4096)
  for column in range(16):
   yield (group*16+column)*4096+position


def case_audit(case):
 summary=json.loads((case/'summary.json').read_text())
 assert summary['status'] in ('RUNNING','PASS'),summary.get('error')
 mathematical,partial_gold,pool_gold=math_audit(case/'vector',True)
 records=summary['cases'];by_name={record['case']:record for record in records}
 shard_count=int(summary.get('shards',1))
 assert shard_count>=1
 normal_names={'normal'} if shard_count==1 else {f'normal_{index}' for index in range(shard_count)}
 fixed_names={'preflight_start','preflight_group_boundary','preflight_last','wrong_weight','wrong_pool'}
 assert len(records)==len(by_name)==5+shard_count and set(by_name)==fixed_names|normal_names
 assert all(record.get('pass') for record in records)
 validations=[]
 for record in records:
  name=record['case'];start=record['tile_start'];count=record['tile_count']
  log=(case/(name+'_simulation.log')).read_text()
  trace=case/'rtl'/(name+'_tiles.tsv')
  partial_dump=case/'rtl'/(name+'_partials.bin')
  if name.startswith('wrong_'):
   assert 'Fatal:' in log and 'CONV1_FULL_MONITOR_PASS' not in log and 'CONV1_FULL_CPU_RETURN_PASS' not in log
   if name=='wrong_weight':
    match=re.search(r'CONV1_FULL_MONITOR_FAIL weight configuration index=(\d+) addr=([0-9a-f]+) data=([0-9a-f]+) expected_addr=([0-9a-f]+) expected_data=([0-9a-f]+)',log)
    assert match and int(match[1])==66 and int(match[2],16)==int(match[4],16)==0x90000020
    assert int(match[3],16)==(int(match[5],16)^1)
    hardware=np.fromfile(case/'vector/weights_hw.bin',dtype='u1').reshape(27,32)
    correct=sum(int(hardware[0,i])<<(8*i) for i in range(4))
    assert int(match[5],16)==correct
    assert partial_dump.stat().st_size==0
    validations.append({'case':name,'status':'PASS','reason':'only first weight bit changed; independently rejected during preload'})
   else:
    match=re.search(r'CONV1_FULL_MONITOR_FAIL CPU output index=(\d+) tile=(\d+) column=(\d+) actual=([0-9a-f]+) expected=([0-9a-f]+) reads=(\d+) required_reads=(\d+)',log)
    assert match and tuple(int(match[i]) for i in (1,2,3))==(0,0,0)
    expected=int(pool_gold.reshape(-1)[0])
    assert int(match[4],16)==(expected^1) and int(match[5],16)==expected
    assert int(match[6])==int(match[7])==68
    observed=np.fromfile(partial_dump,dtype='<i4')
    assert np.array_equal(observed,partial_gold[0].reshape(-1)[:68])
    assert not trace_rows(trace)
    validations.append({'case':name,'status':'PASS','reason':'first CPU pooled output changed by one bit after 68 correct PE reads'})
   continue
  assert log.count('CONV1_FULL_MONITOR_PASS')==1 and log.count('CONV1_FULL_CPU_RETURN_PASS')==1
  assert not re.search(r'CONV1_FULL_.*FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|^\s*(?:Error-\[|Fatal:|FATAL:)',log,re.M)
  assert len(re.findall(r'^REFERENCE_SRAM_BEHAVIORAL_MODEL instance=',log,re.M))==256
  for instance in ('imem','dmem'):
   assert f'AXI_RESPONSE_DELAY_NS=1.000 instance=tb_soc.{instance}' in log
   assert f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{instance}' in log
  observed_partials=np.fromfile(partial_dump,dtype='<i4')
  assert np.array_equal(observed_partials,partial_gold[start:start+count].reshape(-1)),name+' actual partial mismatch'
  observed=np.fromfile(case/'rtl'/(name+'_pool.bin'),dtype='u1')
  assert observed.size==131072
  selected=np.fromiter(output_indices(start,count),dtype=np.int64,count=count*16)
  expected=np.zeros(131072,dtype=np.uint8)
  expected[selected]=pool_gold.reshape(-1)[selected]
  assert np.array_equal(observed,expected),name+' actual CPU output mismatch'
  rows=trace_rows(trace);tiles=[row for row in rows if row['event']=='TILE'];done=[row for row in rows if row['event']=='COMPLETE']
  assert len(rows)==count+1 and len(tiles)==count and len(done)==1
  for index,row in enumerate(tiles):
   assert row['index']==start+index and row['completed']==index+1
   assert row['output_bytes']==16*(index+1) and row['pe_reads']==128*(index+1)
   assert row['pe_writes']==389+34*(index+1)
   if index:assert row['cycles']>tiles[index-1]['cycles']
  complete=done[0]
  assert (complete['pe_reads'],complete['pe_writes'],complete['pe_responses'],complete['output_bytes'],complete['tiles'])==(128*count,389+34*count,389+34*count,16*count,count)
  assert record['pe_reads_checked']==count*128 and record['pe_writes_checked']==389+34*count
  assert record['output_bytes_checked']==count*16
  assert sha(partial_dump)==record['observed_partials_sha256']
  assert sha(case/'rtl'/(name+'_pool.bin'))==record['observed_sha256']
  assert sha(trace)==record['trace_sha256']
  validations.append({'case':name,'status':'PASS','tile_start':start,'tile_count':count,
                      'actual_partial_values':count*128,'actual_output_bytes':count*16,
                      'pe_configuration_writes':389+34*count,'cycles':complete['cycles'],
                      'actual_partial_sha256':sha(partial_dump),'actual_output_sha256':sha(case/'rtl'/(name+'_pool.bin'))})
 # The cases all execute the same elaborated CPU/PE simulator.  Golden files
 # are separate plusargs, and no CPU golden table is present in the header.
 simulator=case/'rtl/simv';assert sha(simulator)==summary['simulator_sha256']
 commands=[record['argv'] for record in summary['commands'] if record['argv'][0]==str(simulator)]
 assert len(commands)==5+shard_count
 assert len({next(arg for arg in command if arg.startswith('+MEM_HEX=')) for command in commands})==5+shard_count
 for option in ('+INPUT_FILE=','+WEIGHT_FILE=','+PARTIAL_FILE=','+POOL_FILE='):
  assert len({next(arg for arg in command if arg.startswith(option)) for command in commands})==1
 for path,digest in summary['frozen_source_sha256'].items():assert sha(case/'source'/path)==digest,path
 for path,digest in summary['input_sha256'].items():assert sha(path)==digest,path
 for path,digest in summary.get('artifacts_sha256',{}).items():assert sha(case/path)==digest,path
 # Check the baseline source manifest independently of the runner's flag.
 rtl_live=next(Path(path) for path in summary['input_sha256'] if path.endswith('/project/rtl/soc_top.sv'))
 task=rtl_live.parents[3]
 starter=json.loads((task/'SOURCE_MANIFEST.json').read_text())['base_files']
 baseline=[]
 for name,digest in starter.items():
  if name.startswith('project/rtl/') or name=='project/sw/pe_smoke.c':
   assert summary['frozen_source_sha256'][name]==digest and sha(case/'source'/name)==digest
   baseline.append(name)
 monitor=(case/'source/project/tests/soc_conv1_full_monitor.sv').read_text()
 assert "32'h600dcafe" in monitor and 'completion_this_cycle = 1' in monitor
 full=summary['tile_start']==0 and summary['tile_count']==8192
 normal_parts=sorted((record for record in validations if record['case'] in normal_names),key=lambda record:record['tile_start'])
 assert len(normal_parts)==shard_count
 next_start=summary['tile_start']
 merged_actual_partials=[]
 merged_actual_pool=np.zeros(131072,dtype=np.uint8)
 covered=np.zeros(131072,dtype=np.uint8)
 for record in normal_parts:
  assert record['tile_start']==next_start and record['tile_count']>0
  next_start+=record['tile_count']
  name=record['case']
  merged_actual_partials.append(np.fromfile(case/'rtl'/(name+'_partials.bin'),dtype='<i4'))
  selected=np.fromiter(output_indices(record['tile_start'],record['tile_count']),dtype=np.int64,count=record['tile_count']*16)
  assert not covered[selected].any()
  covered[selected]=1
  values=np.fromfile(case/'rtl'/(name+'_pool.bin'),dtype='u1')
  merged_actual_pool[selected]=values[selected]
 assert next_start==summary['tile_start']+summary['tile_count']
 assert int(covered.sum())==summary['tile_count']*16
 observed_partial=np.fromfile(case/'rtl/normal_partials.bin',dtype='<i4')
 assert np.array_equal(observed_partial,np.concatenate(merged_actual_partials))
 assert np.array_equal(observed_partial,partial_gold[summary['tile_start']:next_start].reshape(-1))
 observed_pool=np.fromfile(case/'rtl/normal_pool.bin',dtype='u1')
 assert np.array_equal(observed_pool,merged_actual_pool)
 assert np.array_equal(observed_pool[covered.astype(bool)],pool_gold.reshape(-1)[covered.astype(bool)])
 assert sha(case/'rtl/normal_pool.bin')==summary['observed_output_sha256']
 simulation_mode=summary.get('simulation_mode','one CPU reset over the requested contiguous range')
 normal={'status':'PASS','tile_start':summary['tile_start'],'tile_count':summary['tile_count'],
         'shards':shard_count,'simulation_mode':simulation_mode,
         'actual_partial_values':summary['tile_count']*128,
         'actual_output_bytes':summary['tile_count']*16,
         'pe_configuration_writes':sum(record['pe_configuration_writes'] for record in normal_parts),
         'actual_partial_sha256':sha(case/'rtl/normal_partials.bin'),
         'actual_output_sha256':sha(case/'rtl/normal_pool.bin'),
         'per_shard_cycles':[{'case':record['case'],'tile_start':record['tile_start'],
                              'tile_count':record['tile_count'],'cycles':record['cycles']} for record in normal_parts]}
 if shard_count==1:
  normal['cycles']=normal_parts[0]['cycles']
 execution_scope='actual CPU/AXI/PE RTL'
 if shard_count>1:
  execution_scope+='; coverage from independent CPU resets over contiguous ranges, not one continuous inference measurement'
 return {'status':'PASS','scope':('complete first Conv1/ReLU/Q24/pool layer' if full else 'specified first-layer tile range')+'; '+execution_scope+'; no SDF or remaining CNN layers',
         'case':str(case),'full_first_layer':full,'mathematical_oracle':mathematical,
         'execution_cases':validations,'normal':normal,'checks':{
          'all_actual_pe_partial_values_match_independent_math':True,
          'all_selected_actual_cpu_pool_output_bytes_match_independent_math':True,
          'tile_coverage_counts_order_and_no_duplicates':True,
          'normal_ranges_cover_requested_tiles_contiguously_without_overlap':True,
          'merged_partial_and_pool_files_reconstructed_from_actual_shard_dumps':True,
          'positive_preflights_cross_channel_group_and_last_tile':True,
          'negative_weight_and_pool_controls_rejected_for_expected_reason':True,
          'same_simulator_and_host_golden_files_separate_from_firmware':True,
          'all_live_frozen_source_and_recorded_artifact_hashes_match':True,
          'rtl_and_original_smoke_match_starter':baseline,
          'cpu_completion_magic_and_256_sram_models':True}}


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--case',type=Path,required=True)
 parser.add_argument('--output',type=Path)
 args=parser.parse_args()
 case=args.case.resolve(strict=True)
 report=args.output or case/'independent_validation_summary.json'
 try:
  result=case_audit(case)
 except (AssertionError,ValueError,OSError,KeyError) as error:
  result={'status':'FAIL','case':str(case),'error':str(error),'audit':'independent full Conv1 CPU/oracle audit'}
  report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
  raise
 report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
