from pathlib import Path
import hashlib,json,re
BASE=Path('/mnt/data/Ubuntu/qingruan');src=BASE/'contest/task1/libraries/sram.lib';out=BASE/'native/cache/libraries';out.mkdir(parents=True,exist_ok=True)
original=src.read_text();count=0

def normalize(m):
 global count
 body=m.group(1);rows=re.findall(r'"(?:[^"\\]|\\.)*"',body)
 rest=re.sub(r'"(?:[^"\\]|\\.)*"','',body)
 if not rows or re.sub(r'[\s,\\]','',rest):raise RuntimeError('Unexpected Liberty values syntax')
 count+=1;return 'values('+', '.join(rows)+');'
converted=re.sub(r'\bvalues\s*\((.*?)\)\s*;',normalize,original,flags=re.S)
when='when : "!ce_in & we_in";';cond='sdf_cond : "ce_in == 1\'b0 && we_in == 1\'b1";'
pattern_when=re.compile(r'(timing_sense\s*:\s*non_unate;\s*\n[ \t]*when\s*:\s*"!ce_in & we_in";)')
converted,n_conditions=pattern_when.subn(lambda m:m.group(1)+'\n                '+cond,converted)
if n_conditions!=8:raise RuntimeError('Expected exactly eight read timing conditions')

def tokens(s):
 s=re.sub(r'/\*.*?\*/','',s,flags=re.S)
 s=re.sub(r'\\\s*\n',' ',s)
 return re.findall(r'"(?:[^"\\]|\\.)*"|[A-Za-z_$][\w$]*|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|[^\s]',s)
restored=converted.replace('\n                '+cond,'')
if tokens(original)!=tokens(restored):raise RuntimeError('Original Liberty token sequence changed')
header='/* Generated legacy-LC format view. Original numeric/string tables unchanged.\n * Adds eight equivalent read-mode SDF conditions. Source remains engineering v011, NOT FOR SIGNOFF. */\n'
dst=out/'sram-lc-compat.lib';dst.write_text(header+converted)
# Supply a matching optional timing-model view for conditional read IOPATH SDF.
modelsrc=BASE/'contest/task1/libraries/sram_timing.v';model=modelsrc.read_text();n=0
pattern=re.compile(r'^([ \t]*)(\(posedge clk => \(rd_out\[\d+\] \+: rd_value\[\d+\]\)\) = \(0,0\);)$',re.M)
def adapt(m):
 global n
 n+=1
 return m.group(1)+"if (ce_in == 1'b0 && we_in == 1'b1) "+m.group(2)+'\n'+m.group(1)+'ifnone '+m.group(2)
model2=pattern.sub(adapt,model)
if n!=8:raise RuntimeError('Expected eight SRAM specify read paths')
modeldst=out/'sram-lc-compat-timing.v';modeldst.write_text('// Generated conditional-SDF read-path view; behavior and unconditional input checks unchanged.\n'+model2)
report={'status':'PASS','source':str(src),'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'compatibility_lib':str(dst),'compatibility_sha256':hashlib.sha256(dst.read_bytes()).hexdigest(),'normalized_values_groups':count,'equivalent_sdf_conditions_added':8,'original_token_sequence_preserved_after_removing_added_sdf_metadata':True,'original_numeric_tables_unchanged':True,'source_model_sha256':hashlib.sha256(modelsrc.read_bytes()).hexdigest(),'model_compatibility_view':str(modeldst),'conditional_read_paths':n,'qualification':'Original engineering v011; not signoff; this is format adaptation, not characterization'}
(out/'sram-compat-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print('SRAM_FORMAT_COMPAT_PASS values_groups='+str(count))
