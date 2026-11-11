#!/usr/bin/env python3
"""Prepare one real AircraftCNN Conv1 dot product for the RISC-V PE test.

Run with the task2-cnn Python environment.  No provider module is imported:
dataset_loader.py creates loaders and reads batches at import time.  The sample
ordering, seed-42 split and preprocessing below reproduce that module explicitly.
Only the requested output directory is written; the task2 base stays read-only.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np
from PIL import Image
import PIL
import torch
import torch.nn.functional as F
import torchvision
from torchvision import transforms


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_validation_sample(base, validation_index):
    """Match AircraftDataset and random_split without importing either script."""
    dataset_dir = base / "dataset"
    classes = sorted(p.name for p in dataset_dir.iterdir() if p.is_dir())
    samples = []
    for label, class_name in enumerate(classes):
        paths = sorted(
            p for p in (dataset_dir / class_name).iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        if class_name == "C-17":
            indices = torch.randperm(
                len(paths), generator=torch.Generator().manual_seed(42)
            )[:620].tolist()
            paths = [paths[index] for index in indices]
        samples.extend((path, label) for path in paths)
    order = torch.randperm(
        len(samples), generator=torch.Generator().manual_seed(42)
    ).tolist()
    train_size = int(0.7 * len(samples))
    validation = order[train_size:]
    if not 0 <= validation_index < len(validation):
        raise ValueError(f"validation-index must be 0..{len(validation) - 1}")
    dataset_index = validation[validation_index]
    image_path, label = samples[dataset_index]
    return image_path, label, classes, dataset_index, len(samples), len(validation)


def sign_magnitude(value):
    value = int(value)
    if not -127 <= value <= 127:
        raise ValueError(f"PE operand cannot represent {value}")
    return value if value >= 0 else 0x80 | (-value)


def c_array(name, c_type, values, hexadecimal=False):
    formatted = [f"0x{int(value):02x}" if hexadecimal else str(int(value))
                 for value in values]
    lines = [f"static const {c_type} {name}[{len(values)}] = {{"]
    for start in range(0, len(formatted), 9):
        lines.append("    " + ", ".join(formatted[start:start + 9]) + ",")
    lines.append("};")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task2-root", type=Path,
                        default=Path.home() / "contest/task2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validation-index", type=int, default=0)
    parser.add_argument("--output-channel", type=int, default=0)
    parser.add_argument("--row", type=int, default=0)
    parser.add_argument("--col", type=int, default=0)
    args = parser.parse_args()
    torch.set_num_threads(2)
    base = (args.task2_root / "base").resolve(strict=True)
    out = args.output_dir.resolve()
    if out == base or base in out.parents:
        raise ValueError("output-dir must not be inside task2/base")
    if not 0 <= args.output_channel < 32:
        raise ValueError("output-channel must be 0..31")
    if not (0 <= args.row < 128 and 0 <= args.col < 128):
        raise ValueError("row and col must be 0..127")

    image_path, label, classes, dataset_index, total, val_total = (
        selected_validation_sample(base, args.validation_index)
    )
    preprocessing = transforms.Compose([
        transforms.Resize((130, 130)), transforms.ToTensor()
    ])
    with Image.open(image_path) as source:
        image_float = preprocessing(source.convert("RGB"))
    # Match evaluate_INT8_cnn_fixed.integer_forward: promote the ToTensor
    # float32 result to float64 before round-to-nearest-even and saturation.
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
    encoded = np.array([sign_magnitude(w) for w in weight_q.flat], dtype=np.uint8)
    if not np.array_equal(encoded.reshape(weight_q.shape), weight_hw):
        raise ValueError("Provider conv1_weight_hw does not encode weight_q")

    oc, row, col = args.output_channel, args.row, args.col
    terms = []
    for channel in range(3):
        for kernel_row in range(3):
            for kernel_col in range(3):
                input_q = int(image_q[channel, row + kernel_row, col + kernel_col])
                logical_weight = int(weight_q[oc, channel, kernel_row, kernel_col])
                terms.append({
                    "k": len(terms), "input_channel": channel,
                    "kernel_row": kernel_row, "kernel_col": kernel_col,
                    "image_row": row + kernel_row, "image_col": col + kernel_col,
                    "input_q": input_q, "input_hw": sign_magnitude(input_q),
                    "weight_q": logical_weight,
                    "weight_hw": int(weight_hw[oc, channel, kernel_row, kernel_col]),
                    "product": input_q * logical_weight,
                })
    partial = [sum(term["product"] for term in terms[:16]),
               sum(term["product"] for term in terms[16:])]
    bias = int(biases[oc])
    mac = sum(partial)
    accumulator = mac + bias
    # Independent reference: PyTorch full valid 2D convolution for this channel.
    # Every product and sum here is an exactly representable integer in float64.
    conv = F.conv2d(
        image_q.unsqueeze(0).to(torch.float64),
        torch.from_numpy(weight_q[oc:oc + 1].copy()).to(torch.float64),
        torch.tensor([bias], dtype=torch.float64), stride=1, padding=0,
    )
    independent = float(conv[0, 0, row, col])
    if independent != accumulator or not independent.is_integer():
        raise ValueError(f"Independent Conv2D disagrees: {independent} != {accumulator}")
    if any(not -(1 << 31) <= value < (1 << 31)
           for value in [*partial, bias, mac, accumulator]):
        raise ValueError("Golden does not fit int32_t")
    batches = []
    for batch, start in enumerate((0, 16)):
        selected = terms[start:start + 16]
        batches.append({
            "batch": batch, "term_start": start, "active_rows": len(selected),
            "input_hw": [t["input_hw"] for t in selected] + [0] * (16 - len(selected)),
            "weight_hw": [t["weight_hw"] for t in selected] + [0] * (16 - len(selected)),
            "partial_golden": partial[batch],
        })
    provenance_paths = {
        "image": image_path, "quantized_model": npz_path,
        "dataset_loader": base / "src/dataset_loader.py",
        "integer_reference": base / "evaluate_INT8_cnn_fixed.py",
        "generator": Path(__file__).resolve(),
    }
    provenance = {
        name: {"path": str(path), "sha256": sha256(path)}
        for name, path in provenance_paths.items()
    }
    provenance["quantized_image_chw_int64_le"] = {
        "sha256": hashlib.sha256(image_q.numpy().astype("<i8").tobytes()).hexdigest(),
        "shape": [3, 130, 130],
    }
    payload = {
        "kind": "AIRCRAFT_CNN_REAL_CONV1_POINT_V1",
        "scope": "one raw INT32 Conv1 accumulator before ReLU/requantization/pooling",
        "validation_index": args.validation_index, "dataset_index": dataset_index,
        "dataset_samples": total, "validation_samples": val_total,
        "class_label": label, "class_name": classes[label], "classes": classes,
        "sample_relative_path": image_path.relative_to(base).as_posix(),
        "output_channel": oc, "output_row": row, "output_col": col,
        "flatten_order": "input_channel, kernel_row, kernel_col (C, KH, KW)",
        "preprocessing": "RGB; torchvision Resize((130,130)) bilinear; ToTensor float32; float64 round(127*X); clip[0,127]",
        "pe_operand_encoding": "bit7 sign, bits6:0 magnitude; zero is 0x00",
        "terms": terms, "batches": batches, "partial_golden": partial,
        "bias": bias, "mac_golden": mac, "accumulator_golden": accumulator,
        "independent_torch_conv2d_golden": int(independent),
        "negative_weights": sum(term["weight_q"] < 0 for term in terms),
        "positive_weights": sum(term["weight_q"] > 0 for term in terms),
        "requant_multiplier": requant_mult, "requant_shift": requant_shift,
        "provenance": provenance,
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "torch": torch.__version__, "torchvision": torchvision.__version__,
                        "pillow": PIL.__version__},
    }
    header = [
        "/* Generated by prepare_conv1_point.py; see conv1_point.json for provenance. */",
        "#ifndef CONV1_POINT_DATA_H", "#define CONV1_POINT_DATA_H", "#include <stdint.h>",
        "enum { C1P_TERMS = 27, C1P_BATCHES = 2, C1P_PE_ROWS = 16 };",
        f"/* {payload['sample_relative_path']}; output channel={oc}, row={row}, col={col}. */",
        c_array("C1P_INPUT_HW", "uint8_t", [t["input_hw"] for t in terms], True),
        c_array("C1P_WEIGHT_HW", "uint8_t", [t["weight_hw"] for t in terms], True),
        c_array("C1P_WEIGHT_Q", "int8_t", [t["weight_q"] for t in terms]),
        c_array("C1P_PARTIAL_GOLDEN", "int32_t", partial),
        f"static const int32_t C1P_BIAS = {bias};",
        f"static const int32_t C1P_MAC_GOLDEN = {mac};",
        f"static const int32_t C1P_ACC_GOLDEN = {accumulator};",
        "#endif", "",
    ]
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "conv1_point.json"
    header_path = out / "conv1_point_data.h"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    header_path.write_text("\n\n".join(header))
    print(json.dumps({"status": "PASS", "sample": payload["sample_relative_path"],
                      "output_channel": oc, "row": row, "col": col,
                      "partial_golden": partial, "bias": bias,
                      "accumulator_golden": accumulator, "independent_conv2d_match": True,
                      "header": str(header_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print("CONV1_POINT_PREPARE_FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
