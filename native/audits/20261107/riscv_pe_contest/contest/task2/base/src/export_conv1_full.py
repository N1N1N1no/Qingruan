import os
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms

from model import AircraftCNN


# ============================================================
# 1. Basic configuration
#
# Conv1:
#
#   Input:
#       [3, 130, 130]
#
#   Weight:
#       [32, 3, 3, 3]
#
#   Output:
#       [32, 128, 128]
#
# Hardware mapping:
#
#   16 PE columns
#       -> 16 output channels
#
#   4 lanes
#       -> 4 spatial positions
#
# Therefore:
#
#   OC tiles:
#       32 / 16 = 2
#
#   Spatial positions:
#       128 * 128 = 16384
#
#   Spatial tiles:
#       16384 / 4 = 4096
#
#   Total PE tiles:
#       2 * 4096 = 8192
#
#   Total Conv1 outputs:
#       32 * 128 * 128 = 524288
#
# K:
#       3 * 3 * 3 = 27
# ============================================================

IN_C = 3
IN_H = 130
IN_W = 130

OUT_C = 32
OUT_H = 128
OUT_W = 128

KERNEL_H = 3
KERNEL_W = 3

K = IN_C * KERNEL_H * KERNEL_W

NUM_COL = 16
NUM_LANE = 4

NUM_OC_TILE = OUT_C // NUM_COL
NUM_SPATIAL = OUT_H * OUT_W
NUM_SPATIAL_TILE = NUM_SPATIAL // NUM_LANE

OUT_DIR = "exports/conv1_full"
os.makedirs(OUT_DIR, exist_ok=True)

HEADER_PATH = os.path.join(
    OUT_DIR,
    "conv1_data.h"
)

META_PATH = os.path.join(
    OUT_DIR,
    "meta.txt"
)


# ============================================================
# 2. Sanity checks
# ============================================================

assert OUT_C % NUM_COL == 0
assert NUM_SPATIAL % NUM_LANE == 0

assert NUM_OC_TILE == 2
assert NUM_SPATIAL_TILE == 4096
assert K == 27


# ============================================================
# 3. Load trained CNN
# ============================================================

model = AircraftCNN()

state_dict = torch.load(
    "models/best_model.pth",
    map_location="cpu"
)

model.load_state_dict(state_dict)
model.eval()


# ============================================================
# 4. Load exactly the same real image as previous exporters
# ============================================================

image_dir = "dataset/B-1"

image_files = sorted([
    name
    for name in os.listdir(image_dir)
    if name.lower().endswith(
        (".jpg", ".jpeg", ".png", ".bmp")
    )
])

if not image_files:
    raise RuntimeError(
        "dataset/B-1 中没有找到图片"
    )

image_path = os.path.join(
    image_dir,
    image_files[0]
)

transform = transforms.Compose([
    transforms.Resize((IN_H, IN_W)),
    transforms.ToTensor()
])

image = Image.open(
    image_path
).convert("RGB")

x = transform(
    image
).unsqueeze(0)

assert tuple(x.shape) == (
    1,
    IN_C,
    IN_H,
    IN_W
)


# ============================================================
# 5. Quantization scales
#
# Keep exactly the same scheme as the verified exporter:
#
#   Sx   = 1 / 127
#
#   Sw   = max(abs(all Conv1 weights)) / 127
#
#   Sacc = Sx * Sw
# ============================================================

Sx = 1.0 / 127.0

all_weights = model.conv1.weight.detach()

Sw = (
    all_weights.abs().max().item()
    / 127.0
)

Sacc = Sx * Sw


# ============================================================
# 6. Hardware sign-magnitude encoding
#
# Exact_8bit hardware format:
#
#   bit7    = sign
#   bits6:0 = magnitude
#
# Examples:
#
#    +3 -> 0x03
#    -3 -> 0x83
#
# NOTE:
#
# Integer Golden calculation uses signed weight_q.
#
# Only the bytes sent to hardware use this encoding.
# ============================================================

def encode_sign_magnitude(value):

    value = int(value)

    if not -127 <= value <= 127:
        raise ValueError(
            f"{value} 超出硬件8-bit范围"
        )

    if value >= 0:
        return value

    return 0x80 | abs(value)


# ============================================================
# 7. Quantize complete Conv1 input feature map
#
# x:
#       [1, 3, 130, 130]
#
# input_q:
#       [3, 130, 130]
#
# Input is non-negative because ToTensor() gives [0, 1].
#
# Hardware byte is therefore numerically identical to input_q,
# but we still use the same encoder for consistency.
# ============================================================

