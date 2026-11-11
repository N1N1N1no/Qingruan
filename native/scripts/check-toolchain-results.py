from pathlib import Path
import datetime, json, re
p=Path('/workspace/native/validation/toolchain-flow')
checks={
'dc.log':'DC_NATIVE_SYNTHESIS_PASS cells=30',
'pt.log':'PT_NATIVE_STA_PASS slack=',
'sram-pt.log':'SRAM_NATIVE_STA_PASS slack=',
'gate-run.log':'GATE_NATIVE_SDF_PASS',
'sram-run.log':'SRAM_NATIVE_SDF_PASS read_delay_gt_0p5ns_lt_2ns',
'opensta-flow.log':'OPENSTA_NATIVE_FLOW_PASS',
}
for name,marker in checks.items():
    s=(p/name).read_text()
    # Tcl scripts are echoed, so a marker alone is insufficient for shell logs.
    assert re.search(r'^'+re.escape(marker),s,re.M), (name,'PASS marker missing')
    assert not re.search(r'^(?:Error[: -]|Fatal:|[A-Z][A-Z0-9_]*_FAIL(?:[: ]|$))',s,re.M), (name,'error in log')
for name in ['sdf-annotation.log','sram-sdf-annotation.log']:
    s=(p/name).read_text()
    for item in ['errors','warnings']:
        assert re.search(r'Total '+item+r': 0\b',s), (name,item)
    assert 'MTM selection: "MAXIMUM"' in s, (name,'wrong SDF corner')
assert 'slack (MET)' in (p/'opensta-flow.log').read_text()
assert 'ABC RESULTS' in (p/'yosys-abc.log').read_text()
assert not re.search(r'^(?:ERROR:|Error:)',(p/'yosys-abc.log').read_text(),re.M)
assert len(re.findall(r'\(COND .*?\(IOPATH', (p/'sram-pt.sdf').read_text()))==8
report={'status':'PASS','verified_at':datetime.datetime.now().astimezone().isoformat(),'checks':checks,
        'sdf_corner':'MAXIMUM','standard_cell_delay_test':'output must not arrive in first 0.001ns after clock edge; must be valid by 1ns',
        'sram_delay_test':'read output remains pending after 0.5ns and matches by 2ns; write/read/preservation checked',
        'scope':'small functional environment probes; not full SoC implementation or signoff'}
(p/'flow-summary.json').write_text(json.dumps(report,indent=2)+'\n')
print('NATIVE_TOOLCHAIN_FUNCTIONAL_PASS')
