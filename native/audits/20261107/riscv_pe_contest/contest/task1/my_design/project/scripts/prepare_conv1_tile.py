#!/usr/bin/env python3
"""Prepare 16 Conv1 output channels at four adjacent real-image positions.

The four lanes are (row,col), (row,col+1), (row+1,col), (row+1,col+1).
Each output is the raw INT32 accumulator before ReLU, requantization or pooling.
Only the selected output directory is written; provider data stays read-only.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np
import PIL
from PIL import Image
import torch
import torch.nn.functional as F
import torchvision
from torchvision import transforms

import prepare_conv1_point as point


def pack_bytes(values):
    return sum(int(value) << (8 * index) for index, value in enumerate(values))


def c_matrix(name, c_type, values, hexadecimal=False):
    rows = []
    for row in values:
        entries = [f"0x{int(value):02x}" if hexadecimal else str(int(value))
                   for value in row]
        rows.append("    { " + ", ".join(entries) + " },")
    return (f"static const {c_type} {name}[{len(values)}][{len(values[0])}] = {{\n"
            + "\n".join(rows) + "\n};")


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
    torch.set_num_threads(2)
    base = (args.task2_root / "base").resolve(strict=True)
    out = args.output_dir.resolve()
    if out == base or base in out.parents:
        raise ValueError("output-dir must not be inside task2/base")
    if not (0 <= args.row <= 126 and 0 <= args.col <= 126):
        raise ValueError("row and col must be 0..126 for a 2x2 output tile")

    image_path, label, classes, dataset_index, total, val_total = (
        point.selected_validation_sample(base, args.validation_index)
    )
    preprocessing = transforms.Compose([
        transforms.Resize((130, 130)), transforms.ToTensor()
    ])
    with Image.open(image_path) as source:
        image_float = preprocessing(source.convert("RGB"))
    image_q = torch.clamp(
        torch.round(image_float.to(torch.float64) * 127.0), 0, 127
    ).to(torch.int64)
    npz_path = base / "quant_output/aircraft_cnn_quantized.npz"
    with np.load(npz_path, allow_pickle=False) as archive:
        weight_q = archive["conv1_weight_q"].astype(np.int64)
        weight_hw = archive["conv1_weight_hw"].astype(np.uint8)
        biases = archive["conv1_bias_q"].astype(np.int64)
        requant_mult = int(archive["conv1_requant_mult"])
        requant_shift = int(archive["conv1_requant_shift"])
    if weight_q.shape != (32, 3, 3, 3) or biases.shape != (32,):
        raise ValueError("Unexpected Conv1 tensor shapes")
    encoded = np.array([point.sign_magnitude(w) for w in weight_q.flat], dtype=np.uint8)
    if not np.array_equal(encoded.reshape(weight_q.shape), weight_hw):
        raise ValueError("Provider conv1_weight_hw does not encode weight_q")

    oc_start, row, col = args.output_channel_start, args.row, args.col
    positions = [(row, col), (row, col + 1), (row + 1, col), (row + 1, col + 1)]
    terms = []
    for channel in range(3):
        for kernel_row in range(3):
            for kernel_col in range(3):
                inputs = [int(image_q[channel, r + kernel_row, c + kernel_col])
                          for r, c in positions]
                weights = [int(weight_q[oc_start + output, channel, kernel_row, kernel_col])
                           for output in range(16)]
                hardware = [int(weight_hw[oc_start + output, channel, kernel_row, kernel_col])
                            for output in range(16)]
                terms.append({
                    "k": len(terms), "input_channel": channel,
                    "kernel_row": kernel_row, "kernel_col": kernel_col,
                    "input_q": inputs,
                    "input_hw": [point.sign_magnitude(x) for x in inputs],
                    "weight_q": weights, "weight_hw": hardware,
                    "products": [inputs[lane] * weights[output]
                                 for output in range(16) for lane in range(4)],
                })
    partial = [
        [sum(term["products"][index] for term in terms[start:start + 16])
         for index in range(64)]
        for start in (0, 16)
    ]
    bias = [int(value) for value in biases[oc_start:oc_start + 16]]
    mac = [partial[0][index] + partial[1][index] for index in range(64)]
    accumulator = [mac[index] + bias[index // 4] for index in range(64)]
    # An independent full valid convolution uses all 130x130 input data and
    # the original 16 filters, not the term list or generated packed words.
    conv = F.conv2d(
        image_q.unsqueeze(0).to(torch.float64),
        torch.from_numpy(weight_q[oc_start:oc_start + 16].copy()).to(torch.float64),
        torch.tensor(bias, dtype=torch.float64), stride=1, padding=0,
    )
    independent = [float(conv[0, output, r, c])
                   for output in range(16) for r, c in positions]
    if any(not value.is_integer() or value != accumulator[index]
           for index, value in enumerate(independent)):
        raise ValueError("Independent full Conv2D disagrees with the 64 scalar accumulators")
    all_values = [value for batch in partial for value in batch] + bias + mac + accumulator
    if any(not -(1 << 31) <= value < (1 << 31) for value in all_values):
        raise ValueError("Golden does not fit int32_t")

    batches = []
    for batch, start in enumerate((0, 16)):
        selected = terms[start:start + 16]
        inputs = [term["input_hw"] for term in selected] + [[0] * 4 for _ in range(16 - len(selected))]
        weights = [term["weight_hw"] for term in selected] + [[0] * 16 for _ in range(16 - len(selected))]
        batches.append({
            "batch": batch, "term_start": start, "active_rows": len(selected),
            "input_hw": inputs, "weight_hw": weights,
            "input_words": [pack_bytes(values) for values in inputs],
            "weight_words": [[pack_bytes(values[word * 4:word * 4 + 4])
                              for word in range(4)] for values in weights],
            "partial_golden": partial[batch],
        })
    provenance_paths = {
        "image": image_path, "quantized_model": npz_path,
        "dataset_loader": base / "src/dataset_loader.py",
        "integer_reference": base / "evaluate_INT8_cnn_fixed.py",
        "generator": Path(__file__).resolve(),
        "point_generator_helpers": Path(point.__file__).resolve(),
    }
    provenance = {
        name: {"path": str(path), "sha256": point.sha256(path)}
        for name, path in provenance_paths.items()
    }
    provenance["quantized_image_chw_int64_le"] = {
        "sha256": hashlib.sha256(image_q.numpy().astype("<i8").tobytes()).hexdigest(),
        "shape": [3, 130, 130],
    }
    payload = {
        "kind": "AIRCRAFT_CNN_REAL_CONV1_TILE_V1",
        "scope": "16 channels x 4 spatial positions; raw INT32 accumulators before ReLU/requantization/pooling",
        "validation_index": args.validation_index, "dataset_index": dataset_index,
        "dataset_samples": total, "validation_samples": val_total,
        "class_label": label, "class_name": classes[label], "classes": classes,
        "sample_relative_path": image_path.relative_to(base).as_posix(),
        "output_channel_start": oc_start, "output_channels": list(range(oc_start, oc_start + 16)),
        "row": row, "col": col, "lane_positions": positions,
        "output_index_order": "column * 4 + lane; column maps to output_channel_start + column",
        "flatten_order": "input_channel, kernel_row, kernel_col (C, KH, KW)",
        "preprocessing": "RGB; torchvision Resize((130,130)) bilinear; ToTensor float32; float64 round(127*X); clip[0,127]",
        "pe_operand_encoding": "bit7 sign, bits6:0 magnitude; zero is 0x00",
        "packing": "little-endian bytes; input word bytes are lanes0..3; each weight word bytes are four consecutive columns",
        "terms": terms, "batches": batches, "partial_golden": partial,
        "bias": bias, "mac_golden": mac, "accumulator_golden": accumulator,
        "independent_torch_conv2d_golden": [int(value) for value in independent],
        "negative_weights": sum(value < 0 for term in terms for value in term["weight_q"]),
        "positive_weights": sum(value > 0 for term in terms for value in term["weight_q"]),
        "positive_accumulators": sum(value > 0 for value in accumulator),
        "negative_accumulators": sum(value < 0 for value in accumulator),
        "requant_multiplier": requant_mult, "requant_shift": requant_shift,
        "provenance": provenance,
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "torch": torch.__version__, "torchvision": torchvision.__version__,
                        "pillow": PIL.__version__},
    }
    header = [
        "/* Generated by prepare_conv1_tile.py; see conv1_tile.json for provenance. */",
        "#ifndef CONV1_TILE_DATA_H", "#define CONV1_TILE_DATA_H", "#include <stdint.h>",
        "enum { C1T_COLS = 16, C1T_LANES = 4, C1T_TERMS = 27, C1T_BATCHES = 2, C1T_OUTPUTS = 64 };",
        f"/* {payload['sample_relative_path']}; channels {oc_start}..{oc_start + 15}; 2x2 tile at ({row},{col}). */",
        c_matrix("C1T_INPUT_HW", "uint8_t", [term["input_hw"] for term in terms], True),
        c_matrix("C1T_WEIGHT_HW", "uint8_t", [term["weight_hw"] for term in terms], True),
        c_matrix("C1T_PARTIAL_GOLDEN", "int32_t", partial),
        point.c_array("C1T_BIAS", "int32_t", bias),
        point.c_array("C1T_ACC_GOLDEN", "int32_t", accumulator),
        "#endif", "",
    ]
    out.mkdir(parents=True, exist_ok=True)
    json_path, header_path = out / "conv1_tile.json", out / "conv1_tile_data.h"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    header_path.write_text("\n\n".join(header))
    print(json.dumps({"status": "PASS", "sample": payload["sample_relative_path"],
                      "output_channel_start": oc_start, "lane_positions": positions,
                      "outputs": 64, "partial0_first": partial[0][0],
                      "partial1_first": partial[1][0], "accumulator_first": accumulator[0],
                      "accumulator_min": min(accumulator), "accumulator_max": max(accumulator),
                      "independent_conv2d_matches": 64,
                      "header": str(header_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print("CONV1_TILE_PREPARE_FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
