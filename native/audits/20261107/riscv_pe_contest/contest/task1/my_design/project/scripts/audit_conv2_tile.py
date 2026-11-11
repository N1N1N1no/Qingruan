#!/usr/bin/env python3
"""Independently audit actual CPU Pool1 -> a 64-channel Conv2/Pool2 region."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def packed(values):
    return sum(int(value) << (8 * index) for index, value in enumerate(values))


def read_trace(path):
    lines = path.read_text().splitlines()
    assert lines and lines[0] == 'CONV2_TILE_TRACE_V1'
    events = []
    for line in lines[1:]:
        fields = line.split('\t')
        event = dict(cycles=int(fields[0]), event=fields[1])
        for field in fields[2:]:
            key, value = field.split('=', 1)
            event[key] = int(value, 16 if key in ('addr', 'value', 'bus') else 10)
        events.append(event)
    return events


def independent_math(vector_dir):
    data = json.loads((vector_dir / 'conv2_tile.json').read_text())
    for name, metadata in data['provenance'].items():
        assert sha(metadata['path']) == metadata['sha256'], name
    for name, metadata in data['artifacts'].items():
        assert sha(vector_dir / name) == metadata['sha256'], name
        assert (vector_dir / name).stat().st_size == metadata['bytes'], name
    source = Path(data['source_conv1_result'])
    source_summary = json.loads((source / 'summary.json').read_text())
    source_audit = json.loads((source / 'independent_validation_summary.json').read_text())
    source_vector = json.loads((source / 'vector/conv1_full.json').read_text())
    assert source_summary['status'] == source_audit['status'] == 'PASS'
    assert source_summary['full_first_layer'] is source_audit['full_first_layer'] is True
    assert source_summary['cpu_included'] is True
    assert source_summary['tile_start'] == 0 and source_summary['tile_count'] == 8192
    source_actual = source / 'rtl/normal_pool.bin'
    assert Path(source_summary['observed_output']).resolve() == source_actual.resolve()
    assert sha(source_actual) == source_summary['observed_output_sha256'] == source_audit['normal']['actual_output_sha256'] == data['source_input_sha256']
    assert source_audit['normal']['actual_output_bytes'] == 131072
    assert (vector_dir / 'conv2_input.bin').read_bytes() == source_actual.read_bytes()
    for name, digest in source_summary['frozen_source_sha256'].items():
        assert sha(source / 'source' / name) == digest, name
    for name, digest in source_summary['artifacts_sha256'].items():
        assert sha(source / name) == digest, name
    model_path = Path(data['provenance']['quantized_model']['path'])
    assert sha(model_path) == source_vector['provenance']['quantized_model']['sha256']
    if 'source_conv1_pointer' in data['provenance']:
        pointer = json.loads(Path(data['provenance']['source_conv1_pointer']['path']).read_text())
        assert pointer['status'] == 'PASS' and pointer['full_first_layer'] is True
        assert Path(pointer['result']).resolve() == source.resolve()
        assert pointer['summary_sha256'] == sha(source / 'summary.json')
        assert pointer['independent_audit_sha256'] == sha(source / 'independent_validation_summary.json')
        assert pointer['results_sha256'] == sha(source / 'RESULTS.md')
        assert pointer['actual_output_sha256'] == sha(source_actual)
    image = np.fromfile(source_actual, dtype='u1').reshape(32, 64, 64).astype(np.int64)
    assert image.max() <= 127
    with np.load(model_path, allow_pickle=False) as archive:
        weights = archive['conv2_weight_q'].astype(np.int64)
        hardware = archive['conv2_weight_hw']
        biases = archive['conv2_bias_q'].astype(np.int64)
        multiplier = int(archive['conv2_requant_mult'])
        shift = int(archive['conv2_requant_shift'])
    assert multiplier == data['requant_multiplier'] == 21410
    assert shift == data['requant_shift'] == 24
    assert weights.shape == (64, 32, 3, 3)
    assert np.array_equal(hardware, np.where(weights < 0, 128 - weights, weights).astype(np.uint8))
    weight_by_k = weights.reshape(64, 288).T
    hardware_by_k = hardware.reshape(64, 288).T
    assert np.array_equal(np.fromfile(vector_dir / 'weights_hw.bin', dtype='u1').reshape(288, 64), hardware_by_k)
    assert np.array_equal(np.fromfile(vector_dir / 'bias.bin', dtype='<i4'), biases)
    row, col = data['row'], data['col']
    assert 0 <= row <= 60 and 0 <= col <= 60 and row % 2 == col % 2 == 0
    positions = [(row, col), (row, col + 1), (row + 1, col), (row + 1, col + 1)]
    assert data['lane_positions'] == [list(position) for position in positions]
    partials = np.zeros((4, 18, 64), dtype=np.int64)
    window = []
    # Direct 288-term accumulation instead of the generator's matrix products.
    for channel in range(32):
        for ky in range(3):
            for kx in range(3):
                term = len(window)
                values = np.array([image[channel, r + ky, c + kx] for r, c in positions], dtype=np.int64)
                window.append(values)
                partials[:, term // 16, :] += (weights[:, channel, ky, kx, None] * values[None, :]).reshape(4, 64)
    window = np.asarray(window)
    raw = (partials.sum(axis=1).reshape(64, 4) + biases[:, None]).reshape(256)
    def quantize(value):
        whole, remainder = divmod(max(0, int(value)) * multiplier, 1 << shift)
        return min(127, whole + int(remainder >= (1 << (shift - 1))))
    quantized = np.array([quantize(value) for value in raw], dtype=np.int64)
    pool = np.array([max(quantized[start:start + 4]) for start in range(0, 256, 4)], dtype=np.int64)
    all_results = np.concatenate([partials.reshape(-1), raw, quantized, pool]).astype('<i4')
    assert np.array_equal(np.fromfile(vector_dir / 'partials.bin', dtype='<i4').reshape(4, 18, 64), partials)
    assert np.array_equal(np.fromfile(vector_dir / 'raw.bin', dtype='<i4'), raw)
    assert np.array_equal(np.fromfile(vector_dir / 'requant.bin', dtype='u1'), quantized)
    assert np.array_equal(np.fromfile(vector_dir / 'pool.bin', dtype='u1'), pool)
    assert np.array_equal(np.fromfile(vector_dir / 'cpu_results.bin', dtype='<i4'), all_results)
    assert data['pool_golden'] == pool.tolist()
    header = (vector_dir / 'conv2_tile_data.h').read_text()
    assert 'GOLDEN' not in header and 'expected' not in header.lower()
    for name, expected in [('C2T_INPUT_HW', image), ('C2T_WEIGHT_HW', hardware_by_k), ('C2T_BIAS', biases)]:
        match = re.search(name + r'\s*\[[^=]+?=\s*\{(.*?)\};', header, re.S)
        assert match, name
        values = [int(value, 0) for value in re.findall(r'-?0x[0-9a-fA-F]+|-?\d+', match[1])]
        assert values == expected.reshape(-1).tolist(), name
    fault = int(data['fault_term'])
    assert fault == int(np.flatnonzero((window[:, 0] != 0) & (weight_by_k[:, 0] != 0))[0])
    assert re.search(r'#define C2T_FAULT_TERM\s+' + str(fault) + r'\b', header)
    return data, source_summary, window, hardware_by_k, partials, all_results


def audit_case(case):
    summary = json.loads((case / 'summary.json').read_text())
    assert summary['status'] in ('RUNNING', 'PASS')
    data, source_summary, window, hardware, partials, expected = independent_math(case / 'vector')
    writes = [(0x90000000, 0), (0x90000008, 65535)]
    writes += [(0x90000200 + 4 * index, 0) for index in range(64)]
    for group in range(4):
        for batch in range(18):
            for row in range(16):
                term = batch * 16 + row
                for word in range(4):
                    writes.append((0x90000020 + 4 * word, packed(hardware[term, group * 16 + word * 4:group * 16 + word * 4 + 4])))
                writes.append((0x90000010, 1 << row))
            for row in range(16):
                writes.append((0x90000100 + 4 * row, packed(window[batch * 16 + row])))
    assert len(writes) == 6978
    fault_batch, fault_row = divmod(data['fault_term'], 16)
    failed_write = {'wrong_weight': 66 + fault_batch * 96 + fault_row * 5,
                    'wrong_input': 66 + fault_batch * 96 + 80 + fault_row}
    negative_requested = summary['negative_controls_requested']
    names = {'normal', 'wrong_weight', 'wrong_input', 'wrong_requant', 'wrong_pool'} if negative_requested else {'normal'}
    records = summary['cases']
    assert len(records) == len(names) and {record['case'] for record in records} == names
    assert all(record.get('pass') for record in records)
    reports = []
    for record in records:
        name = record['case']
        trace = case / 'rtl' / (name + '_trace.tsv')
        rows = read_trace(trace)
        log = (case / (name + '_simulation.log')).read_text()
        expected_writes = failed_write[name] if name in failed_write else 6978
        expected_reads = fault_batch * 64 if name in failed_write else 4608
        expected_cpu = expected_reads if name in failed_write else {'normal': 5184, 'wrong_requant': 4864, 'wrong_pool': 5120}[name]
        observed_writes = [row for row in rows if row['event'] == 'PE_WRITE']
        assert len(observed_writes) == expected_writes
        for index, (row, (address, value)) in enumerate(zip(observed_writes, writes)):
            assert (row['index'], row['addr'], row['value']) == (index, address, value), (name, index)
        observed_reads = [row for row in rows if row['event'] == 'PE_RESULT']
        assert len(observed_reads) == expected_reads
        for index, row in enumerate(observed_reads):
            pair = (index // 2) * 2
            bus = ((int(expected[pair + 1]) & 0xffffffff) << 32) | (int(expected[pair]) & 0xffffffff)
            assert (row['index'], row['addr'], row['value'], row['bus']) == (index, 0x90000400 + 4 * (index % 64), int(expected[index]) & 0xffffffff, bus)
        cpu_rows = [row for row in rows if row['event'] == 'CPU_RESULT']
        assert len(cpu_rows) == expected_cpu
        address = int(record['result_address'], 16)
        for index, row in enumerate(cpu_rows):
            assert (row['index'], row['addr'], row['value']) == (index, address + 4 * index, int(expected[index]) & 0xffffffff)
        completed = [row for row in rows if row['event'] == 'COMPLETE']
        assert len(rows) == expected_writes + expected_reads + expected_cpu + (1 if name == 'normal' else 0)
        assert sha(trace) == record['trace_sha256']
        if name == 'normal':
            assert len(completed) == 1
            done = completed[0]
            assert (done['pe_reads'], done['pe_writes'], done['pe_responses'], done['cpu_results']) == (4608, 6978, 6978, 5184)
            actual = np.fromfile(record['observed_results'], dtype='<i4')
            assert np.array_equal(actual, expected)
            assert sha(record['observed_results']) == record['observed_results_sha256']
            assert log.count('CONV2_TILE_MONITOR_PASS') == 1 and log.count('CONV2_TILE_CPU_RETURN_PASS') == 1
            assert len(re.findall(r'^REFERENCE_SRAM_BEHAVIORAL_MODEL instance=', log, re.M)) == 256
            assert not re.search(r'CONV2_TILE_.*FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|^\s*(?:Error-\[|Fatal:|FATAL:)', log, re.M)
            for instance in ('imem', 'dmem'):
                assert f'AXI_RESPONSE_DELAY_NS=1.000 instance=tb_soc.{instance}' in log
                assert f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{instance}' in log
            reports.append(dict(case=name, status='PASS', cycles=done['cycles'], pe_writes=6978, pe_reads=4608, actual_cpu_words=5184, actual_results_sha256=sha(record['observed_results'])))
        else:
            assert not completed and 'Fatal:' in log
            assert 'CONV2_TILE_MONITOR_PASS' not in log and 'CONV2_TILE_CPU_RETURN_PASS' not in log
            if name in failed_write:
                kind = 'weight' if name == 'wrong_weight' else 'input'
                match = re.search(r'CONV2_TILE_MONITOR_FAIL ' + kind + r' configuration index=(\d+) addr=([0-9a-f]+) data=([0-9a-f]+) expected_addr=([0-9a-f]+) expected_data=([0-9a-f]+)', log)
                assert match and int(match[1]) == failed_write[name]
                address, value = writes[failed_write[name]]
                assert int(match[2], 16) == int(match[4], 16) == address
                assert int(match[3], 16) == (value ^ 1) and int(match[5], 16) == value
            else:
                kind, offset = ('requant', 4864) if name == 'wrong_requant' else ('pool', 5120)
                match = re.search(r'CONV2_TILE_MONITOR_FAIL CPU ' + kind + r' index=0 actual=([0-9a-f]+) expected=([0-9a-f]+)', log)
                assert match and int(match[1], 16) == (int(expected[offset]) ^ 1) and int(match[2], 16) == int(expected[offset])
            reports.append(dict(case=name, status='PASS', accepted_pe_writes=expected_writes, accepted_pe_reads=expected_reads, accepted_cpu_words=expected_cpu, failure='first deliberately changed value rejected'))
    assert summary['wrong_source_guard_rejected'] is True
    guard_log = (case / 'wrong_source_guard.log').read_text()
    assert 'CONV2_TILE_PREPARE_FAILED: Conv1 pointer provenance mismatch: summary_sha256' in guard_log
    assert not (case / 'rejected_source_vector').exists()
    bad_pointer = json.loads((case / 'wrong_source_pointer.json').read_text())
    assert bad_pointer['summary_sha256'] == '0' * 64 and bad_pointer['status'] == 'PASS' and bad_pointer['full_first_layer'] is True
    source = Path(data['source_conv1_result'])
    assert Path(bad_pointer['result']).resolve() == source.resolve()
    assert bad_pointer['actual_output_sha256'] == data['source_input_sha256']
    assert bad_pointer['independent_audit_sha256'] == sha(source / 'independent_validation_summary.json')
    simulator = case / 'rtl/simv'
    assert sha(simulator) == summary['simulator_sha256']
    commands = [record['argv'] for record in summary['commands'] if record['argv'][0] == str(simulator)]
    assert len(commands) == len(names)
    assert len({next(arg for arg in command if arg.startswith('+MEM_HEX=')) for command in commands}) == len(names)
    for option in ('+INPUT_FILE=', '+WEIGHT_FILE=', '+GOLDEN_FILE='):
        assert len({next(arg for arg in command if arg.startswith(option)) for command in commands}) == 1
    for name, digest in summary['frozen_source_sha256'].items():
        assert sha(case / 'source' / name) == digest, name
    for path, digest in summary['input_sha256'].items():
        assert sha(path) == digest, path
    for name, digest in summary.get('artifacts_sha256', {}).items():
        assert sha(case / name) == digest, name
    rtl_path = next(Path(path) for path in summary['input_sha256'] if path.endswith('/project/rtl/soc_top.sv'))
    manifest = json.loads((rtl_path.parents[3] / 'SOURCE_MANIFEST.json').read_text())['base_files']
    baseline = []
    for name, digest in manifest.items():
        if name.startswith('project/rtl/') or name == 'project/sw/pe_smoke.c':
            assert summary['frozen_source_sha256'][name] == digest
            baseline.append(name)
    retained = []
    for name, digest in source_summary['frozen_source_sha256'].items():
        if name.startswith('project/sw/') and name.endswith(('.c', '.h', '.S', '.ld')):
            assert summary['frozen_source_sha256'][name] == digest, name
            retained.append(name)
    monitor = (case / 'source/project/tests/soc_conv2_tile_monitor.sv').read_text()
    assert "32'h600dcafe" in monitor and 'completion_this_cycle = 1' in monitor
    normal = next(record for record in reports if record['case'] == 'normal')
    return dict(status='PASS', scope='Actual prior CPU Pool1 -> all 64 Conv2 channels for one 2x2 region -> ReLU/Q24/Pool2; staged firmware boots, RTL only, not complete Conv2/CNN',
                source_conv1_result=str(source), source_input_sha256=data['source_input_sha256'], row=data['row'], col=data['col'],
                partial_values=4608, raw_values=256, requant_values=256, pool_values=64,
                pool_values_actual=expected[5120:].tolist(), normal=normal, execution_cases=reports,
                checks=dict(actual_upstream_full_layer_and_audit_verified=True, model_identity_matches_upstream=True,
                            independent_direct_288_term_math_and_divmod_quantization=True, exact_header_operands_no_cpu_golden=True,
                            all_6978_mmio_addresses_and_payloads=True, all_4608_pe_reads_full_bus_and_words=True,
                            all_5184_actual_cpu_ram_results=True, upstream_wrong_hash_guard_rejected=True,
                            requested_negative_controls_verified=negative_requested,
                            same_simulator_and_separate_firmware_and_host_golden=True,
                            live_frozen_and_artifact_hashes=True, original_rtl_smoke_preserved=baseline,
                            previous_conv1_software_preserved=retained))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    case = args.case.resolve(strict=True)
    output = args.output or case / 'independent_validation_summary.json'
    try:
        result = audit_case(case)
    except (AssertionError, OSError, ValueError, KeyError) as error:
        output.write_text(json.dumps(dict(status='FAIL', error=str(error), case=str(case)), indent=2) + '\n')
        raise
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
