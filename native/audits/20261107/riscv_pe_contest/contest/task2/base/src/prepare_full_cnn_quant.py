import os
from pathlib import Path

import numpy as np
import torch

from model import AircraftCNN
from dataset_loader import val_loader


# ============================================================
# Configuration
# ============================================================

MODEL_PATH = Path("models/best_model.pth")

OUTPUT_DIR = Path("quant_output")

OUTPUT_NPZ = OUTPUT_DIR / "aircraft_cnn_quantized.npz"
OUTPUT_REPORT = OUTPUT_DIR / "quant_report.txt"


# ------------------------------------------------------------
# Input quantization
#
# Previous deployment convention:
#
#     Xq = round(127 * X)
#
# Therefore:
#
#     Sx1 = 1 / 127
# ------------------------------------------------------------

SX1 = 1.0 / 127.0


# ------------------------------------------------------------
# Fixed-point requantization
#
# We continue using Q24:
#
#     q = round(acc * M)
#
# where:
#
#     M = Sacc / Sout
#
# and:
#
#     MULT = round(M * 2^24)
#
# RISC-V:
#
#     scaled = acc * MULT
#     scaled += 1 << 23
#     q = scaled >> 24
#
# ------------------------------------------------------------

REQUANT_SHIFT = 24
REQUANT_ROUND = 1 << (REQUANT_SHIFT - 1)


# ============================================================
# Helpers
# ============================================================

def symmetric_weight_quantize(weight_fp32):
    """
    Symmetric per-tensor INT8 quantization.

        Sw = max(abs(W)) / 127

        Wq = round(W / Sw)

        Wq = clamp(Wq, -127, 127)

    Returns:
        weight_q : int8
        scale    : float
    """

    weight_np = (
        weight_fp32
        .detach()
        .cpu()
        .numpy()
        .astype(np.float64)
    )

    abs_max = np.max(
        np.abs(weight_np)
    )

    if abs_max == 0.0:

        scale = 1.0

        weight_q = np.zeros_like(
            weight_np,
            dtype=np.int8
        )

        return weight_q, scale

    scale = abs_max / 127.0

    weight_q = np.round(
        weight_np / scale
    )

    weight_q = np.clip(
        weight_q,
        -127,
        127
    )

    weight_q = weight_q.astype(
        np.int8
    )

    return weight_q, float(scale)


def quantize_bias(
    bias_fp32,
    input_scale,
    weight_scale
):
    """
    Bias is accumulated in the same INT32 domain as:

        Xq * Wq

    Therefore:

        Sb = Sx * Sw

        Bq = round(
            B_fp32 / (Sx * Sw)
        )
    """

    bias_np = (
        bias_fp32
        .detach()
        .cpu()
        .numpy()
        .astype(np.float64)
    )

    bias_scale = (
        input_scale *
        weight_scale
    )

    bias_q = np.round(
        bias_np / bias_scale
    )

    bias_q = bias_q.astype(
        np.int64
    )

    # Make sure values fit int32.
    int32_min = np.iinfo(
        np.int32
    ).min

    int32_max = np.iinfo(
        np.int32
    ).max

    if np.any(bias_q < int32_min) or \
       np.any(bias_q > int32_max):

        raise RuntimeError(
            "Bias quantization overflowed int32"
        )

    return bias_q.astype(np.int32)


def activation_scale_from_max(max_value):
    """
    ReLU output is non-negative.

    Quantization:

        q = round(x / Sx)

        q = clamp(q, 0, 127)

    Therefore:

        Sx = max / 127
    """

    if max_value <= 0.0:

        raise RuntimeError(
            "Invalid activation max: "
            f"{max_value}"
        )

    return float(
        max_value / 127.0
    )


def calculate_requant(
    input_scale,
    weight_scale,
    output_scale
):
    """
    Conv/FC accumulator:

        acc_real =
            acc_int *
            input_scale *
            weight_scale

    Output activation:

        q_out =
            round(
                acc_real /
                output_scale
            )

    Therefore:

        M =
            input_scale *
            weight_scale /
            output_scale

    Q24:

        MULT =
            round(
                M * 2^24
            )
    """

    real_multiplier = (
        input_scale *
        weight_scale /
        output_scale
    )

    mult = int(
        round(
            real_multiplier *
            (1 << REQUANT_SHIFT)
        )
    )

    return (
        float(real_multiplier),
        mult,
        REQUANT_SHIFT,
        REQUANT_ROUND
    )


