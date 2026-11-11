"""Software command composition numeric test; never stands in for RV32 RTL."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parents[1];SWIN=BASE/'native/swin-tiny';plan=ROOT/'work/fused-plan';case=ROOT/'work/aux-host';case.mkdir(parents=True,exist_ok=True)
assert not(case/'summary.json').exists(),'preserve completed proof'
files=[ROOT/'c/aux_plan.c',ROOT/'c/aux_plan.h',ROOT/'c/nonmac_plan.h',ROOT/'c/aux_host_check.c',SWIN/'c/graph.c',SWIN/'c/graph.h',SWIN/'c/pe_driver.c',SWIN/'c/pe_driver.h',plan/'graph.bin',plan/'constants.bin',Path(__file__)]
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()for p in files}
with(case/'compile.log').open('w')as f:subprocess.run(['gcc','-std=c11','-O2','-ffp-contract=off','-DSWIN_HOST','-I'+str(ROOT/'c'),'-I'+str(SWIN/'c'),ROOT/'c/aux_plan.c',ROOT/'c/aux_host_check.c',SWIN/'c/graph.c',SWIN/'c/pe_driver.c','-lm','-o',case/'check'],stdout=f,stderr=subprocess.STDOUT,check=True)
argv=[case/'check',plan/'graph.bin',plan/'constants.bin',case/'results.json']
with(case/'normal.log').open('w')as f:positive=subprocess.run(argv,stdout=f,stderr=subprocess.STDOUT)
assert positive.returncode==0 and'AUX_HOST_COMPOSITION_PASS'in(case/'normal.log').read_text(),str(case/'normal.log')
with(case/'negative.log').open('w')as f:negative=subprocess.run([*argv,'--corrupt-source'],stdout=f,stderr=subprocess.STDOUT)
assert negative.returncode==7 and'AUX_HOST_NUMERICAL_MISMATCH'in(case/'negative.log').read_text()
for p,want in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==want
proof=json.loads((case/'results.json').read_text());proof.update(source_sha256=hashes,negative_control_detected=True,hardware_executed=False,whole_model_inference=False,
 scope='82 isolated model-shaped Slice/Concat/AvgPool nodes with synthetic finite inputs, real C command producer plus host implementations of primitive contracts compared bitwise with unchanged graph.c; all writes guarded. Not an RTL test, model accuracy result or inference performance measurement.')
(case/'summary.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps({k:v for k,v in proof.items()if k!='source_sha256'},indent=2),flush=True)