input_fp32 = x[0].detach()

input_q = torch.round(
    input_fp32 / Sx
).clamp(
    0,
    127
).to(torch.int64)

assert tuple(input_q.shape) == (
    IN_C,
    IN_H,
    IN_W
)

input_hw = torch.zeros_like(
    input_q,
    dtype=torch.int64
)

for c in range(IN_C):
    for h in range(IN_H):
        for w in range(IN_W):

            input_hw[c, h, w] = (
                encode_sign_magnitude(
                    input_q[c, h, w].item()
                )
            )


# ============================================================
# 8. Quantize ALL Conv1 weights
#
# weight_fp32:
#       [32, 3, 3, 3]
#
# weight_q:
#       signed integer representation
#
# weight_flat_q:
#       [32, 27]
#
# weight_hw:
#       [32, 27]
#
# Later:
#
#   OC Tile 0 -> OC0..15
#   OC Tile 1 -> OC16..31
# ============================================================

weight_fp32 = (
    model.conv1.weight
    .detach()
)

weight_q = torch.round(
    weight_fp32 / Sw
).clamp(
    -127,
    127
).to(torch.int64)

assert tuple(weight_q.shape) == (
    OUT_C,
    IN_C,
    KERNEL_H,
    KERNEL_W
)

weight_flat_q = weight_q.reshape(
    OUT_C,
    K
)

weight_hw = torch.zeros(
    OUT_C,
    K,
    dtype=torch.int64
)

for oc in range(OUT_C):

    for k in range(K):

        weight_hw[oc, k] = (
            encode_sign_magnitude(
                weight_flat_q[oc, k].item()
            )
        )


# ============================================================
# 9. Quantize ALL Conv1 bias
#
# bias_q:
#       [32]
#
# Integer accumulator starts from:
#
#       Bq = round(B / Sacc)
# ============================================================

bias_fp32 = (
    model.conv1.bias
    .detach()
)

bias_q = torch.round(
    bias_fp32 / Sacc
).to(torch.int64)

assert tuple(bias_q.shape) == (
    OUT_C,
)


# ============================================================
# 10. Build complete Integer Golden
#
# We intentionally use PyTorch unfold here.
#
# IMPORTANT:
#
# unfold generates the K dimension in the same logical order:
#
#   channel
#       -> kernel row
#           -> kernel column
#
# This matches:
#
#   patch.reshape(-1)
#
# used by our already verified two-tile exporter.
#
#
# patches:
#
#   [1, 27, 16384]
#
# after transpose:
#
#   [16384, 27]
#
#
# Golden:
#
#   [32, 16384]
#
# reshape:
#
#   [32, 128, 128]
#
#
# Formula:
#
#   acc =
#       bias_q
#       +
#       sum(
#           input_q * weight_q
#       )
# ============================================================

patches = F.unfold(
    input_q.unsqueeze(0).to(torch.float64),
    kernel_size=(KERNEL_H, KERNEL_W),
    stride=1,
    padding=0
)

# [1, 27, 16384]
assert tuple(patches.shape) == (
    1,
    K,
    NUM_SPATIAL
)

patches = (
    patches[0]
    .transpose(0, 1)
    .contiguous()
    .to(torch.int64)
)

# [16384, 27]
assert tuple(patches.shape) == (
    NUM_SPATIAL,
    K
)


# Matrix multiplication:
#
#   [32,27] @ [27,16384]
#       ->
#   [32,16384]

expected_flat = (
    weight_flat_q
    @
    patches.transpose(0, 1)
)

expected_flat = (
    expected_flat
    +
    bias_q[:, None]
)

assert tuple(expected_flat.shape) == (
    OUT_C,
    NUM_SPATIAL
)

expected = expected_flat.reshape(
    OUT_C,
    OUT_H,
    OUT_W
)

assert tuple(expected.shape) == (
    OUT_C,
    OUT_H,
    OUT_W
)


# ============================================================
# 11. Independent consistency check against explicit MAC
#
# This deliberately reproduces the old build_tile() logic.
#
# We check the already RTL-verified region:
#
#   OC0..15
#   P0..P7
#
# Especially:
#
#   Tile0 COL0 Lane0 = -45373
#   Tile1 COL0 Lane0 = -52481
#
# If these fail, STOP.
# ============================================================

