#!/usr/bin/env python3
"""Independently audit full Conv2 arithmetic and actual CPU/PE execution."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import numpy as np
import prepare_conv2_tile as source

TOTAL_TILES = 3844
OUTPUT_BYTES = 61504
PARTIALS_PER_TILE = 1152


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def blocks(start, count):
    stop = start + count
    while start < stop:
        length = min(32, stop - start, 961 - start % 961)
        yield start, length
        start += length


def expected_writes(start, count):
    return 66 + sum(18 * (80 + 16 * length) for _, length in blocks(start, count))


def output_indices(start, count):
    for tile_id in range(start, start + count):
        group, pixel = divmod(tile_id, 961)
        for column in range(16):
            yield (group * 16 + column) * 961 + pixel


def stream_partials(partials, start, count):
    return np.concatenate([partials[base:base + length].transpose(1, 0, 2).reshape(-1)
                           for base, length in blocks(start, count)])


def tile(values):
    return values.reshape(4, 16, 31, 2, 31, 2).transpose(0, 2, 4, 1, 3, 5).reshape(3844, 64)


def math_audit(root, return_data=False):
    data = json.loads((root / 'conv2_full.json').read_text())
    for name, meta in data['provenance'].items():
        assert sha(meta['path']) == meta['sha256'], 'Source hash: ' + name
    for name, meta in data['artifacts'].items():
        path = root / name
        assert path.stat().st_size == meta['bytes'] and sha(path) == meta['sha256'], 'Artifact hash: ' + name
    parent, actual_path, parent_vector, _, _ = source.source_conv1(SimpleNamespace(
        conv1_result=Path(data['source_conv1_result']), conv1_pointer=None))
    assert data['provenance']['quantized_model']['sha256'] == parent_vector['provenance']['quantized_model']['sha256']
    inputs = np.fromfile(actual_path, dtype='u1').reshape(32, 64, 64).astype(np.int64)
    assert sha(actual_path) == data['source_input_sha256'] == sha(root / 'conv2_input.bin')
    with np.load(data['provenance']['quantized_model']['path'], allow_pickle=False) as archive:
        weights = archive['conv2_weight_q'].astype(np.int64)
        hw = archive['conv2_weight_hw'].astype(np.uint8)
        biases = archive['conv2_bias_q'].astype(np.int64)
        mult, shift = int(archive['conv2_requant_mult']), int(archive['conv2_requant_shift'])
    assert int(inputs.max()) <= 127 and int(np.abs(weights).max()) <= 127
    assert int(np.abs(biases).max()) + 288 * 127 * 127 <= (1 << 31) - 1
    assert (mult, shift) == (21410, 24) == (data['requant_multiplier'], data['requant_shift'])
    assert np.array_equal(hw, np.where(weights < 0, 128 - weights, weights).astype(np.uint8))
    assert np.array_equal(np.fromfile(root / 'weights_hw.bin', dtype='u1').reshape(288, 64), hw.reshape(64, 288).T)
    assert np.array_equal(np.fromfile(root / 'bias.bin', dtype='<i4'), biases)
    # Direct kernel-slice accumulation; no patch matrix, matmul or PyTorch.
    partial_chw = np.zeros((18, 64, 62, 62), dtype=np.int64)
    term = 0
    for channel in range(32):
        for ky in range(3):
            for kx in range(3):
                partial_chw[term // 16] += weights[:, channel, ky, kx, None, None] * inputs[channel, ky:ky + 62, kx:kx + 62]
                term += 1
    partials = np.stack([tile(values) for values in partial_chw], axis=1).astype('<i4')
    assert np.array_equal(np.fromfile(root / 'partials.bin', dtype='<i4').reshape(3844, 18, 64), partials)
    raw = partial_chw.sum(axis=0) + biases[:, None, None]
    assert np.array_equal(np.fromfile(root / 'raw.bin', dtype='<i4').reshape(64, 62, 62), raw)
    def quant(value):
        quotient, remainder = divmod(max(0, int(value)) * mult, 1 << shift)
        return min(127, quotient + int(remainder >= (1 << (shift - 1))))
    quantized = np.fromiter((quant(value) for value in raw.flat), dtype=np.uint8, count=raw.size).reshape(raw.shape)
    assert np.array_equal(np.fromfile(root / 'requant.bin', dtype='u1').reshape(raw.shape), quantized)
    pool = np.maximum.reduce([quantized[:, 0::2, 0::2], quantized[:, 0::2, 1::2],
                              quantized[:, 1::2, 0::2], quantized[:, 1::2, 1::2]])
    assert np.array_equal(np.fromfile(root / 'pool.bin', dtype='u1').reshape(64, 31, 31), pool)
    header = (root / 'conv2_full_data.h').read_text()
    assert 'golden' not in header.lower() and 'expected' not in header.lower()
    for name, expected in [('C2F_INPUT_HW', inputs), ('C2F_WEIGHT_HW', hw.reshape(64, 288).T), ('C2F_BIAS', biases)]:
        match = re.search(name + r'\s*\[[^=]+?=\s*\{(.*?)\};', header, re.S)
        assert match, name
        values = [int(value, 0) for value in re.findall(r'-?0x[0-9a-fA-F]+|-?\d+', match[1])]
        assert values == expected.reshape(-1).tolist(), name
    result = dict(status='PASS', scope='independent direct288-term full Conv2 arithmetic',
                  source_conv1_result=str(parent), source_input_sha256=sha(actual_path),
                  partial_values=int(partials.size), raw_values=int(raw.size), pool_values=int(pool.size),
                  raw_min=int(raw.min()), raw_max=int(raw.max()), pool_sha256=sha(root / 'pool.bin'),
                  checks=dict(actual_audited_parent_cpu_input=True, all288_direct_kernel_terms=True,
                              all18_partial_batches=True, scalar_divmod_q24_and_pool=True,
                              firmware_header_contains_only_exact_operands=True, source_and_artifact_hashes=True))
    if return_data:
        return result, partials, pool
    return result


def trace_rows(path):
    lines = path.read_text().splitlines()
    assert lines and lines[0] == 'CONV2_FULL_TILE_TRACE_V1', 'Invalid tile trace header'
    rows = []
    for line in lines[1:]:
        fields = line.split('\t')
        item = dict(cycles=int(fields[0]), event=fields[1])
        item.update({key: int(value, 16 if key in ('addr', 'actual', 'expected_addr', 'expected') else 10)
                     for key, value in (field.split('=', 1) for field in fields[2:])})
        rows.append(item)
    return rows


def case_audit(case):
    summary = json.loads((case / 'summary.json').read_text())
    assert summary['status'] in ('RUNNING', 'PASS'), summary.get('error')
    math_result, partial_gold, pool_gold = math_audit(case / 'vector', True)
    records = summary['cases']
    by_name = {record['case']: record for record in records}
    assert len(records) == len(by_name) and all(record.get('pass') for record in records)
    assert records, 'No actual execution cases'
    expected_fixed = {'preflight_start', 'preflight_group_boundary', 'preflight_block_boundary', 'preflight_last', 'wrong_weight', 'wrong_input', 'wrong_pool'}
    assert expected_fixed <= set(by_name), 'Missing boundary or negative execution cases'
    validations = []
    for record in records:
        name, start, count = record['case'], record['tile_start'], record['tile_count']
        log = (case / (name + '_simulation.log')).read_text()
        trace = case / 'rtl' / (name + '_tiles.tsv')
        partial_dump = case / 'rtl' / (name + '_partials.bin')
        if name.startswith('wrong_'):
            assert 'Fatal:' in log and 'CONV2_FULL_MONITOR_PASS' not in log and 'CONV2_FULL_CPU_RETURN_PASS' not in log
            fault = summary['vector']['fault_term']
            batch, row = divmod(fault, 16)
            first_length = next(blocks(start, count))[1]
            prefix = 66 + batch * (80 + 16 * first_length)
            if name in ('wrong_weight', 'wrong_input'):
                match = re.search(r'CONV2_FULL_MONITOR_FAIL (?:weight configuration|input configuration|PE configuration) index=(\d+) addr=([0-9a-f]+) data=([0-9a-f]+) expected_addr=([0-9a-f]+) expected_data=([0-9a-f]+)', log)
                assert match, name + ' missing configuration failure'
                failure_index = prefix + (row * 5 if name == 'wrong_weight' else 80 + row)
                address = 0x90000020 if name == 'wrong_weight' else 0x90000100 + row * 4
                assert int(match[1]) == failure_index
                assert int(match[2], 16) == int(match[4], 16) == address
                assert int(match[3], 16) == (int(match[5], 16) ^ 1)
                expected_prefix = stream_partials(partial_gold, start, count)[:batch * first_length * 64]
                assert np.array_equal(np.fromfile(partial_dump, dtype='<i4'), expected_prefix)
                rejected = trace_rows(trace)
                assert len(rejected) == 1 and rejected[0]['event'] == 'REJECT_WRITE'
                event = rejected[0]
                assert (event['index'], event['addr'], event['expected_addr']) == (failure_index, address, address)
                assert event['actual'] == (event['expected'] ^ 1)
                assert event['actual'] == int(match[3], 16) and event['expected'] == int(match[5], 16)
                assert event['pe_reads'] == expected_prefix.size
                validations.append(dict(case=name, status='PASS', failure_write_index=failure_index,
                                        correct_partial_prefix_words=int(expected_prefix.size),
                                        reason='one meaningful operand bit changed and rejected at actual MMIO acceptance'))
            elif name == 'wrong_pool':
                match = re.search(r'CONV2_FULL_MONITOR_FAIL CPU output index=(\d+) tile=(\d+) column=(\d+) actual=([0-9a-f]+) expected=([0-9a-f]+) reads=(\d+) required_reads=(\d+)', log)
                assert match, 'missing CPU output failure'
                first_index = next(output_indices(start, 1))
                expected = int(pool_gold.reshape(-1)[first_index])
                assert (int(match[1]), int(match[2]), int(match[3])) == (first_index, start, 0)
                assert int(match[4], 16) == (expected ^ 1) and int(match[5], 16) == expected
                expected_reads = first_length * PARTIALS_PER_TILE
                assert int(match[6]) == int(match[7]) == expected_reads
                assert np.array_equal(np.fromfile(partial_dump, dtype='<i4'), stream_partials(partial_gold, start, first_length))
                rejected = trace_rows(trace)
                assert len(rejected) == 1 and rejected[0]['event'] == 'REJECT_OUTPUT'
                event = rejected[0]
                assert (event['index'], event['tile'], event['column']) == (first_index, start, 0)
                assert event['actual'] == (expected ^ 1) and event['expected'] == expected
                assert event['pe_reads'] == expected_reads and event['pe_writes'] == expected_writes(start, first_length)
                assert event['output_bytes'] == 0
                validations.append(dict(case=name, status='PASS', correct_partial_prefix_words=expected_reads,
                                        reason='first actual CPU pool byte changed by one bit after full first-block arithmetic'))
            else:
                raise AssertionError('Unrecognized negative execution case: ' + name)
            continue
        assert log.count('CONV2_FULL_MONITOR_PASS') == 1 and log.count('CONV2_FULL_CPU_RETURN_PASS') == 1
        assert not re.search(r'CONV2_FULL_.*FAIL|AXI_RX_(?:SETUP_FAIL|UNKNOWN)|^\s*(?:Error-\[|Fatal:|FATAL:)', log, re.M)
        assert len(re.findall(r'^REFERENCE_SRAM_BEHAVIORAL_MODEL instance=', log, re.M)) == 256
        for instance in ('imem', 'dmem'):
            assert f'AXI_RESPONSE_DELAY_NS=1.000 instance=tb_soc.{instance}' in log
            assert f'AXI_RX_SETUP_CHECK_ENABLED setup_ns=1.000 hold_ns=0.000 instance=tb_soc.{instance}' in log
        observed_partial = np.fromfile(partial_dump, dtype='<i4')
        assert np.array_equal(observed_partial, stream_partials(partial_gold, start, count)), name + ' actual PE partials'
        observed = np.fromfile(case / 'rtl' / (name + '_pool.bin'), dtype='u1')
        expected = np.zeros(OUTPUT_BYTES, dtype=np.uint8)
        selected = np.fromiter(output_indices(start, count), dtype=np.int64, count=count * 16)
        expected[selected] = pool_gold.reshape(-1)[selected]
        assert np.array_equal(observed, expected), name + ' actual CPU pool'
        rows = trace_rows(trace)
        tile_rows = [row for row in rows if row['event'] == 'TILE']
        done_rows = [row for row in rows if row['event'] == 'COMPLETE']
        assert len(rows) == count + 1 and len(tile_rows) == count and len(done_rows) == 1
        completed = 0
        write_total = 66
        read_total = 0
        for base, length in blocks(start, count):
            write_total += 18 * (80 + 16 * length)
            read_total += length * PARTIALS_PER_TILE
            for index in range(length):
                row = tile_rows[completed]
                completed += 1
                assert (row['index'], row['completed'], row['output_bytes']) == (base + index, completed, completed * 16)
                assert row['pe_reads'] == read_total and row['pe_writes'] == write_total
                if completed > 1:
                    assert row['cycles'] > tile_rows[completed - 2]['cycles']
        done = done_rows[0]
        assert (done['pe_reads'], done['pe_writes'], done['pe_responses'], done['output_bytes'], done['tiles']) == (count * PARTIALS_PER_TILE, expected_writes(start, count), expected_writes(start, count), count * 16, count)
        for key, value in [('pe_reads_checked', count * PARTIALS_PER_TILE), ('pe_writes_checked', expected_writes(start, count)), ('output_bytes_checked', count * 16)]:
            assert record[key] == value, key
        assert sha(partial_dump) == record['observed_partials_sha256']
        assert sha(case / 'rtl' / (name + '_pool.bin')) == record['observed_sha256']
        assert sha(trace) == record['trace_sha256']
        validations.append(dict(case=name, status='PASS', tile_start=start, tile_count=count,
                                actual_partial_values=count * PARTIALS_PER_TILE, actual_output_bytes=count * 16,
                                pe_configuration_writes=expected_writes(start, count), cycles=done['cycles'],
                                actual_partial_sha256=sha(partial_dump), actual_output_sha256=sha(case / 'rtl' / (name + '_pool.bin'))))
    simulator = case / 'rtl/simv'
    assert sha(simulator) == summary['simulator_sha256']
    commands = [record['argv'] for record in summary['commands'] if record['argv'][0] == str(simulator)]
    assert len(commands) == len(records), 'All recorded executions must use the same simulator'
    for option in ('+INPUT_FILE=', '+WEIGHT_FILE=', '+PARTIAL_FILE=', '+POOL_FILE='):
        assert len({next(arg for arg in command if arg.startswith(option)) for command in commands}) == 1
    for path, digest in summary['frozen_source_sha256'].items():
        assert sha(case / 'source' / path) == digest, path
    for path, digest in summary['input_sha256'].items():
        assert sha(path) == digest, path
    for path, digest in summary.get('artifacts_sha256', {}).items():
        assert sha(case / path) == digest, path
    rtl_live = next(Path(path) for path in summary['input_sha256'] if path.endswith('/project/rtl/soc_top.sv'))
    task_root = rtl_live.parents[3]
    starter = json.loads((task_root / 'SOURCE_MANIFEST.json').read_text())['base_files']
    baseline = []
    for name, digest in starter.items():
        if name.startswith('project/rtl/') or name == 'project/sw/pe_smoke.c':
            assert summary['frozen_source_sha256'][name] == digest and sha(case / 'source' / name) == digest
            baseline.append(name)
    if summary.get('wrong_source_guard_rejected'):
        wrong_source_log = (case / 'wrong_source_guard.log').read_text()
        assert 'CONV2_FULL_PREPARE_FAILED: Conv1 pointer provenance mismatch: summary_sha256' in wrong_source_log
    requested_negatives = summary.get('negative_controls_requested', True)
    if requested_negatives:
        assert {'wrong_weight', 'wrong_input', 'wrong_pool'} <= set(by_name)
        assert summary.get('wrong_source_guard_rejected') is True
    normal = sorted([record for record in validations if record['case'].startswith('normal')], key=lambda record: record['tile_start'])
    preflight_only = bool(summary.get('preflight_only', False))
    assert len(normal) == (0 if preflight_only else int(summary['shards']))
    next_start = summary['tile_start']
    merged_pool = np.zeros(OUTPUT_BYTES, dtype=np.uint8)
    merged_partials = np.zeros((TOTAL_TILES, 18, 64), dtype='<i4')
    covered = np.zeros(OUTPUT_BYTES, dtype=np.uint8)
    for record in normal:
        assert record['tile_start'] == next_start and record['tile_count'] > 0
        next_start += record['tile_count']
        selected = np.fromiter(output_indices(record['tile_start'], record['tile_count']), dtype=np.int64, count=record['tile_count'] * 16)
        assert not covered[selected].any()
        covered[selected] = 1
        actual = np.fromfile(case / 'rtl' / (record['case'] + '_pool.bin'), dtype='u1')
        merged_pool[selected] = actual[selected]
        stream = np.fromfile(case / 'rtl' / (record['case'] + '_partials.bin'), dtype='<i4')
        cursor = 0
        for base, length in blocks(record['tile_start'], record['tile_count']):
            size = length * PARTIALS_PER_TILE
            merged_partials[base:base + length] = stream[cursor:cursor + size].reshape(18, length, 64).transpose(1, 0, 2)
            cursor += size
        assert cursor == stream.size
    checked_tiles = 0 if preflight_only else summary['tile_count']
    assert next_start == summary['tile_start'] + checked_tiles
    assert int(covered.sum()) == checked_tiles * 16
    assert np.array_equal(np.fromfile(case / 'rtl/normal_pool.bin', dtype='u1'), merged_pool)
    assert np.array_equal(merged_pool[covered.astype(bool)], pool_gold.reshape(-1)[covered.astype(bool)])
    assert np.array_equal(np.fromfile(case / 'rtl/normal_partials.bin', dtype='<i4'), merged_partials.reshape(-1))
    assert sha(case / 'rtl/normal_pool.bin') == summary['observed_output_sha256']
    full = not preflight_only and summary['tile_start'] == 0 and summary['tile_count'] == TOTAL_TILES
    return dict(status='PASS', case=str(case), full_second_layer=full,
                scope=('full' if full else 'selected range of') + ' Conv2/ReLU/Q24/Pool2 with actual CPU and PE; saved upstream Pool1; no continuous multilayer execution or SDF',
                mathematical_oracle=math_result, execution_cases=validations,
                normal=dict(status='PASS', tile_start=summary['tile_start'], tile_count=checked_tiles,
                            shards=(0 if preflight_only else summary['shards']), simulation_mode=summary['simulation_mode'],
                            actual_partial_values=checked_tiles * PARTIALS_PER_TILE,
                            actual_output_bytes=checked_tiles * 16,
                            pe_configuration_writes=sum(record['pe_configuration_writes'] for record in normal),
                            actual_output_sha256=sha(case / 'rtl/normal_pool.bin'), actual_partial_sha256=sha(case / 'rtl/normal_partials.bin'),
                            per_shard_cycles=[dict(case=record['case'], tile_start=record['tile_start'], tile_count=record['tile_count'], cycles=record['cycles']) for record in normal]),
                checks=dict(all_actual_pe_partials_match_independent_direct_math=True,
                            all_actual_cpu_pool_bytes_match_independent_math=True,
                            contiguous_nonoverlapping_tile_coverage=True,
                            actual_dumps_merged_without_host_substitution=True,
                            same_compiled_simulator_and_original_rtl=True,
                            negative_controls_rejected=requested_negatives,
                            source_and_artifact_hashes=True, starter_rtl_and_smoke=baseline,
                            actual_256_sram_models_and_ram_timing_checks=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path)
    parser.add_argument('--vector', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if (args.case is None) == (args.vector is None):
        parser.error('Specify exactly one of --case or --vector')
    root = (args.case or args.vector).resolve(strict=True)
    output = args.output or root / ('independent_validation_summary.json' if args.case else 'independent_math_summary.json')
    try:
        result = case_audit(root) if args.case else math_audit(root)
    except (AssertionError, ValueError, OSError, KeyError) as error:
        result = dict(status='FAIL', case=str(root), error=str(error), audit='independent full Conv2 CPU/oracle audit')
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        raise
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