def int8_to_sign_magnitude(weight_q):
    """
    Convert normal signed INT8 weights to the PE hardware format.

    Normal signed value:
        +5
        -5

    PE byte:
        +5 -> 0x05
        -5 -> 0x85

    bit 7:
        sign

    bit 6:0:
        magnitude

    Because quantization uses [-127, 127],
    magnitude always fits in 7 bits.
    """

    weight_i16 = weight_q.astype(
        np.int16
    )

    magnitude = np.abs(
        weight_i16
    ).astype(np.uint8)

    sign = (
        weight_i16 < 0
    ).astype(np.uint8)

    weight_hw = (
        magnitude |
        (sign << 7)
    )

    return weight_hw.astype(
        np.uint8
    )


def print_tensor_stats(name, tensor):
    """
    Simple FP32 tensor statistics.
    """

    print(
        f"{name:16s}: "
        f"shape={tuple(tensor.shape)}, "
        f"min={tensor.min().item():.8f}, "
        f"max={tensor.max().item():.8f}"
    )


# ============================================================
# 1. Load model
# ============================================================

print()
print(
    "============================================================"
)
print(
    " Loading AircraftCNN"
)
print(
    "============================================================"
)

device = torch.device(
    "cuda"
    if torch.cuda.is_available()
    else "cpu"
)

model = AircraftCNN().to(
    device
)

state_dict = torch.load(
    MODEL_PATH,
    map_location=device
)

model.load_state_dict(
    state_dict
)

model.eval()

print(
    "Device     :",
    device
)

print(
    "Model      :",
    MODEL_PATH
)


# ============================================================
# 2. Print model parameter shapes
# ============================================================

print()
print(
    "============================================================"
)
print(
    " Model parameter shapes"
)
print(
    "============================================================"
)

for name, parameter in model.named_parameters():

    print(
        f"{name:24s}",
        tuple(parameter.shape)
    )


# ============================================================
# 3. Activation calibration
#
# We need:
#
# Sx2:
#   input of Conv2
#   = Pool1 output
#
# Sx3:
#   input of Conv3
#   = Pool2 output
#
# Sx4:
#   input of FC1
#   = Pool3 output / flattened
#
# Sx5:
#   input of FC2
#   = ReLU(FC1)
#
# ============================================================

print()
print(
    "============================================================"
)
print(
    " Activation calibration"
)
print(
    "============================================================"
)


stats = {

    "pool1": {
        "min": float("inf"),
        "max": float("-inf"),
        "count": 0,
    },

    "pool2": {
        "min": float("inf"),
        "max": float("-inf"),
        "count": 0,
    },

    "pool3": {
        "min": float("inf"),
        "max": float("-inf"),
        "count": 0,
    },

    "fc1_relu": {
        "min": float("inf"),
        "max": float("-inf"),
        "count": 0,
    },
}


def update_stats(name, tensor):

    batch_min = tensor.min().item()
    batch_max = tensor.max().item()

    stats[name]["min"] = min(
        stats[name]["min"],
        batch_min
    )

    stats[name]["max"] = max(
        stats[name]["max"],
        batch_max
    )

    stats[name]["count"] += (
        tensor.numel()
    )