def explicit_mac(oc, p):

    h = p // OUT_W
    w = p % OUT_W

    patch = input_q[
        :,
        h:h + KERNEL_H,
        w:w + KERNEL_W
    ].reshape(-1)

    assert patch.numel() == K

    acc = int(
        bias_q[oc].item()
    )

    for k in range(K):

        acc += (
            int(
                weight_flat_q[oc, k].item()
            )
            *
            int(
                patch[k].item()
            )
        )

    return acc


print(
    "Checking already RTL-verified "
    "OC0..15 x P0..P7 ..."
)

for oc in range(16):

    for p in range(8):

        h = p // OUT_W
        w = p % OUT_W

        explicit_value = explicit_mac(
            oc,
            p
        )

        full_value = int(
            expected[
                oc,
                h,
                w
            ].item()
        )

        if explicit_value != full_value:

            raise RuntimeError(
                "Full Conv1 Golden consistency "
                f"check failed: OC={oc}, P={p}, "
                f"explicit={explicit_value}, "
                f"full={full_value}"
            )


# Known RTL-verified anchors

tile0_anchor = int(
    expected[0, 0, 0].item()
)

tile1_anchor = int(
    expected[0, 0, 4].item()
)

if tile0_anchor != -45373:

    raise RuntimeError(
        "Tile0 regression failed: "
        f"expected -45373, got {tile0_anchor}"
    )

if tile1_anchor != -52481:

    raise RuntimeError(
        "Tile1 regression failed: "
        f"expected -52481, got {tile1_anchor}"
    )


# ============================================================
# 12. Optional FP32 Conv1 comparison
#
# This does NOT determine RTL PASS/FAIL.
#
# RTL comparison uses Integer Golden exactly.
#
# This section only tells us how close the quantized Conv1 is
# to the original FP32 Conv1.
# ============================================================

with torch.no_grad():

    fp32_output = model.conv1(x)[0]

dequant_output = (
    expected.to(torch.float64)
    *
    Sacc
)

abs_error = (
    dequant_output
    -
    fp32_output.to(torch.float64)
).abs()

mae = abs_error.mean().item()
rmse = torch.sqrt(
    (
        (
            dequant_output
            -
            fp32_output.to(torch.float64)
        ) ** 2
    ).mean()
).item()

max_error = abs_error.max().item()


# ============================================================
# 13. Helper functions for C header generation
# ============================================================

def write_u8_hex_array_3d(
    f,
    name,
    tensor
):
    """
    tensor shape:
        [D0, D1, D2]

    Generate:
        static const uint8_t name[D0][D1][D2]
    """

    d0, d1, d2 = tensor.shape

    f.write(
        f"static const uint8_t "
        f"{name}[{d0}][{d1}][{d2}] = {{\n"
    )

    for i in range(d0):

        f.write("    {\n")

        for j in range(d1):

            values = []

            for k in range(d2):

                value = int(
                    tensor[i, j, k].item()
                )

                values.append(
                    f"0x{value:02X}"
                )

            f.write(
                "        { "
                +
                ", ".join(values)
                +
                " }"
            )

            if j != d1 - 1:
                f.write(",")

            f.write("\n")

        f.write("    }")

        if i != d0 - 1:
            f.write(",")

        f.write("\n")

    f.write("};\n\n")


def write_i32_array_1d(
    f,
    name,
    tensor
):

    d0 = tensor.shape[0]

    f.write(
        f"static const int32_t "
        f"{name}[{d0}] = {{\n"
    )

    for i in range(d0):

        value = int(
            tensor[i].item()
        )

        f.write(
            f"    {value}"
        )

        if i != d0 - 1:
            f.write(",")

        f.write("\n")

    f.write("};\n\n")


def write_i32_array_3d(
    f,
    name,
    tensor
):
    """
    tensor shape:
        [D0, D1, D2]

    Generate:
        static const int32_t name[D0][D1][D2]
    """

    d0, d1, d2 = tensor.shape

    f.write(
        f"static const int32_t "
        f"{name}[{d0}][{d1}][{d2}] = {{\n"
    )

    for i in range(d0):

        f.write("    {\n")

        for j in range(d1):

            values = []

            for k in range(d2):

                value = int(
                    tensor[i, j, k].item()
                )

                values.append(
                    str(value)
                )

            f.write(
                "        { "
                +
                ", ".join(values)
                +
                " }"
            )

            if j != d1 - 1:
                f.write(",")

            f.write("\n")

        f.write("    }")

        if i != d0 - 1:
            f.write(",")

        f.write("\n")

    f.write("};\n\n")


