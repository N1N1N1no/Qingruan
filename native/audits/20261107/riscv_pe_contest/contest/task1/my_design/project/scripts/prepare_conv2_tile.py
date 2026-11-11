#!/usr/bin/env python3
"""Prepare all 64 Conv2 channels at one real 2x2 region of actual CPU Pool1.

Input is copied from a completed, independently verified Conv1 CPU dump.  It is
never regenerated from a host golden.  Firmware receives only input/weights/
biases; all expected partial sums and postprocessing results stay on the host.
"""

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


def source_conv1(args):
    pointer_path = None
    pointer = None
    if args.conv1_result is None:
        pointer_path = args.conv1_pointer.resolve(strict=True)
        pointer = json.loads(pointer_path.read_text())
        if pointer.get("status") != "PASS" or pointer.get("full_first_layer") is not True:
            raise ValueError("Conv1 pointer is not a verified complete first-layer result")
        case = Path(pointer["result"]).resolve(strict=True)
    else:
        case = args.conv1_result.resolve(strict=True)
    summary_path = case / "summary.json"
    audit_path = case / "independent_validation_summary.json"
    vector_path = case / "vector/conv1_full.json"
    summary = json.loads(summary_path.read_text())
    audit = json.loads(audit_path.read_text())
    vector = json.loads(vector_path.read_text())
    if summary.get("status") != "PASS" or summary.get("full_first_layer") is not True:
        raise ValueError("Conv1 source must have a complete first-layer CPU PASS")
    if audit.get("status") != "PASS" or audit.get("full_first_layer") is not True:
        raise ValueError("Conv1 source must have a complete independent audit PASS")
    if not summary.get("cpu_included") or summary.get("tile_start") != 0 or summary.get("tile_count") != 8192:
        raise ValueError("Conv1 source does not cover the actual CPU's entire first layer")
    actual_path = Path(summary["observed_output"]).resolve(strict=True)
    if actual_path != (case / "rtl/normal_pool.bin").resolve(strict=True):
        raise ValueError("Conv1 input must be the recorded actual CPU dump")
    if actual_path.stat().st_size != 131072:
        raise ValueError("Actual Conv1 Pool1 output must contain 131072 CHW bytes")
    digest = point.sha256(actual_path)
    if digest != summary["observed_output_sha256"] or digest != audit["normal"]["actual_output_sha256"]:
        raise ValueError("Conv1 actual CPU output hash does not match its PASS records")
    if audit["normal"].get("actual_output_bytes") != 131072 or audit["normal"].get("tile_count") != 8192:
        raise ValueError("Independent Conv1 audit does not cover all Pool1 output bytes")
    if pointer is not None:
        for field, path in (("summary_sha256", summary_path),
                            ("independent_audit_sha256", audit_path),
                            ("results_sha256", case / "RESULTS.md")):
            if pointer.get(field) != point.sha256(path):
                raise ValueError("Conv1 pointer provenance mismatch: " + field)
        if Path(pointer["actual_output"]).resolve(strict=True) != actual_path or pointer["actual_output_sha256"] != digest:
            raise ValueError("Conv1 pointer does not refer to the actual audited CPU dump")
    # Check the archived source and artifacts, without demanding old live files
    # remain unchanged after later milestones add their own source code.
    for relative, expected in summary["frozen_source_sha256"].items():
        if point.sha256(case / "source" / relative) != expected:
            raise ValueError("Archived Conv1 source changed: " + relative)
    for relative, expected in summary.get("artifacts_sha256", {}).items():
        if point.sha256(case / relative) != expected:
            raise ValueError("Archived Conv1 artifact changed: " + relative)
    provenance_paths = {"source_conv1_actual_pool": actual_path,
                        "source_conv1_summary": summary_path,
                        "source_conv1_independent_audit": audit_path,
                        "source_conv1_vector": vector_path}
    if pointer_path is not None:
        provenance_paths["source_conv1_pointer"] = pointer_path
    return case, actual_path, vector, summary, provenance_paths


def c_input(values):
    lines = ["static const uint8_t C2T_INPUT_HW[32][64][64] = {"]
    for channel in values:
        lines.append("    {")
        for row in channel:
            lines.append("        { " + ", ".join(str(int(value)) for value in row) + " },")
        lines.append("    },")
    lines.append("};")
    return "\n".join(lines)