with torch.no_grad():

    for batch_idx, (
        images,
        labels
    ) in enumerate(val_loader):

        images = images.to(
            device
        )

        # ----------------------------------------------------
        # Conv1 -> ReLU -> Pool1
        #
        # Output:
        #     [N, 32, 64, 64]
        #
        # This becomes Conv2 input.
        # ----------------------------------------------------

        x = model.conv1(
            images
        )

        x = model.relu(
            x
        )

        x = model.pool(
            x
        )

        update_stats(
            "pool1",
            x
        )

        # ----------------------------------------------------
        # Conv2 -> ReLU -> Pool2
        #
        # Output:
        #     [N, 64, 31, 31]
        #
        # This becomes Conv3 input.
        # ----------------------------------------------------

        x = model.conv2(
            x
        )

        x = model.relu(
            x
        )

        x = model.pool(
            x
        )

        update_stats(
            "pool2",
            x
        )

        # ----------------------------------------------------
        # Conv3 -> ReLU -> Pool3
        #
        # Output:
        #     [N, 32, 14, 14]
        #
        # Flatten:
        #
        #     32 * 14 * 14
        #     = 6272
        #
        # This becomes FC1 input.
        # ----------------------------------------------------

        x = model.conv3(
            x
        )

        x = model.relu(
            x
        )

        x = model.pool(
            x
        )

        update_stats(
            "pool3",
            x
        )

        # ----------------------------------------------------
        # Flatten
        # ----------------------------------------------------

        x = x.view(
            x.size(0),
            -1
        )

        # ----------------------------------------------------
        # FC1 -> ReLU
        #
        # Dropout is ignored for deployment.
        #
        # In model.eval(), Dropout itself is inactive anyway.
        #
        # This becomes FC2 input.
        # ----------------------------------------------------

        x = model.fc1(
            x
        )

        x = model.relu(
            x
        )

        update_stats(
            "fc1_relu",
            x
        )

        if (
            batch_idx % 10 == 0
        ):

            print(
                f"Batch {batch_idx:3d} calibrated"
            )


# ============================================================
# 4. Activation scales
# ============================================================

Sx2 = activation_scale_from_max(
    stats["pool1"]["max"]
)

Sx3 = activation_scale_from_max(
    stats["pool2"]["max"]
)

Sx4 = activation_scale_from_max(
    stats["pool3"]["max"]
)

Sx5 = activation_scale_from_max(
    stats["fc1_relu"]["max"]
)


print()
print(
    "============================================================"
)
print(
    " Activation calibration results"
)
print(
    "============================================================"
)


for name in [
    "pool1",
    "pool2",
    "pool3",
    "fc1_relu",
]:

    print()

    print(
        name
    )

    print(
        "  Global min :",
        stats[name]["min"]
    )

    print(
        "  Global max :",
        stats[name]["max"]
    )

    print(
        "  Values     :",
        stats[name]["count"]
    )


print()
print(
    "Activation scales:"
)

print(
    "Sx1 =",
    SX1
)

print(
    "Sx2 =",
    Sx2
)

print(
    "Sx3 =",
    Sx3
)

print(
    "Sx4 =",
    Sx4
)

print(
    "Sx5 =",
    Sx5
)


# ============================================================
# 5. Quantize all weights
# ============================================================

print()
print(
    "============================================================"
)
print(
    " Weight quantization"
)
print(
    "============================================================"
)


conv1_weight_q, Sw1 = \
    symmetric_weight_quantize(
        model.conv1.weight
    )

conv2_weight_q, Sw2 = \
    symmetric_weight_quantize(
        model.conv2.weight
    )

conv3_weight_q, Sw3 = \
    symmetric_weight_quantize(
        model.conv3.weight
    )

fc1_weight_q, Sw4 = \
    symmetric_weight_quantize(
        model.fc1.weight
    )

fc2_weight_q, Sw5 = \
    symmetric_weight_quantize(
        model.fc2.weight
    )


print(
    "Sw1 Conv1 =",
    Sw1
)

print(
    "Sw2 Conv2 =",
    Sw2
)

print(
    "Sw3 Conv3 =",
    Sw3
)

print(
    "Sw4 FC1   =",
    Sw4
)

print(
    "Sw5 FC2   =",
    Sw5
)


# ============================================================
# 6. Quantize all biases
# ============================================================

print()
print(
    "============================================================"
)
print(
    " Bias quantization"
)
print(
    "============================================================"
)


conv1_bias_q = quantize_bias(
    model.conv1.bias,
    SX1,
    Sw1
)

conv2_bias_q = quantize_bias(
    model.conv2.bias,
    Sx2,
    Sw2
)

conv3_bias_q = quantize_bias(
    model.conv3.bias,
    Sx3,
    Sw3
)

fc1_bias_q = quantize_bias(
    model.fc1.bias,
    Sx4,
    Sw4
)

fc2_bias_q = quantize_bias(
    model.fc2.bias,
    Sx5,
    Sw5
)


print(
    "Conv1 bias q range:",
    int(conv1_bias_q.min()),
    "..",
    int(conv1_bias_q.max())
)

