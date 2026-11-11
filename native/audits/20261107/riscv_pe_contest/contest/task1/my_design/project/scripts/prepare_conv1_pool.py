#!/usr/bin/env python3
"""Prepare real Conv1 tile -> ReLU/Q24 requantization -> one pool window.

The existing tile generator supplies the 64 raw convolution accumulators.
This stage adds their exact integer postprocessing and separate CPU arithmetic
fixtures.  The synthetic fixtures are unit checks, not extra model outputs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def requant_scalar(value, multiplier, shift):
    """Python unlimited-precision reference: ReLU, half-up Q24, saturation."""
    relu = max(0, int(value))
    return min(127, (relu * multiplier + (1 << (shift - 1))) >> shift)


def requant_numpy(values, multiplier, shift):
    """Independent vector calculation with explicit signed 64-bit intermediates."""
    value_array = np.asarray(values, dtype=np.int64)
    scaled = np.maximum(value_array, np.int64(0)) * np.int64(multiplier)
    return np.clip((scaled + np.int64(1 << (shift - 1))) >> shift, 0, 127).tolist()


def c_array(name, c_type, values):
    entries = []
    for value in values:
        entries.append("INT32_MIN" if value == -(1 << 31) else str(int(value)))
    lines = [f"static const {c_type} {name}[{len(entries)}] = {{"]
    for start in range(0, len(entries), 8):
        lines.append("    " + ", ".join(entries[start:start + 8]) + ",")
    lines.append("};")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task2-root", type=Path,
                        default=Path.home() / "contest/task2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validation-index", type=int, default=0)
    parser.add_argument("--output-channel-start", type=int, choices=(0, 16), default=0)
    parser.add_argument("--row", type=int, default=0)
    parser.add_argument("--col", type=int, default=0)
    args = parser.parse_args()
    if not (0 <= args.row <= 126 and 0 <= args.col <= 126
            and args.row % 2 == 0 and args.col % 2 == 0):
        raise ValueError("row and col must be even integers in 0..126 for stride-2 pooling")
    task2_root = args.task2_root.resolve(strict=True)
    base = (task2_root / "base").resolve(strict=True)
    out = args.output_dir.resolve()
    if out == base or base in out.parents:
        raise ValueError("output-dir must not be inside task2/base")
    tile_generator = Path(__file__).resolve().with_name("prepare_conv1_tile.py")
    command = [sys.executable, str(tile_generator), "--task2-root", str(task2_root),
               "--output-dir", str(out), "--validation-index", str(args.validation_index),
               "--output-channel-start", str(args.output_channel_start),
               "--row", str(args.row), "--col", str(args.col)]
    subprocess.run(command, check=True)
    tile_path = out / "conv1_tile.json"
    tile_header_path = out / "conv1_tile_data.h"
    tile = json.loads(tile_path.read_text())
    multiplier = int(tile["requant_multiplier"])
    shift = int(tile["requant_shift"])
    if shift != 24 or not 0 < multiplier <= (1 << 31) - 1:
        raise ValueError("Expected positive INT32 Q24 Conv1 multiplier")
    npz_path = Path(tile["provenance"]["quantized_model"]["path"])
    with np.load(npz_path, allow_pickle=False) as archive:
        if int(archive["conv1_requant_mult"]) != multiplier or int(archive["conv1_requant_shift"]) != shift:
            raise ValueError("Tile quantization parameters differ from the model")
    real_acc = tile["accumulator_golden"]
    real_requant = [requant_scalar(value, multiplier, shift) for value in real_acc]
    if real_requant != requant_numpy(real_acc, multiplier, shift):
        raise ValueError("Scalar and NumPy real-image requantization disagree")
    real_pool = [max(real_requant[start:start + 4]) for start in range(0, 64, 4)]
    if real_pool != np.asarray(real_requant, dtype=np.int64).reshape(16, 2, 2).max(axis=(1, 2)).tolist():
        raise ValueError("Scalar and NumPy real-image pooling disagree")

    # Five output transition boundaries: low rounding, middle range, entry to
    # 127, and the unclipped transition to 128 which must saturate back to 127.
    # For target t, the first integer accumulator producing >=t before clip is
    # ceil((t*2^shift - 2^(shift-1))/multiplier).
    fixtures = [
        ("int32_min_relu", -(1 << 31)), ("negative_relu", -100000),
        ("minus_one_relu", -1), ("zero", 0), ("one", 1), ("two", 2),
        ("small_positive", 123), ("ordinary_positive", 1000),
        ("int32_max_wide_multiply", (1 << 31) - 1),
    ]
    boundary_metadata = []
    for target in (1, 2, 64, 127, 128):
        numerator = (target << shift) - (1 << (shift - 1))
        threshold = (numerator + multiplier - 1) // multiplier
        if not 1 <= threshold < (1 << 31) - 1:
            raise ValueError("Quantization threshold does not fit fixture range")
        boundary_metadata.append({"unclipped_target": target, "first_input": threshold})
        fixtures.extend((f"transition_to_{target}_{name}", threshold + delta)
                        for name, delta in (("before", -1), ("at", 0), ("after", 1)))
    quant_input = [value for _, value in fixtures]
    quant_golden = [requant_scalar(value, multiplier, shift) for value in quant_input]
    if len(quant_input) != 24 or quant_golden != requant_numpy(quant_input, multiplier, shift):
        raise ValueError("Scalar and NumPy 24-case requantization disagree")
    for boundary in boundary_metadata:
        target, threshold = boundary["unclipped_target"], boundary["first_input"]
        if target <= 127:
            if requant_scalar(threshold - 1, multiplier, shift) != target - 1 or requant_scalar(threshold, multiplier, shift) != target:
                raise ValueError("Invalid round transition fixture")
        elif requant_scalar(threshold + 1, multiplier, shift) != 127:
            raise ValueError("Saturation fixture failed")

    pool_input = [[127, 3, 2, 1], [4, 126, 2, 0], [9, 5, 125, 7], [1, 3, 7, 124],
                  [42, 42, 3, 42], [0, 0, 0, 0], [127, 127, 127, 127], [11, 62, 61, 12]]
    pool_labels = ["lane0_unique_max", "lane1_unique_max", "lane2_unique_max", "lane3_unique_max",
                   "ties", "all_zero", "all_127", "mixed_values"]
    pool_golden = [max(values) for values in pool_input]
    if pool_golden != np.asarray(pool_input, dtype=np.int64).max(axis=1).tolist():
        raise ValueError("Scalar and NumPy eight-case pooling disagree")
    payload = dict(tile)
    payload.update({
        "kind": "AIRCRAFT_CNN_REAL_CONV1_POOL_V1",
        "scope": "16 channels x four adjacent Conv1 positions -> 64 ReLU/Q24 values -> 16 stride-2 maxpool values, plus separate CPU arithmetic fixtures",
        "requant_golden": real_requant, "pool_golden": real_pool,
        "pool_row": args.row // 2, "pool_col": args.col // 2,
        "requant_rule": "min(127, ((max(0,acc) * multiplier) + 2^(shift-1)) >> shift); INT64 intermediate",
        "pool_rule": "max of lanes 0,1,2,3 for each output channel; top-left convolution position is even",
        "quant_case_count": len(quant_input), "quant_case_input": quant_input,
        "quant_case_golden": quant_golden, "quant_case_labels": [label for label, _ in fixtures],
        "quant_round_boundaries": boundary_metadata,
        "pool_case_count": len(pool_input), "pool_case_input": pool_input,
        "pool_case_golden": pool_golden, "pool_case_labels": pool_labels,
        "fixture_scope": "synthetic CPU arithmetic tests; not additional model outputs",
        "cpu_result_layout": {"raw_partials": [0, 127], "raw_accumulators": [128, 191],
                              "real_requant": [192, 255], "real_pool": [256, 271],
                              "quant_fixtures": [272, 295], "pool_fixtures": [296, 303]},
        "cpu_result_words": 304,
        "independent_numpy_scalar_requant_matches": 64 + len(quant_input),
        "independent_numpy_scalar_pool_matches": 16 + len(pool_input),
    })
    payload["provenance"] = dict(tile["provenance"])
    payload["provenance"].update({
        "pool_generator": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__).resolve())},
        "pool_integer_reference": {"path": str(base / "verify_full_cnn.py"), "sha256": sha256(base / "verify_full_cnn.py")},
        "raw_tile_json": {"path": str(tile_path), "sha256": sha256(tile_path)},
        "raw_tile_header": {"path": str(tile_header_path), "sha256": sha256(tile_header_path)},
    })
    header = [
        "/* Generated by prepare_conv1_pool.py; see conv1_pool.json for provenance. */",
        "#ifndef CONV1_POOL_DATA_H", "#define CONV1_POOL_DATA_H", "#include <stdint.h>",
        '#include "conv1_tile_data.h"',
        f"#define C1B_REQUANT_MULT {multiplier}", f"#define C1B_REQUANT_SHIFT {shift}",
        "enum { C1B_QUANT_CASES = 24, C1B_POOL_CASES = 8, C1B_RESULT_WORDS = 304 };",
        c_array("C1B_REQUANT_GOLDEN", "uint32_t", real_requant),
        c_array("C1B_POOL_GOLDEN", "uint32_t", real_pool),
        "/* The following synthetic cases test CPU arithmetic independently of image data. */",
        c_array("C1B_QUANT_CASE_INPUT", "int32_t", quant_input),
        c_array("C1B_QUANT_CASE_GOLDEN", "uint32_t", quant_golden),
        "static const uint32_t C1B_POOL_CASE_INPUT[8][4] = {\n"
        + "\n".join("    { " + ", ".join(map(str, values)) + " }," for values in pool_input) + "\n};",
        c_array("C1B_POOL_CASE_GOLDEN", "uint32_t", pool_golden), "#endif", "",
    ]
    json_path, header_path = out / "conv1_pool.json", out / "conv1_pool_data.h"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    header_path.write_text("\n\n".join(header))
    print(json.dumps({"status": "PASS", "sample": payload["sample_relative_path"],
                      "raw_accumulators": 64, "requant_values": 64, "pool_values": 16,
                      "pool_golden": real_pool, "quant_cases": 24, "pool_cases": 8,
                      "result_words": 304, "header": str(header_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print("CONV1_POOL_PREPARE_FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
