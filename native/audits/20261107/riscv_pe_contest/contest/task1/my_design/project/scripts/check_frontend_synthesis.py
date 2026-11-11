#!/usr/bin/env python3
"""Generic synthesis with reference SRAM blackboxes, not technology mapping."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from fold_verilog_literal_slices import fold_literal_slices

ROOT = Path(__file__).resolve().parents[1]
CELL = 'SRAM_6T_CORE_16x8_MC_TB'
PORTS = {'wd_in': ('input',8), 'rd_out': ('output',8), 'addr_in': ('input',4),
         'ce_in': ('input',1), 'we_in': ('input',1), 'clk': ('input',1)}

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
def run(cmd, log, stdout=None):
    print('RUN:', ' '.join(map(str,cmd)),flush=True)
    with log.open('w',encoding='utf-8') as err:
        if stdout is not None:
            with stdout.open('w',encoding='utf-8') as out:
                r=subprocess.run(list(map(str,cmd)),cwd=ROOT,stdout=out,stderr=err)
        else:r=subprocess.run(list(map(str,cmd)),cwd=ROOT,stdout=err,stderr=subprocess.STDOUT)
    if r.returncode: raise RuntimeError(f'Exit {r.returncode}: inspect {log}')

def build_ys_script(rel):
    # Diagnostic 'check' before synth only records dead logic left by the disabled
    # DCCM/ICCM/ICACHE config; 'check -assert' judges the optimized netlist after synth.
    return '\n'.join([
        f'read_verilog {rel}/soc_sv2v.v',
        'hierarchy -check -top soc_top','proc','flatten','check',
        'synth -top soc_top -flatten -noabc','hierarchy -check -top soc_top',
        'check -assert','stat',
        f'write_verilog -noattr {rel}/soc_generic.v',f'write_json {rel}/soc_generic.json',''])

def parse_pre_synth_warnings(log_text):
    pre=log_text.split('Executing SYNTH',1)[0]
    return {'undriven_wire_bits':pre.count('used but has no driver'),
            'logic_loops':pre.count('found logic loop'),
            'synth_marker_found':'Executing SYNTH' in log_text}

def audit(data):
    errors=[];mods=data['modules'];top=mods.get('soc_top',{})
    if not top: return {'pass':False,'errors':['Missing soc_top']}
    macro=mods.get(CELL,{})
    got={n:(p['direction'],len(p['bits'])) for n,p in macro.get('ports',{}).items()}
    if got!=PORTS:errors.append('SRAM blackbox interface mismatch')
    if str(macro.get('attributes',{}).get('blackbox','0')).strip('0')!='1':
        errors.append('SRAM definition is not marked blackbox')
    if macro.get('cells') or macro.get('processes') or macro.get('memories'):
        errors.append('SRAM blackbox contains implementation')
    cells=top.get('cells',{});ram=[(n,c) for n,c in cells.items() if c['type']==CELL]
    if len(ram)!=256:errors.append(f'Expected 256 SRAM cells, got {len(ram)}')
    for name,c in cells.items():
        if c['type']!=CELL and not c['type'].startswith('$_'):
            errors.append('Unexpected residual cell '+name+': '+c['type'])
    consumers=set()
    for c in cells.values():
        for port,bits in c.get('connections',{}).items():
            if c.get('port_directions',{}).get(port)=='input':
                consumers.update(b for b in bits if isinstance(b,int))
    for p in top.get('ports',{}).values():
        if p['direction']=='output':consumers.update(p['bits'])
    for name,c in ram:
        for port,(_,width) in PORTS.items():
            if len(c.get('connections',{}).get(port,[]))!=width:
                errors.append(name+': wrong width for '+port)
        if not set(c['connections'].get('rd_out',[])) & consumers:
            errors.append(name+': read output unused')
    if top.get('processes') or top.get('memories'):
        errors.append('Residual processes or inferred memory after synthesis')
    return {'pass':not errors,'errors':errors,'sram_blackbox_count':len(ram),
            'generic_logic_cell_count':len(cells)-len(ram)}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sv2v',default='sv2v');ap.add_argument('--yosys',default='yosys')
    a=ap.parse_args();os.chdir(ROOT)
    out=ROOT/'build/frontend_synth'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    out.mkdir(parents=True,exist_ok=False)
    args=[];files=set();has_synthesis=False
    for line in (ROOT/'build/synth.f').read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if line.startswith('+incdir+'):
            d=Path(line[8:]);args+=['-I'+str(d)]
            files.update(p for p in d.rglob('*') if p.is_file() and p.suffix in ('.v','.sv','.vh','.svh'))
        elif line.startswith('+define+'):
            define=line[8:]
            if define=='SYNTHESIS':has_synthesis=True
            if define.split('=')[0] in ('PE_LEGACY_RAM','PE_REFERENCE_MACRO'):
                raise RuntimeError('RAM mode must not be embedded in shared source list')
            args+=['-D'+define]
        elif not line or line.startswith(('//','+libext+')):continue
        elif line.startswith(('+','-')):raise RuntimeError('Unsupported source-list entry: '+line)
        else:
            p=Path(line)
            if p.name in ('rfsp16x8_legacy.v','sram_reference_model.v','axi_ram_model.sv','tb_soc.sv','sram_blackbox.v'):
                raise RuntimeError('Simulation/blackbox definition leaked into design list: '+line)
            args.append(line);files.add(p)
    if not has_synthesis:raise RuntimeError('Missing SYNTHESIS define')
    stub=ROOT/'synth/sram_blackbox.v'
    files.update((stub,ROOT/'tb/sram_reference_model.v',Path(__file__),
                  ROOT/'scripts/fold_verilog_literal_slices.py',ROOT/'Makefile.frontend'))
    before={str(p.resolve()):sha(p) for p in sorted(files,key=str)}
    save(out/'source_hashes.json',before)
    raw = out/'soc_sv2v.raw.v'
    processed = out/'soc_sv2v.v'
    run([a.sv2v,*args,'-DPE_REFERENCE_MACRO',str(stub)],out/'sv2v.log',raw)
    folded, edits = fold_literal_slices(raw.read_text(encoding='utf-8'))
    if fold_literal_slices(folded) != (folded, []):
        raise RuntimeError('Literal folding is not idempotent')
    processed.write_text(folded,encoding='utf-8')
    save(out/'literal_slice_folds.json', {
        'scope': 'known sized literal, constant in-range slice only; no RTL/config changes',
        'raw_sha256': sha(raw), 'processed_sha256': sha(processed),
        'helper_sha256': sha(ROOT/'scripts/fold_verilog_literal_slices.py'),
        'replacement_count': len(edits), 'edits': edits})
    print('LITERAL_SLICE_FOLDS:',len(edits),flush=True)
    rel=out.relative_to(ROOT).as_posix()
    (out/'check.ys').write_text(build_ys_script(rel),encoding='utf-8')
    run([a.yosys,'-s',str(out/'check.ys')],out/'yosys.log')
    pre_warnings=parse_pre_synth_warnings((out/'yosys.log').read_text(encoding='utf-8'))
    report=audit(json.loads((out/'soc_generic.json').read_text(encoding='utf-8')))
    if before!={str(p.resolve()):sha(p) for p in sorted(files,key=str)}:
        report['errors'].append('Sources changed during run');report['pass']=False
    report.update(scope='generic synthesis only; reference SRAM interface kept blackbox; no Liberty mapping, area, timing, or equivalence claim',
                  pre_synth_check_warnings=pre_warnings,
                  check_assert_point='post-synthesis optimized netlist; pre-synth diagnostic check warnings are recorded, not asserted',
                  generic_netlist_sha256=sha(out/'soc_generic.v'))
    save(out/'report.json',report)
    print('Report:',out/'report.json')
    if not report['pass']:raise RuntimeError('; '.join(report['errors'][:15]))
    print('FRONTEND_SYNTHESIS_CHECK_PASS: 256 SRAM blackboxes retained')

if __name__=='__main__':main()