print(
    "Conv2 bias q range:",
    int(conv2_bias_q.min()),
    "..",
    int(conv2_bias_q.max())
)

print(
    "Conv3 bias q range:",
    int(conv3_bias_q.min()),
    "..",
    int(conv3_bias_q.max())
)

print(
    "FC1 bias q range  :",
    int(fc1_bias_q.min()),
    "..",
    int(fc1_bias_q.max())
)

print(
    "FC2 bias q range  :",
    int(fc2_bias_q.min()),
    "..",
    int(fc2_bias_q.max())
)


# ============================================================
# 7. Requantization parameters
#
# Conv1 accumulator -> Conv2 input:
#
#     Sx1 * Sw1 -> Sx2
#
# Conv2 accumulator -> Conv3 input:
#
#     Sx2 * Sw2 -> Sx3
#
# Conv3 accumulator -> FC1 input:
#
#     Sx3 * Sw3 -> Sx4
#
# FC1 accumulator -> FC2 input:
#
#     Sx4 * Sw4 -> Sx5
#
# FC2 is final logits.
#
# No ReLU/requant is required after FC2 for argmax.
# ============================================================

print()
print(
    "============================================================"
)
print(
    " Q24 requantization parameters"
)
print(
    "============================================================"
)


M1, MULT1, SHIFT1, ROUND1 = \
    calculate_requant(
        SX1,
        Sw1,
        Sx2
    )


M2, MULT2, SHIFT2, ROUND2 = \
    calculate_requant(
        Sx2,
        Sw2,
        Sx3
    )


M3, MULT3, SHIFT3, ROUND3 = \
    calculate_requant(
        Sx3,
        Sw3,
        Sx4
    )


M4, MULT4, SHIFT4, ROUND4 = \
    calculate_requant(
        Sx4,
        Sw4,
        Sx5
    )


def print_requant(
    name,
    real_multiplier,
    mult,
    shift,
    round_value
):

    approx = (
        mult /
        float(1 << shift)
    )

    error = (
        approx -
        real_multiplier
    )

    print()

    print(
        name
    )

    print(
        "  Real multiplier :",
        real_multiplier
    )

    print(
        "  Q24 MULT        :",
        mult
    )

    print(
        "  SHIFT           :",
        shift
    )

    print(
        "  ROUND           :",
        round_value
    )

    print(
        "  Q24 approx      :",
        approx
    )

    print(
        "  Error           :",
        error
    )


print_requant(
    "Conv1 -> Conv2",
    M1,
    MULT1,
    SHIFT1,
    ROUND1
)

print_requant(
    "Conv2 -> Conv3",
    M2,
    MULT2,
    SHIFT2,
    ROUND2
)

print_requant(
    "Conv3 -> FC1",
    M3,
    MULT3,
    SHIFT3,
    ROUND3
)

print_requant(
    "FC1 -> FC2",
    M4,
    MULT4,
    SHIFT4,
    ROUND4
)


# ============================================================
# 8. Convert all weights to PE sign-magnitude format
# ============================================================

print()
print(
    "============================================================"
)
print(
    " PE sign-magnitude conversion"
)
print(
    "============================================================"
)


conv1_weight_hw = \
    int8_to_sign_magnitude(
        conv1_weight_q
    )

conv2_weight_hw = \
    int8_to_sign_magnitude(
        conv2_weight_q
    )

conv3_weight_hw = \
    int8_to_sign_magnitude(
        conv3_weight_q
    )

fc1_weight_hw = \
    int8_to_sign_magnitude(
        fc1_weight_q
    )

fc2_weight_hw = \
    int8_to_sign_magnitude(
        fc2_weight_q
    )


print(
    "Conv1 HW weight shape:",
    conv1_weight_hw.shape
)

print(
    "Conv2 HW weight shape:",
    conv2_weight_hw.shape
)

print(
    "Conv3 HW weight shape:",
    conv3_weight_hw.shape
)

print(
    "FC1 HW weight shape  :",
    fc1_weight_hw.shape
)

print(
    "FC2 HW weight shape  :",
    fc2_weight_hw.shape
)


# ============================================================
# 9. Sanity checks
# ============================================================

print()
print(
    "============================================================"
)
print(
    " Sanity checks"
)
print(
    "============================================================"
)