def c_weights(values):
    lines = ["static const uint8_t C2T_WEIGHT_HW[288][64] = {"]
    for row in values:
        lines.append("    { " + ", ".join(f"0x{int(value):02x}" for value in row) + " },")
    lines.append("};")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task2-root", type=Path, default=Path.home() / "contest/task2")
    parser.add_argument("--conv1-result", type=Path)
    parser.add_argument("--conv1-pointer", type=Path,
                        default=Path.home() / "contest/task1/work/latest_conv1_full_result.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--row", type=int, default=0)
    parser.add_argument("--col", type=int, default=0)
    args = parser.parse_args()
    torch.set_num_threads(2)
    if not (0 <= args.row <= 60 and 0 <= args.col <= 60 and args.row % 2 == 0 and args.col % 2 == 0):
        raise ValueError("Conv2 row and col must be even integers in 0..60")
    base = (args.task2_root / "base").resolve(strict=True)
    out = args.output_dir.resolve()
    if out == base or base in out.parents:
        raise ValueError("output-dir must not be inside task2/base")
    source_case, actual_path, source_vector, source_summary, provenance_paths = source_conv1(args)
    if out == source_case or source_case in out.parents:
        raise ValueError("output-dir must not modify the archived Conv1 result")
    source_digest = point.sha256(actual_path)
    inputs = np.fromfile(actual_path, dtype=np.uint8).reshape(32, 64, 64)
    if int(inputs.max()) > 127:
        raise ValueError("Pool1 activation exceeds the PE's nonnegative input range")
    npz_path = base / "quant_output/aircraft_cnn_quantized.npz"
    if point.sha256(npz_path) != source_vector["provenance"]["quantized_model"]["sha256"]:
        raise ValueError("Conv2 model differs from the model used for the actual Conv1 input")
    with np.load(npz_path, allow_pickle=False) as archive:
        weights = archive["conv2_weight_q"].astype(np.int64)
        hardware = archive["conv2_weight_hw"].astype(np.uint8)
        biases = archive["conv2_bias_q"].astype(np.int64)
        multiplier = int(archive["conv2_requant_mult"])
        shift = int(archive["conv2_requant_shift"])
    if weights.shape != (64, 32, 3, 3) or biases.shape != (64,):
        raise ValueError("Unexpected Conv2 tensor shapes")
    encoded = np.asarray([point.sign_magnitude(value) for value in weights.flat], dtype=np.uint8).reshape(weights.shape)
    if not np.array_equal(hardware, encoded):
        raise ValueError("Conv2 hardware weights do not match sign-magnitude encoding")
    if multiplier != 21410 or shift != 24:
        raise ValueError("Expected the supplied Conv2 Q24 multiplier/shift 21410/24")
    row, col = args.row, args.col
    positions = [(row, col), (row, col + 1), (row + 1, col), (row + 1, col + 1)]
    window_inputs = np.stack([inputs[:, r:r + 3, c:c + 3].reshape(288)
                              for r, c in positions], axis=1).astype(np.int64)
    weight_by_k = weights.reshape(64, 288).T
    hardware_by_k = hardware.reshape(64, 288).T
    partials = np.empty((4, 18, 64), dtype=np.int64)
    for group in range(4):
        for batch in range(18):
            first = batch * 16
            partials[group, batch] = (weight_by_k[first:first + 16, group * 16:group * 16 + 16].T
                                      @ window_inputs[first:first + 16]).reshape(64)
    raw = (partials.sum(axis=1).reshape(64, 4) + biases[:, None]).reshape(256)
    if min(int(partials.min()), int(raw.min())) < -(1 << 31) or max(int(partials.max()), int(raw.max())) >= (1 << 31):
        raise ValueError("Conv2 results do not fit INT32")
    independent = F.conv2d(
        torch.from_numpy(inputs.astype(np.float64)).unsqueeze(0),
        torch.from_numpy(weights.astype(np.float64)), torch.from_numpy(biases.astype(np.float64)),
        stride=1, padding=0,
    )[0]
    reference = [int(independent[channel, r, c]) for channel in range(64) for r, c in positions]
    if raw.tolist() != reference:
        raise ValueError("Selected Conv2 tile disagrees with independent full PyTorch Conv2D")
    quantized = np.clip((np.maximum(raw, 0) * multiplier + (1 << 23)) >> 24, 0, 127).astype(np.uint8)
    scalar_quant = []
    for value in raw:
        quotient, remainder = divmod(max(0, int(value)) * multiplier, 1 << 24)
        scalar_quant.append(min(127, quotient + int(remainder >= (1 << 23))))
    if scalar_quant != quantized.tolist():
        raise ValueError("Scalar and NumPy Conv2 requantization disagree")
    pooled = quantized.reshape(64, 4).max(axis=1).astype(np.uint8)
    if pooled.tolist() != [max(scalar_quant[start:start + 4]) for start in range(0, 256, 4)]:
        raise ValueError("Scalar and NumPy Conv2 pooling disagree")
    fault_candidates = np.flatnonzero((window_inputs[:, 0] != 0) & (weight_by_k[:, 0] != 0))
    if not fault_candidates.size:
        raise ValueError("Selected tile has no meaningful nonzero input/weight negative-control operand")
    fault_term = int(fault_candidates[0])
    changed_weight_byte = int(hardware_by_k[fault_term, 0]) ^ 1
    changed_weight = -(changed_weight_byte & 127) if changed_weight_byte & 128 else changed_weight_byte
    weight_delta = int(window_inputs[fault_term, 0]) * (changed_weight - int(weight_by_k[fault_term, 0]))
    input_delta = ((int(window_inputs[fault_term, 0]) ^ 1) - int(window_inputs[fault_term, 0])) * int(weight_by_k[fault_term, 0])
    cpu_results = np.concatenate([partials.reshape(-1), raw, quantized.astype(np.int64), pooled.astype(np.int64)])
    assert cpu_results.size == 5184
    out.mkdir(parents=True, exist_ok=True)
    input_path = out / "conv2_input.bin"
    shutil.copyfile(actual_path, input_path)
    if point.sha256(input_path) != source_digest:
        raise ValueError("Copied actual CPU Pool1 input changed")
    artifacts = {input_path.name: {"path": str(input_path), "sha256": source_digest, "dtype": "uint8",
                                  "shape": [32, 64, 64], "bytes": 131072,
                                  "layout": "actual Conv1 CPU output in CHW order"}}
    def binary(name, values, dtype, layout):
        path = out / name
        array = np.asarray(values, dtype=dtype)
        array.tofile(path)
        artifacts[name] = {"path": str(path), "sha256": point.sha256(path),
                           "dtype": str(np.dtype(dtype)), "shape": list(array.shape),
                           "bytes": path.stat().st_size, "layout": layout}
    binary("weights_hw.bin", hardware_by_k, "u1", "K,output_channel = 288,64; sign-magnitude bytes")
    binary("bias.bin", biases, "<i4", "output_channel=64; INT32 little-endian")
    binary("partials.bin", partials, "<i4", "group,batch,column*4+lane = 4,18,64; INT32 little-endian")
    binary("raw.bin", raw, "<i4", "output_channel*4+lane=256; bias added once")
    binary("requant.bin", quantized, "u1", "output_channel*4+lane=256; ReLU/Q24/clamp")
    binary("pool.bin", pooled, "u1", "output_channel=64 at one Pool2 spatial position")
    binary("cpu_results.bin", cpu_results, "<i4", "partials4608,raw256,requant256,pool64 = 5184 INT32 words")
    header = ["/* Conv2 operands from an actual audited CPU Pool1 dump; no CPU golden arrays. */",
              "#ifndef CONV2_TILE_DATA_H", "#define CONV2_TILE_DATA_H", "#include <stdint.h>",
              "enum { C2T_INPUT_CHANNELS=32, C2T_INPUT_H=64, C2T_INPUT_W=64,",
              "       C2T_TERMS=288, C2T_BATCHES=18, C2T_GROUPS=4, C2T_COLS=16, C2T_LANES=4,",
              "       C2T_OUTPUTS=256, C2T_POOL_OUTPUTS=64, C2T_RESULT_WORDS=5184 };",
              f"#define C2T_ROW {row}", f"#define C2T_COL {col}",
              f"#define C2T_FAULT_TERM {fault_term}", f"#define C2T_REQUANT_MULT {multiplier}",
              f"#define C2T_REQUANT_SHIFT {shift}", c_input(inputs), c_weights(hardware_by_k),
              point.c_array("C2T_BIAS", "int32_t", biases.tolist()), "#endif", ""]
    header_path = out / "conv2_tile_data.h"
    header_path.write_text("\n\n".join(header))
    artifacts[header_path.name] = {"path": str(header_path), "sha256": point.sha256(header_path),
                                   "bytes": header_path.stat().st_size,
                                   "scope": "CPU operands only; no expected outputs"}
    provenance_paths.update({"quantized_model": npz_path,
                             "integer_reference": base / "evaluate_INT8_cnn_fixed.py",
                             "pool_integer_reference": base / "verify_full_cnn.py",
                             "generator": Path(__file__).resolve(),
                             "point_generator_helpers": Path(point.__file__).resolve()})
    provenance = {name: {"path": str(path), "sha256": point.sha256(path)}
                  for name, path in provenance_paths.items()}
    payload = {"kind": "AIRCRAFT_CNN_ACTUAL_POOL1_CONV2_TILE_V1",
               "scope": "all 64 Conv2 channels at four positions -> ReLU/Q24 -> 64 Pool2 values, using actual prior CPU Pool1 output",
               "source_conv1_result": str(source_case), "source_conv1_simulation_mode": source_summary.get("simulation_mode"),
               "source_input_actual": str(actual_path), "source_input_sha256": source_digest,
               "sample_relative_path": source_vector["sample_relative_path"],
               "input_shape": [32, 64, 64], "conv_shape": [64, 62, 62], "full_pool_shape": [64, 31, 31],
               "row": row, "col": col, "pool_row": row // 2, "pool_col": col // 2,
               "lane_positions": positions, "groups": 4, "batches": 18, "terms": 288,
               "partial_values": 4608, "raw_values": 256, "requant_values": 256, "pool_values": 64,
               "cpu_result_words": 5184, "requant_multiplier": multiplier, "requant_shift": shift,
               "term_order": "input_channel,kernel_row,kernel_col (C,KH,KW)",
               "partial_order": "group,batch,column*4+lane", "output_order": "global_output_channel*4+lane",
               "cpu_result_layout": {"partials": [0, 4607], "raw": [4608, 4863],
                                     "requant": [4864, 5119], "pool": [5120, 5183]},
               "fault_term": fault_term, "fault_group": 0, "fault_column": 0,
               "fault_batch": fault_term // 16, "fault_row": fault_term % 16,
               "fault_input_lane0": int(window_inputs[fault_term, 0]),
               "fault_weight_q": int(weight_by_k[fault_term, 0]),
               "fault_weight_hw": int(hardware_by_k[fault_term, 0]),
               "weight_fault_expected_partial_delta_lane0": weight_delta,
               "input_fault_expected_partial_delta_lane0": input_delta,
               "raw_min": int(raw.min()), "raw_max": int(raw.max()),
               "requant_min": int(quantized.min()), "requant_max": int(quantized.max()),
               "pool_golden": pooled.tolist(), "pool_sha256": artifacts["pool.bin"]["sha256"],
               "host_golden_only": True, "firmware_golden_arrays": False,
               "independent_torch_fullconv_selected_matches": 256,
               "independent_scalar_requant_matches": 256,
               "provenance": provenance, "artifacts": artifacts,
               "environment": {"python": platform.python_version(), "numpy": np.__version__, "torch": torch.__version__}}
    json_path = out / "conv2_tile.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "source_conv1_result": str(source_case),
                      "source_input_sha256": source_digest, "row": row, "col": col,
                      "partial_values": 4608, "raw_values": 256, "pool_values": 64,
                      "raw_range": [int(raw.min()), int(raw.max())], "pool_golden": pooled.tolist(),
                      "fault_term": fault_term, "header": str(header_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print("CONV2_TILE_PREPARE_FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