# ============================================================
# 14. Reorganize weight into hardware OC tiles
#
# Current weight_hw:
#
#   [32, 27]
#
# Header representation:
#
#   [2 OC tiles][27 K][16 columns]
#
# So:
#
#   conv1_weight_hw[0][k][col]
#       -> OC = col
#
#   conv1_weight_hw[1][k][col]
#       -> OC = 16 + col
# ============================================================

weight_tile_hw = torch.zeros(
    NUM_OC_TILE,
    K,
    NUM_COL,
    dtype=torch.int64
)

for oc_tile in range(
    NUM_OC_TILE
):

    for k in range(K):

        for col in range(
            NUM_COL
        ):

            oc = (
                oc_tile * NUM_COL
                +
                col
            )

            weight_tile_hw[
                oc_tile,
                k,
                col
            ] = weight_hw[
                oc,
                k
            ]


# ============================================================
# 15. Generate conv1_data.h
#
# IMPORTANT:
#
# We store the ORIGINAL quantized input feature map:
#
#   [3][130][130]
#
# NOT:
#
#   [4096][27][4]
#
# The RISC-V C scheduler will build each 4-lane PE tile from
# this feature map.
#
#
# expected:
#
#   [32][128][128]
#
# is included for VERIFICATION.
#
# Later deployment versions can remove this large Golden array.
# ============================================================

with open(
    HEADER_PATH,
    "w"
) as f:

    f.write(
        "/*\n"
        " * AUTO-GENERATED FILE\n"
        " *\n"
        " * Generated by:\n"
        " *   src/export_conv1_full.py\n"
        " *\n"
        " * DO NOT EDIT MANUALLY.\n"
        " */\n\n"
    )

    f.write(
        "#ifndef CONV1_DATA_H\n"
        "#define CONV1_DATA_H\n\n"
    )

    f.write(
        "#include <stdint.h>\n\n"
    )


    # --------------------------------------------------------
    # Dimensions
    # --------------------------------------------------------

    f.write(
        "#define CONV1_IN_C 3\n"
    )

    f.write(
        "#define CONV1_IN_H 130\n"
    )

    f.write(
        "#define CONV1_IN_W 130\n\n"
    )

    f.write(
        "#define CONV1_OUT_C 32\n"
    )

    f.write(
        "#define CONV1_OUT_H 128\n"
    )

    f.write(
        "#define CONV1_OUT_W 128\n\n"
    )

    f.write(
        "#define CONV1_K 27\n"
    )

    f.write(
        "#define CONV1_NUM_COL 16\n"
    )

    f.write(
        "#define CONV1_NUM_LANE 4\n"
    )

    f.write(
        "#define CONV1_NUM_OC_TILE 2\n"
    )

    f.write(
        "#define CONV1_NUM_SPATIAL "
        "16384\n"
    )

    f.write(
        "#define CONV1_NUM_SPATIAL_TILE "
        "4096\n"
    )

    f.write(
        "#define CONV1_TOTAL_OUTPUT "
        "524288\n\n"
    )


    # --------------------------------------------------------
    # Quantization metadata
    #
    # These are useful for debugging and later dequantization.
    # --------------------------------------------------------

    f.write(
        f"#define CONV1_SX "
        f"({Sx:.17g})\n"
    )

    f.write(
        f"#define CONV1_SW "
        f"({Sw:.17g})\n"
    )

    f.write(
        f"#define CONV1_SACC "
        f"({Sacc:.17g})\n\n"
    )


    # --------------------------------------------------------
    # Input
    #
    # [3][130][130]
    # --------------------------------------------------------

    f.write(
        "/*\n"
        " * Quantized Conv1 input feature map.\n"
        " * Layout: [channel][h][w]\n"
        " */\n"
    )

    write_u8_hex_array_3d(
        f,
        "conv1_input_q",
        input_hw
    )


    # --------------------------------------------------------
    # Weight
    #
    # [2][27][16]
    # --------------------------------------------------------

    f.write(
        "/*\n"
        " * Hardware-encoded Conv1 weights.\n"
        " * Layout: [oc_tile][k][column]\n"
        " *\n"
        " * oc_tile 0 -> OC0..15\n"
        " * oc_tile 1 -> OC16..31\n"
        " */\n"
    )

    write_u8_hex_array_3d(
        f,
        "conv1_weight_hw",
        weight_tile_hw
    )


    # --------------------------------------------------------
    # Bias
    #
    # [32]
    # --------------------------------------------------------

    f.write(
        "/*\n"
        " * Quantized Conv1 bias.\n"
        " * Layout: [output_channel]\n"
        " */\n"
    )

    write_i32_array_1d(
        f,
        "conv1_bias_q",
        bias_q
    )


    # --------------------------------------------------------
    # Integer Golden
    #
    # [32][128][128]
    # --------------------------------------------------------

    f.write(
        "/*\n"
        " * Full Conv1 Integer Golden.\n"
        " * Layout: [output_channel][h][w]\n"
        " *\n"
        " * Verification only.\n"
        " */\n"
    )

    write_i32_array_3d(
        f,
        "conv1_expected",
        expected
    )


    f.write(
        "#endif /* CONV1_DATA_H */\n"
    )