expected_shapes = {

    "conv1":
        (32, 3, 3, 3),

    "conv2":
        (64, 32, 3, 3),

    "conv3":
        (32, 64, 3, 3),

    "fc1":
        (450, 6272),

    "fc2":
        (10, 450),
}


actual_shapes = {

    "conv1":
        tuple(
            conv1_weight_q.shape
        ),

    "conv2":
        tuple(
            conv2_weight_q.shape
        ),

    "conv3":
        tuple(
            conv3_weight_q.shape
        ),

    "fc1":
        tuple(
            fc1_weight_q.shape
        ),

    "fc2":
        tuple(
            fc2_weight_q.shape
        ),
}


for name in expected_shapes:

    expected = expected_shapes[name]

    actual = actual_shapes[name]

    status = (
        "PASS"
        if actual == expected
        else "FAIL"
    )

    print(
        f"{name:6s}: "
        f"expected={expected}, "
        f"actual={actual} "
        f"{status}"
    )

    if actual != expected:

        raise RuntimeError(
            f"{name} shape mismatch"
        )


# Check the already known Conv1 numbers.

print()

print(
    "Known Conv1 reference:"
)

print(
    "Expected Sx1 =",
    1.0 / 127.0
)

print(
    "Current  Sx1 =",
    SX1
)

print(
    "Previously measured Sx2 =",
    0.013367715783006563
)

print(
    "Current             Sx2 =",
    Sx2
)

print(
    "Previously measured Sw1 =",
    0.0017061166641280407
)

print(
    "Current             Sw1 =",
    Sw1
)

print(
    "Previously used Conv1 MULT =",
    16860
)

print(
    "Current Conv1 MULT         =",
    MULT1
)


# ============================================================
# 10. Create output directory
# ============================================================

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# 11. Save complete quantized model
# ============================================================

np.savez_compressed(

    OUTPUT_NPZ,

    # --------------------------------------------------------
    # Activation scales
    # --------------------------------------------------------

    Sx1=np.float64(SX1),
    Sx2=np.float64(Sx2),
    Sx3=np.float64(Sx3),
    Sx4=np.float64(Sx4),
    Sx5=np.float64(Sx5),

    # --------------------------------------------------------
    # Weight scales
    # --------------------------------------------------------

    Sw1=np.float64(Sw1),
    Sw2=np.float64(Sw2),
    Sw3=np.float64(Sw3),
    Sw4=np.float64(Sw4),
    Sw5=np.float64(Sw5),

    # --------------------------------------------------------
    # Normal signed INT8 weights
    # --------------------------------------------------------

    conv1_weight_q=conv1_weight_q,
    conv2_weight_q=conv2_weight_q,
    conv3_weight_q=conv3_weight_q,

    fc1_weight_q=fc1_weight_q,
    fc2_weight_q=fc2_weight_q,

    # --------------------------------------------------------
    # PE sign-magnitude weights
    # --------------------------------------------------------

    conv1_weight_hw=conv1_weight_hw,
    conv2_weight_hw=conv2_weight_hw,
    conv3_weight_hw=conv3_weight_hw,

    fc1_weight_hw=fc1_weight_hw,
    fc2_weight_hw=fc2_weight_hw,

    # --------------------------------------------------------
    # INT32 biases
    # --------------------------------------------------------

    conv1_bias_q=conv1_bias_q,
    conv2_bias_q=conv2_bias_q,
    conv3_bias_q=conv3_bias_q,

    fc1_bias_q=fc1_bias_q,
    fc2_bias_q=fc2_bias_q,

    # --------------------------------------------------------
    # Requant parameters
    # --------------------------------------------------------

    conv1_requant_mult=np.int64(
        MULT1
    ),

    conv1_requant_shift=np.int32(
        SHIFT1
    ),

    conv2_requant_mult=np.int64(
        MULT2
    ),

    conv2_requant_shift=np.int32(
        SHIFT2
    ),

    conv3_requant_mult=np.int64(
        MULT3
    ),

    conv3_requant_shift=np.int32(
        SHIFT3
    ),

    fc1_requant_mult=np.int64(
        MULT4
    ),

    fc1_requant_shift=np.int32(
        SHIFT4
    ),
)


