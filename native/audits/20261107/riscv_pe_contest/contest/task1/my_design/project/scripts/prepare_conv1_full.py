#!/usr/bin/env python3
"""Prepare a whole real image for Conv1/INT8/pool execution on the SoC.

Firmware receives only input pixels, sign-magnitude weights and biases.
Independent expected results are binary host-side files, never CPU tables.
Tile order: channel_group, pooled_row, pooled_col; lanes are the 2x2 pixels.
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


def tile_order(values):
    """CHW32x128x128 -> tiles8192x64, each column then four spatial lanes."""
    if values.shape != (32, 128, 128):
        raise ValueError("Unexpected full Conv1 tensor shape")
    return values.reshape(2, 16, 64, 2, 64, 2).transpose(0, 2, 4, 1, 3, 5).reshape(8192, 64)


def c_input(values):
    lines = ["static const uint8_t C1F_INPUT_HW[3][130][130] = {"]
    for channel in values:
        lines.append("    {")
        for row in channel:
            lines.append("        { " + ", ".join(str(int(x)) for x in row) + " },")
        lines.append("    },")
    lines.append("};")
    return "\n".join(lines)


def c_weights(values):
    lines = ["static const uint8_t C1F_WEIGHT_HW[27][32] = {"]
    for row in values:
        lines.append("    { " + ", ".join(f"0x{int(x):02x}" for x in row) + " },")
    lines.append("};")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task2-root", type=Path,
                        default=Path.home() / "contest/task2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validation-index", type=int, default=0)
    args = parser.parse_args()
    torch.set_num_threads(2)
    base = (args.task2_root / "base").resolve(strict=True)
    out = args.output_dir.resolve()
    if out == base or base in out.parents:
        raise ValueError("output-dir must not be inside task2/base")
    image_path, label, classes, dataset_index, total, val_total = (
        point.selected_validation_sample(base, args.validation_index)
    )
    preprocess = transforms.Compose([
        transforms.Resize((130, 130)), transforms.ToTensor()
    ])
    with Image.open(image_path) as image:
        image_float = preprocess(image.convert("RGB"))
    image_q = torch.clamp(torch.round(image_float.to(torch.float64) * 127.0), 0, 127).to(torch.int64)
    input_q = image_q.numpy()
    npz_path = base / "quant_output/aircraft_cnn_quantized.npz"
    with np.load(npz_path, allow_pickle=False) as archive:
        weights = archive["conv1_weight_q"].astype(np.int64)
        hardware = archive["conv1_weight_hw"].astype(np.uint8)
        biases = archive["conv1_bias_q"].astype(np.int64)
        multiplier = int(archive["conv1_requant_mult"])
        shift = int(archive["conv1_requant_shift"])
    if weights.shape != (32, 3, 3, 3) or biases.shape != (32,):
        raise ValueError("Unexpected Conv1 tensor shapes")
    encoded = np.array([point.sign_magnitude(x) for x in weights.flat], dtype=np.uint8).reshape(weights.shape)
    if not np.array_equal(hardware, encoded):
        raise ValueError("Provider hardware weights do not encode the logical weights")
    if shift != 24 or not 0 < multiplier < (1 << 31):
        raise ValueError("Expected a positive INT32 Q24 Conv1 multiplier")
    # Independent integer matrix evaluation of every convolution window.
    # Sliding window order is C,Hout,Wout,KH,KW; flatten uses C,KH,KW.
    windows = np.lib.stride_tricks.sliding_window_view(input_q, (3, 3), axis=(1, 2))
    patches = windows.transpose(0, 3, 4, 1, 2).reshape(27, 128 * 128)
    flattened_weights = weights.reshape(32, 27)
    partial_chw = [
        (flattened_weights[:, :16] @ patches[:16]).reshape(32, 128, 128),
        (flattened_weights[:, 16:] @ patches[16:]).reshape(32, 128, 128),
    ]
    raw_chw = partial_chw[0] + partial_chw[1] + biases[:, None, None]
    for values in [*partial_chw, raw_chw]:
        if values.min() < -(1 << 31) or values.max() >= (1 << 31):
            raise ValueError("Full Conv1 accumulator does not fit INT32")
    # Float64 convolution exactly represents this model's integer products and
    # sums, and uses the original NCHW tensors independently of our K slicing.
    independent = F.conv2d(
        image_q.unsqueeze(0).to(torch.float64),
        torch.from_numpy(weights.copy()).to(torch.float64),
        torch.from_numpy(biases.copy()).to(torch.float64), stride=1, padding=0,
    )[0].numpy()
    if not np.array_equal(independent, raw_chw):
        raise ValueError("Full PyTorch Conv2D and NumPy integer results disagree")
    requant_chw = np.clip(
        (np.maximum(raw_chw, 0) * multiplier + (1 << (shift - 1))) >> shift, 0, 127
    ).astype(np.uint8)
    pool_chw = requant_chw.reshape(32, 64, 2, 64, 2).max(axis=(2, 4)).astype(np.uint8)
    # Scalar divmod checks all values independently of the shift expression,
    # and independent torch pooling checks spatial grouping over the full map.
    scalar_requant = []
    denominator = 1 << shift
    for value in raw_chw.flat:
        positive = max(0, int(value))
        whole, remainder = divmod(positive * multiplier, denominator)
        scalar_requant.append(min(127, whole + int(remainder * 2 >= denominator)))
    if not np.array_equal(np.asarray(scalar_requant, dtype=np.uint8).reshape(raw_chw.shape), requant_chw):
        raise ValueError("Full scalar and NumPy requantization disagree")
    independent_pool = F.max_pool2d(torch.from_numpy(requant_chw.astype(np.float64)), 2).numpy()
    if not np.array_equal(independent_pool, pool_chw):
        raise ValueError("Full PyTorch and NumPy max-pooling disagree")
    partial_tiles = np.stack([tile_order(values) for values in partial_chw], axis=1)
    raw_tiles = tile_order(raw_chw)
    requant_tiles = tile_order(requant_chw)
    hardware_by_k = hardware.reshape(32, 27).T

    out.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    def binary(name, values, dtype, shape_description):
        path = out / name
        array = np.asarray(values, dtype=dtype)
        array.tofile(path)
        artifacts[name] = {"path": str(path), "sha256": point.sha256(path),
                           "dtype": str(np.dtype(dtype)), "shape": list(array.shape),
                           "bytes": path.stat().st_size, "layout": shape_description}
    binary("input.bin", input_q, "u1", "C,H,W = 3,130,130; PE nonnegative sign-magnitude bytes")
    binary("weights_hw.bin", hardware_by_k, "u1", "K,output_channel = 27,32; sign-magnitude")
    binary("bias.bin", biases, "<i4", "output_channel = 32; little-endian signed INT32")
    binary("partials.bin", partial_tiles, "<i4", "tile,batch,column*4+lane = 8192,2,64; little-endian signed INT32")
    binary("raw.bin", raw_tiles, "<i4", "tile,column*4+lane = 8192,64; bias included once")
    binary("requant.bin", requant_tiles, "u1", "tile,column*4+lane = 8192,64; ReLU/Q24/clamp[0,127]")
    binary("pool.bin", pool_chw, "u1", "output_channel,pool_row,pool_col = 32,64,64")

    header = [
        "/* Full real-image Conv1 operands only; independent goldens stay on the host. */",
        "#ifndef CONV1_FULL_DATA_H", "#define CONV1_FULL_DATA_H", "#include <stdint.h>",
        "enum { C1F_INPUT_CHANNELS=3, C1F_INPUT_H=130, C1F_INPUT_W=130,",
        "       C1F_CONV_CHANNELS=32, C1F_CONV_H=128, C1F_CONV_W=128,",
        "       C1F_POOL_H=64, C1F_POOL_W=64, C1F_TERMS=27, C1F_COLS=16, C1F_LANES=4,",
        "       C1F_TOTAL_TILES=8192, C1F_POOL_PIXELS=4096, C1F_OUTPUT_BYTES=131072 };",
        f"#define C1F_REQUANT_MULT {multiplier}", f"#define C1F_REQUANT_SHIFT {shift}",
        c_input(input_q), c_weights(hardware_by_k),
        point.c_array("C1F_BIAS", "int32_t", biases.tolist()), "#endif", "",
    ]
    header_path = out / "conv1_full_data.h"
    header_path.write_text("\n\n".join(header))
    artifacts[header_path.name] = {"path": str(header_path), "sha256": point.sha256(header_path),
                                   "bytes": header_path.stat().st_size,
                                   "scope": "firmware operands only; no expected-output arrays"}
    provenance_paths = {
        "image": image_path, "quantized_model": npz_path,
        "dataset_loader": base / "src/dataset_loader.py",
        "integer_reference": base / "evaluate_INT8_cnn_fixed.py",
        "pool_integer_reference": base / "verify_full_cnn.py",
        "generator": Path(__file__).resolve(),
        "point_generator_helpers": Path(point.__file__).resolve(),
    }
    provenance = {name: {"path": str(path), "sha256": point.sha256(path)}
                  for name, path in provenance_paths.items()}
    provenance["quantized_image_chw_int64_le"] = {
        "sha256": hashlib.sha256(input_q.astype("<i8").tobytes()).hexdigest(),
        "shape": [3, 130, 130],
    }
    payload = {
        "kind": "AIRCRAFT_CNN_REAL_CONV1_FULL_V1",
        "scope": "one image; all 32 Conv1 channels and 128x128 positions, then ReLU/Q24 and 2x2 maxpool",
        "validation_index": args.validation_index, "dataset_index": dataset_index,
        "dataset_samples": total, "validation_samples": val_total,
        "class_label": label, "class_name": classes[label], "classes": classes,
        "sample_relative_path": image_path.relative_to(base).as_posix(),
        "input_shape": [3, 130, 130], "conv_shape": [32, 128, 128], "pool_shape": [32, 64, 64],
        "tile_count": 8192, "groups": 2, "terms": 27, "batches": [16, 11],
        "tile_order": "tile_id=channel_group*4096+pool_row*64+pool_col; channel_group0/1 selects channels0..15/16..31",
        "lane_order": "(2*pool_row,2*pool_col),(2*pool_row,2*pool_col+1),(2*pool_row+1,2*pool_col),(2*pool_row+1,2*pool_col+1)",
        "output_index_order": "column*4+lane within each tile; global pool output is CHW",
        "flatten_order": "input_channel,kernel_row,kernel_col (C,KH,KW)",
        "weight_sram_slot": "slot=channel_group*2+batch; each slot row holds K=batch*16+row; batch1 rows11..15=0",
        "preprocessing": "RGB; torchvision Resize((130,130)) bilinear; ToTensor float32; float64 round(127*X); clip[0,127]",
        "pe_operand_encoding": "bit7 sign,bits6:0 magnitude; input activations0..127 are direct bytes",
        "requant_multiplier": multiplier, "requant_shift": shift,
        "requant_rule": "min(127, ((max(0,acc)*multiplier)+2^(shift-1))>>shift), INT64 intermediate",
        "host_golden_only": True, "firmware_golden_arrays": False,
        "raw_values": 524288, "partial_values": 1048576, "pool_values": 131072,
        "raw_min": int(raw_chw.min()), "raw_max": int(raw_chw.max()),
        "requant_min": int(requant_chw.min()), "requant_max": int(requant_chw.max()),
        "pool_min": int(pool_chw.min()), "pool_max": int(pool_chw.max()),
        "independent_torch_conv2d_matches": int(raw_chw.size),
        "independent_scalar_requant_matches": int(raw_chw.size),
        "independent_torch_pool_matches": int(pool_chw.size),
        "provenance": provenance, "artifacts": artifacts,
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "torch": torch.__version__, "torchvision": torchvision.__version__,
                        "pillow": PIL.__version__},
    }
    json_path = out / "conv1_full.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "sample": payload["sample_relative_path"],
                      "tiles": 8192, "raw_values": 524288, "pool_values": 131072,
                      "raw_range": [int(raw_chw.min()), int(raw_chw.max())],
                      "pool_sha256": artifacts["pool.bin"]["sha256"],
                      "header": str(header_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print("CONV1_FULL_PREPARE_FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