# ============================================================
# 16. Metadata
# ============================================================

with open(
    META_PATH,
    "w"
) as f:

    f.write(
        f"image={image_path}\n"
    )

    f.write(
        f"Sx={Sx}\n"
    )

    f.write(
        f"Sw={Sw}\n"
    )

    f.write(
        f"Sacc={Sacc}\n"
    )

    f.write(
        f"input_shape={tuple(input_q.shape)}\n"
    )

    f.write(
        f"weight_shape={tuple(weight_q.shape)}\n"
    )

    f.write(
        f"output_shape={tuple(expected.shape)}\n"
    )

    f.write(
        f"num_oc_tiles={NUM_OC_TILE}\n"
    )

    f.write(
        f"num_spatial_tiles={NUM_SPATIAL_TILE}\n"
    )

    f.write(
        "total_pe_tiles="
        f"{NUM_OC_TILE * NUM_SPATIAL_TILE}\n"
    )

    f.write(
        "total_outputs="
        f"{expected.numel()}\n"
    )

    f.write(
        f"quant_mae={mae}\n"
    )

    f.write(
        f"quant_rmse={rmse}\n"
    )

    f.write(
        f"quant_max_error={max_error}\n"
    )

    f.write(
        f"rtl_anchor_tile0_oc0_lane0={tile0_anchor}\n"
    )

    f.write(
        f"rtl_anchor_tile1_oc0_lane0={tile1_anchor}\n"
    )


# ============================================================
# 17. Console summary
# ============================================================

header_size = os.path.getsize(
    HEADER_PATH
)

print()
print(
    "==============================================="
)
print(
    " Conv1 FULL Export Complete"
)
print(
    "==============================================="
)

print()
print(
    "Image               :",
    image_path
)

print(
    "FP32 input shape    :",
    tuple(x.shape)
)

print(
    "INT8 input shape    :",
    tuple(input_q.shape)
)

print(
    "Weight shape        :",
    tuple(weight_q.shape)
)

print(
    "Integer Golden shape:",
    tuple(expected.shape)
)

print()
print(
    "K                   :",
    K
)

print(
    "OC tiles            :",
    NUM_OC_TILE
)

print(
    "Spatial positions   :",
    NUM_SPATIAL
)

print(
    "Spatial tiles       :",
    NUM_SPATIAL_TILE
)

print(
    "Total PE tiles      :",
    NUM_OC_TILE
    *
    NUM_SPATIAL_TILE
)

print(
    "Total outputs       :",
    expected.numel()
)

print()
print(
    "Sx                  :",
    Sx
)

print(
    "Sw                  :",
    Sw
)

print(
    "Sacc                :",
    Sacc
)

print()
print(
    "Quantization MAE    :",
    mae
)

print(
    "Quantization RMSE   :",
    rmse
)

print(
    "Quantization MAX ERR:",
    max_error
)

print()
print(
    "RTL regression:"
)

print(
    "  Tile0 OC0 Lane0   :",
    tile0_anchor,
    "(expected -45373)"
)

print(
    "  Tile1 OC0 Lane0   :",
    tile1_anchor,
    "(expected -52481)"
)

print()
print(
    "Generated:"
)

print(
    " ",
    HEADER_PATH
)

print(
    " ",
    META_PATH
)

print(
    "Header size         :",
    f"{header_size / 1024 / 1024:.2f} MiB"
)

print()
print(
    "FULL CONV1 EXPORT PASS"
)
print(
    "==============================================="
)
