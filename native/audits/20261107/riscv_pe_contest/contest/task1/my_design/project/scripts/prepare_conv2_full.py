#!/usr/bin/env python3
"""Prepare full Conv2 operands from the preceding audited actual CPU Pool1."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import sys

import numpy as np
import torch
import torch.nn.functional as F

import prepare_conv1_point as point
import prepare_conv2_tile as tile


def from_tiles(values):
    """group,pool_row,pool_col,column,lane -> CHW Conv2 tensor."""
    return values.reshape(4, 31, 31, 16, 2, 2).transpose(0, 3, 1, 4, 2, 5).reshape(64, 62, 62)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task2-root', type=Path, default=Path.home() / 'contest/task2')
    parser.add_argument('--conv1-result', type=Path)
    parser.add_argument('--conv1-pointer', type=Path,
                        default=Path.home() / 'contest/task1/work/latest_conv1_full_result.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    base = (args.task2_root / 'base').resolve(strict=True)
    out = args.output_dir.resolve()
    source_case, actual_path, source_vector, source_summary, provenance_paths = tile.source_conv1(args)
    if out == base or base in out.parents or out == source_case or source_case in out.parents:
        raise ValueError('output-dir must not modify task2/base or archived Conv1 results')
    inputs = np.fromfile(actual_path, dtype=np.uint8).reshape(32, 64, 64)
    if int(inputs.max()) > 127:
        raise ValueError('Actual Pool1 exceeds the nonnegative PE byte range')
    npz_path = base / 'quant_output/aircraft_cnn_quantized.npz'
    if point.sha256(npz_path) != source_vector['provenance']['quantized_model']['sha256']:
        raise ValueError('Conv2 model differs from the verified Conv1 model')
    with np.load(npz_path, allow_pickle=False) as z:
        weights = z['conv2_weight_q'].astype(np.int64)
        hardware = z['conv2_weight_hw'].astype(np.uint8)
        biases = z['conv2_bias_q'].astype(np.int64)
        multiplier, shift = int(z['conv2_requant_mult']), int(z['conv2_requant_shift'])
    if weights.shape != (64, 32, 3, 3) or biases.shape != (64,):
        raise ValueError('Unexpected Conv2 operand shapes')
    if np.abs(weights).max() > 127 or np.abs(biases).max() + 288 * 127 * 127 > (1 << 31) - 1:
        raise ValueError('Conv2 operands exceed proven INT32 accumulation bound')
    encoded = np.where(weights < 0, 128 - weights, weights).astype(np.uint8)
    if not np.array_equal(hardware, encoded) or (multiplier, shift) != (21410, 24):
        raise ValueError('Conv2 sign-magnitude encoding or Q24 scaling is invalid')
    weight_by_k = weights.reshape(64, 288).T
    hardware_by_k = hardware.reshape(64, 288).T
    windows = np.lib.stride_tricks.sliding_window_view(inputs, (3, 3), axis=(1, 2))
    terms = windows.transpose(0, 3, 4, 1, 2).reshape(288, 62, 62)
    lane_values = np.stack([terms[:, 0::2, 0::2], terms[:, 0::2, 1::2],
                            terms[:, 1::2, 0::2], terms[:, 1::2, 1::2]], axis=-1).reshape(288, 961, 4).astype(np.int64)
    partials = np.empty((4, 961, 18, 64), dtype=np.int64)
    for group in range(4):
        for batch in range(18):
            first = batch * 16
            result = weight_by_k[first:first + 16, group * 16:group * 16 + 16].T @ lane_values[first:first + 16].reshape(16, -1)
            partials[group, :, batch] = result.reshape(16, 961, 4).transpose(1, 0, 2).reshape(961, 64)
    partials = partials.reshape(3844, 18, 64)
    raw = from_tiles(partials.sum(axis=1)) + biases[:, None, None]
    independent = F.conv2d(torch.from_numpy(inputs.astype(np.float64)).unsqueeze(0),
                           torch.from_numpy(weights.astype(np.float64)),
                           torch.from_numpy(biases.astype(np.float64)))[0].numpy()
    if not np.array_equal(raw, independent):
        raise ValueError('NumPy blocked partials disagree with independent full PyTorch Conv2D')
    quantized = np.clip((np.maximum(raw, 0) * multiplier + (1 << 23)) >> shift, 0, 127).astype(np.uint8)
    def quant(value):
        quotient, remainder = divmod(max(0, int(value)) * multiplier, 1 << shift)
        return min(127, quotient + int(remainder >= (1 << (shift - 1))))
    scalar = np.fromiter((quant(value) for value in raw.flat), dtype=np.uint8, count=raw.size).reshape(raw.shape)
    if not np.array_equal(quantized, scalar):
        raise ValueError('Vectorized and scalar rounding disagree')
    pooled = quantized.reshape(64, 31, 2, 31, 2).max(axis=(2, 4))
    fault_candidates = np.flatnonzero((lane_values[:, 0, 0] != 0) & (weight_by_k[:, 0] != 0))
    if not fault_candidates.size:
        raise ValueError('First tile has no meaningful nonzero fault operand')
    fault_term = int(fault_candidates[0])
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(actual_path, out / 'conv2_input.bin')
    artifacts = {}
    def record(name, dtype, shape, layout):
        path = out / name
        artifacts[name] = dict(path=str(path), sha256=point.sha256(path), bytes=path.stat().st_size,
                               dtype=str(np.dtype(dtype)), shape=list(shape), layout=layout)
    record('conv2_input.bin', 'u1', inputs.shape, 'audited actual prior CPU Pool1; CHW')
    def binary(name, values, dtype, layout):
        np.asarray(values, dtype=dtype).tofile(out / name)
        record(name, dtype, values.shape, layout)
    binary('weights_hw.bin', hardware_by_k, 'u1', 'K,output_channel; sign magnitude')
    binary('bias.bin', biases, '<i4', 'output_channel')
    binary('partials.bin', partials, '<i4', 'tile,batch,column*4+lane; tile=group*961+pool_row*31+pool_col')
    binary('raw.bin', raw, '<i4', 'CHW64x62x62; bias added once')
    binary('requant.bin', quantized, 'u1', 'CHW64x62x62; ReLU/Q24/clamp')
    binary('pool.bin', pooled, 'u1', 'CHW64x31x31; Pool2')
    header = ['/* Full Conv2 operands from an audited actual CPU Pool1 dump. */',
              '#ifndef CONV2_FULL_DATA_H', '#define CONV2_FULL_DATA_H', '#include <stdint.h>',
              '#define C2F_REQUANT_MULT 21410', '#define C2F_REQUANT_SHIFT 24',
              f'#define C2F_FAULT_TERM {fault_term}', f'#define C2F_MAX_ABS_BIAS {int(np.abs(biases).max())}',
              tile.c_input(inputs).replace('C2T_INPUT_HW', 'C2F_INPUT_HW'),
              tile.c_weights(hardware_by_k).replace('C2T_WEIGHT_HW', 'C2F_WEIGHT_HW'),
              point.c_array('C2F_BIAS', 'int32_t', biases.tolist()), '#endif', '']
    (out / 'conv2_full_data.h').write_text('\n\n'.join(header))
    header_path = out / 'conv2_full_data.h'
    artifacts[header_path.name] = dict(path=str(header_path), sha256=point.sha256(header_path),
                                      bytes=header_path.stat().st_size, scope='CPU operands only')
    provenance_paths.update(quantized_model=npz_path, generator=Path(__file__).resolve(),
                            source_verifier=Path(tile.__file__).resolve(),
                            point_generator_helpers=Path(point.__file__).resolve(),
                            integer_reference=base / 'evaluate_INT8_cnn_fixed.py',
                            pool_integer_reference=base / 'verify_full_cnn.py')
    provenance = {name: dict(path=str(path), sha256=point.sha256(path)) for name, path in provenance_paths.items()}
    changed_byte = int(hardware_by_k[fault_term, 0]) ^ 1
    changed_weight = -(changed_byte & 127) if changed_byte & 128 else changed_byte
    payload = dict(kind='AIRCRAFT_CNN_ACTUAL_POOL1_CONV2_FULL_V1',
                   scope='Full Conv2/ReLU/Q24/Pool2 from actual saved CPU Pool1; separate firmware boot',
                   source_conv1_result=str(source_case), source_input_actual=str(actual_path),
                   source_input_sha256=point.sha256(actual_path),
                   source_conv1_simulation_mode=source_summary.get('simulation_mode'),
                   sample_relative_path=source_vector['sample_relative_path'], input_shape=[32, 64, 64],
                   conv_shape=[64, 62, 62], pool_shape=[64, 31, 31], total_tiles=3844,
                   groups=4, batches=18, terms=288, block_tiles=32,
                   partial_values=int(partials.size), raw_values=int(raw.size), requant_values=int(raw.size),
                   pool_values=int(pooled.size), requant_multiplier=multiplier, requant_shift=shift,
                   term_order='input_channel,kernel_row,kernel_col',
                   partial_order='tile,batch,column*4+lane', pool_order='CHW',
                   fault_term=fault_term, fault_group=0, fault_column=0,
                   fault_batch=fault_term // 16, fault_row=fault_term % 16,
                   fault_input_lane0=int(lane_values[fault_term, 0, 0]), fault_weight_q=int(weight_by_k[fault_term, 0]),
                   fault_weight_hw=int(hardware_by_k[fault_term, 0]),
                   weight_fault_expected_partial_delta_lane0=int(lane_values[fault_term, 0, 0]) * (changed_weight - int(weight_by_k[fault_term, 0])),
                   input_fault_expected_partial_delta_lane0=((int(lane_values[fault_term, 0, 0]) ^ 1) - int(lane_values[fault_term, 0, 0])) * int(weight_by_k[fault_term, 0]),
                   raw_min=int(raw.min()), raw_max=int(raw.max()), pool_sha256=artifacts['pool.bin']['sha256'],
                   host_golden_only=True, firmware_golden_arrays=False,
                   independent_torch_fullconv_matches=int(raw.size), independent_scalar_requant_matches=int(raw.size),
                   provenance=provenance, artifacts=artifacts,
                   environment=dict(python=platform.python_version(), numpy=np.__version__, torch=torch.__version__))
    (out / 'conv2_full.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(status='PASS', source_conv1_result=str(source_case),
                          partial_values=int(partials.size), pool_values=int(pooled.size),
                          pool_sha256=artifacts['pool.bin']['sha256'], fault_term=fault_term,
                          header=str(header_path), json=str(out / 'conv2_full.json')), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print('CONV2_FULL_PREPARE_FAILED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