# ============================================================
# 12. Generate C quantization parameter header
# ============================================================

PARAM_HEADER = (
    OUTPUT_DIR /
    "cnn_quant_params.h"
)


with open(
    PARAM_HEADER,
    "w"
) as f:

    f.write(
        "#ifndef CNN_QUANT_PARAMS_H\n"
    )

    f.write(
        "#define CNN_QUANT_PARAMS_H\n\n"
    )

    f.write(
        "/* Auto-generated by "
        "prepare_full_cnn_quant.py */\n\n"
    )

    # --------------------------------------------------------
    # Activation scales
    #
    # Mainly documentation / Golden reference.
    # RISC-V integer inference itself uses MULT/SHIFT.
    # --------------------------------------------------------

    f.write(
        f"#define CNN_SX1 "
        f"{SX1:.17g}\n"
    )

    f.write(
        f"#define CNN_SX2 "
        f"{Sx2:.17g}\n"
    )

    f.write(
        f"#define CNN_SX3 "
        f"{Sx3:.17g}\n"
    )

    f.write(
        f"#define CNN_SX4 "
        f"{Sx4:.17g}\n"
    )

    f.write(
        f"#define CNN_SX5 "
        f"{Sx5:.17g}\n\n"
    )

    # --------------------------------------------------------
    # Conv1
    # --------------------------------------------------------

    f.write(
        f"#define CONV1_REQUANT_MULT "
        f"{MULT1}LL\n"
    )

    f.write(
        f"#define CONV1_REQUANT_SHIFT "
        f"{SHIFT1}\n"
    )

    f.write(
        f"#define CONV1_REQUANT_ROUND "
        f"(1LL << {SHIFT1 - 1})\n\n"
    )

    # --------------------------------------------------------
    # Conv2
    # --------------------------------------------------------

    f.write(
        f"#define CONV2_REQUANT_MULT "
        f"{MULT2}LL\n"
    )

    f.write(
        f"#define CONV2_REQUANT_SHIFT "
        f"{SHIFT2}\n"
    )

    f.write(
        f"#define CONV2_REQUANT_ROUND "
        f"(1LL << {SHIFT2 - 1})\n\n"
    )

    # --------------------------------------------------------
    # Conv3
    # --------------------------------------------------------

    f.write(
        f"#define CONV3_REQUANT_MULT "
        f"{MULT3}LL\n"
    )

    f.write(
        f"#define CONV3_REQUANT_SHIFT "
        f"{SHIFT3}\n"
    )

    f.write(
        f"#define CONV3_REQUANT_ROUND "
        f"(1LL << {SHIFT3 - 1})\n\n"
    )

    # --------------------------------------------------------
    # FC1
    # --------------------------------------------------------

    f.write(
        f"#define FC1_REQUANT_MULT "
        f"{MULT4}LL\n"
    )

    f.write(
        f"#define FC1_REQUANT_SHIFT "
        f"{SHIFT4}\n"
    )

    f.write(
        f"#define FC1_REQUANT_ROUND "
        f"(1LL << {SHIFT4 - 1})\n\n"
    )

    f.write(
        "#endif\n"
    )


# ============================================================
# 13. Quantization report
# ============================================================

report_lines = []


def report(line=""):

    report_lines.append(
        str(line)
    )


report(
    "AircraftCNN Full Integer Quantization Report"
)

report(
    "============================================"
)

report()

report(
    f"Model: {MODEL_PATH}"
)

report(
    f"Device: {device}"
)

report()


report(
    "[Activation calibration]"
)

for name in [
    "pool1",
    "pool2",
    "pool3",
    "fc1_relu",
]:

    report(
        f"{name}: "
        f"min={stats[name]['min']}, "
        f"max={stats[name]['max']}, "
        f"values={stats[name]['count']}"
    )


report()

report(
    "[Activation scales]"
)

report(
    f"Sx1 = {SX1:.17g}"
)

report(
    f"Sx2 = {Sx2:.17g}"
)

report(
    f"Sx3 = {Sx3:.17g}"
)

report(
    f"Sx4 = {Sx4:.17g}"
)

report(
    f"Sx5 = {Sx5:.17g}"
)


report()

report(
    "[Weight scales]"
)

