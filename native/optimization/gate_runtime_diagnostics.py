"""Strict diagnostic review, including the intentional CPU failure return.

The corruption test deliberately calls one frozen RAM-model $fatal. Only that
exact source location, hierarchy, code and one occurrence may be exempted.
"""
from pathlib import Path
import re


def checked_runtime_text(case, stage, text, local):
    assert stage in ['normal', 'corrupt']
    checked = text
    expected = None
    if stage == 'corrupt':
        ram = case / 'inputs/axi_ram_model.sv'
        sites = [i + 1 for i, line in enumerate(ram.read_text().splitlines())
                 if '$fatal(1,"COMPLETE_SOC_CPU_RETURN_FAIL code=%08x",done_value)' in line]
        assert len(sites) == 1, 'Expected CPU-return fatal source must be unique'
        pattern = (r'(?m)^Fatal: "([^"]+)", (\d+): tb_soc\.dmem\.core: at time (\d+) fs\n'
                   r'COMPLETE_SOC_CPU_RETURN_FAIL code=bad00120$')
        matches = list(re.finditer(pattern, text))
        assert len(matches) == 1, 'Exactly one expected corruption CPU-return fatal required'
        match = matches[0]
        assert Path(local(match[1])).resolve() == ram.resolve() and int(match[2]) == sites[0]
        assert int(match[3]) > 0
        checked = text[:match.start()] + text[match.end():]
        assert text.count('COMPLETE_SOC_NEGATIVE_PASS CPU_detected_corrupted_byte=7') == 1
        assert text.count('COMPLETE_SOC_CPU_RETURN_FAIL code=bad00120') == 1
        assert 'COMPLETE_SOC_CPU_RETURN_PASS' not in text
        expected = {'file': str(ram), 'line': sites[0], 'instance': 'tb_soc.dmem.core',
                    'simulation_time_fs': int(match[3]), 'CPU_return_code': 'bad00120',
                    'intentional_fatal_occurrences': 1}
    assert not re.search(r'(?im)^\s*(?:Warning|Lint|Error|Fatal)(?:-|:)|timing[_ ]violation|TIMEOUT|COMPLETE_SOC_FAIL', checked), 'Unexpected runtime diagnostic'
    return expected