report(
    f"Sw1 Conv1 = {Sw1:.17g}"
)

report(
    f"Sw2 Conv2 = {Sw2:.17g}"
)

report(
    f"Sw3 Conv3 = {Sw3:.17g}"
)

report(
    f"Sw4 FC1   = {Sw4:.17g}"
)

report(
    f"Sw5 FC2   = {Sw5:.17g}"
)


report()

report(
    "[Q24 requantization]"
)

report(
    f"Conv1 -> Conv2: "
    f"M={M1:.17g}, "
    f"MULT={MULT1}, "
    f"SHIFT={SHIFT1}"
)

report(
    f"Conv2 -> Conv3: "
    f"M={M2:.17g}, "
    f"MULT={MULT2}, "
    f"SHIFT={SHIFT2}"
)

report(
    f"Conv3 -> FC1: "
    f"M={M3:.17g}, "
    f"MULT={MULT3}, "
    f"SHIFT={SHIFT3}"
)

report(
    f"FC1 -> FC2: "
    f"M={M4:.17g}, "
    f"MULT={MULT4}, "
    f"SHIFT={SHIFT4}"
)


report()

report(
    "[Integer tensor shapes]"
)

report(
    f"Conv1 weights: "
    f"{conv1_weight_q.shape}"
)

report(
    f"Conv2 weights: "
    f"{conv2_weight_q.shape}"
)

report(
    f"Conv3 weights: "
    f"{conv3_weight_q.shape}"
)

report(
    f"FC1 weights: "
    f"{fc1_weight_q.shape}"
)

report(
    f"FC2 weights: "
    f"{fc2_weight_q.shape}"
)


report()

report(
    "[Bias ranges]"
)

report(
    f"Conv1: "
    f"{conv1_bias_q.min()} .. "
    f"{conv1_bias_q.max()}"
)

report(
    f"Conv2: "
    f"{conv2_bias_q.min()} .. "
    f"{conv2_bias_q.max()}"
)

report(
    f"Conv3: "
    f"{conv3_bias_q.min()} .. "
    f"{conv3_bias_q.max()}"
)

report(
    f"FC1: "
    f"{fc1_bias_q.min()} .. "
    f"{fc1_bias_q.max()}"
)

report(
    f"FC2: "
    f"{fc2_bias_q.min()} .. "
    f"{fc2_bias_q.max()}"
)


with open(
    OUTPUT_REPORT,
    "w"
) as f:

    f.write(
        "\n".join(
            report_lines
        )
    )

    f.write(
        "\n"
    )


# ============================================================
# 14. Final summary
# ============================================================

print()
print(
    "============================================================"
)
print(
    " QUANTIZATION PREPARATION COMPLETE"
)
print(
    "============================================================"
)

print()

print(
    "Activation scales:"
)

print(
    f"Sx1 = {SX1:.17g}"
)

print(
    f"Sx2 = {Sx2:.17g}"
)

print(
    f"Sx3 = {Sx3:.17g}"
)

print(
    f"Sx4 = {Sx4:.17g}"
)

print(
    f"Sx5 = {Sx5:.17g}"
)


print()

print(
    "Weight scales:"
)

print(
    f"Sw1 = {Sw1:.17g}"
)

print(
    f"Sw2 = {Sw2:.17g}"
)

print(
    f"Sw3 = {Sw3:.17g}"
)

print(
    f"Sw4 = {Sw4:.17g}"
)

print(
    f"Sw5 = {Sw5:.17g}"
)


print()

print(
    "Q24 requant:"
)

print(
    f"Conv1 -> Conv2 : "
    f"MULT={MULT1}, SHIFT={SHIFT1}"
)

print(
    f"Conv2 -> Conv3 : "
    f"MULT={MULT2}, SHIFT={SHIFT2}"
)

print(
    f"Conv3 -> FC1   : "
    f"MULT={MULT3}, SHIFT={SHIFT3}"
)

print(
    f"FC1 -> FC2     : "
    f"MULT={MULT4}, SHIFT={SHIFT4}"
)


print()

print(
    "Generated files:"
)

print(
    " ",
    OUTPUT_NPZ
)

print(
    " ",
    PARAM_HEADER
)

print(
    " ",
    OUTPUT_REPORT
)

print()
print(
    "============================================================"
)
