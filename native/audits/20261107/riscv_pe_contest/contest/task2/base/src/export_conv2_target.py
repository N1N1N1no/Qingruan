import os
import torch
import numpy as np


# ============================================================
# Paths
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

MODEL_PATH = os.path.join(
    PROJECT_ROOT,
    "models",
    "best_model.pth"
)

OUTPUT_PATH = os.path.join(
    PROJECT_ROOT,
    "exports",
    "conv2_target_data.h"
)


# ============================================================
# Conv2 configuration
# ============================================================

CONV2_IN_C = 32
CONV2_OUT_C = 64

KERNEL_H = 3
KERNEL_W = 3

K_TOTAL = (
    CONV2_IN_C *
    KERNEL_H *
    KERNEL_W
)

PE_ROWS = 16
PE_COLS = 16

NUM_K_TILES = K_TOTAL // PE_ROWS
NUM_OC_TILES = CONV2_OUT_C // PE_COLS


# ============================================================
# Conv2 input scale
#
# This is the calibrated Pool1 output scale.
# ============================================================

SX2 = 0.013367715783006563


# ============================================================
# Load model
# ============================================================

print()
print("===============================================")
print(" Export Conv2 target data")
print("===============================================")

print("Model:")
print(MODEL_PATH)

print()

state_dict = torch.load(
    MODEL_PATH,
    map_location="cpu"
)


# ============================================================
# Read Conv2 parameters
# ============================================================

weight = state_dict["conv2.weight"].detach().cpu().float()

bias = state_dict["conv2.bias"].detach().cpu().float()


print("conv2.weight shape :", tuple(weight.shape))
print("conv2.bias shape   :", tuple(bias.shape))


expected_weight_shape = (
    CONV2_OUT_C,
    CONV2_IN_C,
    KERNEL_H,
    KERNEL_W
)

expected_bias_shape = (
    CONV2_OUT_C,
)


if tuple(weight.shape) != expected_weight_shape:
    raise RuntimeError(
        f"Unexpected conv2.weight shape: "
        f"{tuple(weight.shape)}, "
        f"expected {expected_weight_shape}"
    )


if tuple(bias.shape) != expected_bias_shape:
    raise RuntimeError(
        f"Unexpected conv2.bias shape: "
        f"{tuple(bias.shape)}, "
        f"expected {expected_bias_shape}"
    )


# ============================================================
# Weight scale
#
# Symmetric per-tensor INT8 quantization:
#
#   Sw2 = max(abs(W)) / 127
# ============================================================

weight_abs_max = weight.abs().max().item()

SW2 = weight_abs_max / 127.0


print()
print("-----------------------------------------------")
print("Quantization parameters")
print("-----------------------------------------------")

print("Sx2               :", SX2)
print("Weight abs max    :", weight_abs_max)
print("Sw2               :", SW2)
print("Accumulator scale :", SX2 * SW2)


# ============================================================
# Quantize weights
#
#   Wq = round(W / Sw2)
#
# range:
#
#   -127 ... +127
# ============================================================

weight_q = torch.round(
    weight / SW2
)

weight_q = torch.clamp(
    weight_q,
    -127,
    127
).to(torch.int32)


print()
print("Weight q min      :", int(weight_q.min()))
print("Weight q max      :", int(weight_q.max()))


# ============================================================
# Quantize bias
#
# Conv accumulator represents:
#
#   real_value =
#
#       accumulator * (Sx2 * Sw2)
#
# therefore:
#
#   Bq =
#
#       round(
#           bias /
#           (Sx2 * Sw2)
#       )
# ============================================================

bias_scale = SX2 * SW2


bias_q = torch.round(
    bias / bias_scale
).to(torch.int64)


int32_min = -(2 ** 31)
int32_max = (2 ** 31) - 1


if torch.any(bias_q < int32_min):
    raise RuntimeError(
        "Conv2 bias quantization underflows int32"
    )


if torch.any(bias_q > int32_max):
    raise RuntimeError(
        "Conv2 bias quantization overflows int32"
    )


bias_q = bias_q.to(torch.int32)


print("Bias q min        :", int(bias_q.min()))
print("Bias q max        :", int(bias_q.max()))


# ============================================================
# Flatten Conv2 K dimension
# ============================================================
#
# PyTorch weight:
#
#   [OC][IC][KH][KW]
#
#
# We need:
#
#   [OC][K]
#
#
# K order:
#
#   IC0 KH0 KW0
#   IC0 KH0 KW1
#   IC0 KH0 KW2
#   IC0 KH1 KW0
#   ...
#   IC31 KH2 KW2
#
#
# This matches:
#
#   input[:, h:h+3, w:w+3].reshape(-1)
#
# used by the Conv1 implementation.
# ============================================================

weight_flat = weight_q.reshape(
    CONV2_OUT_C,
    K_TOTAL
)


assert weight_flat.shape == (
    CONV2_OUT_C,
    K_TOTAL
)


# ============================================================
# Convert signed weight to hardware sign-magnitude byte
# ============================================================
#
# Existing PE weight encoding:
#
# positive:
#
#   +5 -> 0x05
#
# negative:
#
#   -5 -> 0x85
#
# i.e.
#
#   bit7 = sign
#   bit6:0 = magnitude
# ============================================================

def signed_to_hw(value: int) -> int:

    if value < -127 or value > 127:
        raise ValueError(
            f"Weight outside valid range: {value}"
        )

    if value < 0:
        return 0x80 | (-value)

    return value


# ============================================================
# Hardware layout
# ============================================================
#
# conv2_weight_hw
#
#   [OC_TILE]
#   [K_TILE]
#   [ROW]
#   [COLUMN]
#
#
# Dimensions:
#
#   [4][18][16][16]
#
#
# Meaning:
#
# OC_TILE:
#
#   0 -> OC0..15
#   1 -> OC16..31
#   2 -> OC32..47
#   3 -> OC48..63
#
#
# K_TILE:
#
#   0  -> K0..15
#   1  -> K16..31
#   ...
#   17 -> K272..287
#
#
# ROW:
#
#   one K value
#
#
# COLUMN:
#
#   one output channel
# ============================================================

weight_hw = np.zeros(
    (
        NUM_OC_TILES,
        NUM_K_TILES,
        PE_ROWS,
        PE_COLS
    ),
    dtype=np.uint8
)


for oc_tile in range(NUM_OC_TILES):

    for k_tile in range(NUM_K_TILES):

        for row in range(PE_ROWS):

            k = (
                k_tile *
                PE_ROWS +
                row
            )

            for col in range(PE_COLS):

                oc = (
                    oc_tile *
                    PE_COLS +
                    col
                )

                q = int(
                    weight_flat[
                        oc,
                        k
                    ].item()
                )

                weight_hw[
                    oc_tile,
                    k_tile,
                    row,
                    col
                ] = signed_to_hw(q)


# ============================================================
# Internal sanity checks
# ============================================================

print()
print("-----------------------------------------------")
print("Hardware mapping")
print("-----------------------------------------------")

print("K total           :", K_TOTAL)
print("K tiles           :", NUM_K_TILES)
print("OC tiles          :", NUM_OC_TILES)

print(
    "HW weight shape   :",
    weight_hw.shape
)


if K_TOTAL % PE_ROWS != 0:
    raise RuntimeError(
        "Conv2 K dimension is not divisible by PE_ROWS"
    )


if CONV2_OUT_C % PE_COLS != 0:
    raise RuntimeError(
        "Conv2 output channels are not divisible by PE_COLS"
    )


# ============================================================
# Verify hardware encoding by decoding it again
# ============================================================

def hw_to_signed(value: int) -> int:

    if value & 0x80:
        return -(value & 0x7F)

    return value & 0x7F


mapping_errors = 0


for oc_tile in range(NUM_OC_TILES):

    for k_tile in range(NUM_K_TILES):

        for row in range(PE_ROWS):

            k = (
                k_tile *
                PE_ROWS +
                row
            )

            for col in range(PE_COLS):

                oc = (
                    oc_tile *
                    PE_COLS +
                    col
                )

                expected = int(
                    weight_flat[
                        oc,
                        k
                    ].item()
                )

                encoded = int(
                    weight_hw[
                        oc_tile,
                        k_tile,
                        row,
                        col
                    ]
                )

                decoded = hw_to_signed(
                    encoded
                )

                if decoded != expected:

                    mapping_errors += 1

                    if mapping_errors <= 10:

                        print(
                            "Mapping mismatch:",
                            "oc=", oc,
                            "k=", k,
                            "expected=", expected,
                            "decoded=", decoded
                        )


if mapping_errors != 0:
    raise RuntimeError(
        f"Hardware weight mapping failed: "
        f"{mapping_errors} errors"
    )


print(
    "HW mapping check  : PASS"
)


# ============================================================
# Write C header
# ============================================================

os.makedirs(
    os.path.dirname(OUTPUT_PATH),
    exist_ok=True
)


with open(
    OUTPUT_PATH,
    "w",
    encoding="utf-8"
) as f:

    f.write(
        "#ifndef CONV2_TARGET_DATA_H\n"
    )

    f.write(
        "#define CONV2_TARGET_DATA_H\n\n"
    )


    f.write(
        "#include <stdint.h>\n\n"
    )


    # --------------------------------------------------------
    # Dimensions
    # --------------------------------------------------------

    f.write(
        "/* Conv2 dimensions */\n"
    )

    f.write(
        f"#define CONV2_IN_C {CONV2_IN_C}\n"
    )

    f.write(
        f"#define CONV2_OUT_C {CONV2_OUT_C}\n"
    )

    f.write(
        f"#define CONV2_KERNEL_H {KERNEL_H}\n"
    )

    f.write(
        f"#define CONV2_KERNEL_W {KERNEL_W}\n"
    )

    f.write(
        f"#define CONV2_K_TOTAL {K_TOTAL}\n"
    )

    f.write(
        f"#define CONV2_NUM_K_TILE {NUM_K_TILES}\n"
    )

    f.write(
        f"#define CONV2_NUM_OC_TILE {NUM_OC_TILES}\n\n"
    )


    # --------------------------------------------------------
    # Quantization metadata
    #
    # These are comments only.
    # RISC-V target code does not need floating-point constants
    # merely to execute the integer Conv2.
    # --------------------------------------------------------

    f.write(
        "/*\n"
    )

    f.write(
        " * Quantization metadata\n"
    )

    f.write(
        f" * Sx2 = {SX2:.17g}\n"
    )

    f.write(
        f" * Sw2 = {SW2:.17g}\n"
    )

    f.write(
        f" * Sx2*Sw2 = {bias_scale:.17g}\n"
    )

    f.write(
        " */\n\n"
    )


    # --------------------------------------------------------
    # Conv2 hardware weights
    # --------------------------------------------------------

    f.write(
        "/*\n"
    )

    f.write(
        " * Conv2 PE weight layout:\n"
    )

    f.write(
        " *\n"
    )

    f.write(
        " * [oc_tile][k_tile][row][column]\n"
    )

    f.write(
        " *\n"
    )

    f.write(
        " * shape = [4][18][16][16]\n"
    )

    f.write(
        " */\n"
    )


    f.write(
        "static const uint8_t "
        "conv2_weight_hw"
        "[CONV2_NUM_OC_TILE]"
        "[CONV2_NUM_K_TILE]"
        "[16]"
        "[16] = {\n"
    )


    for oc_tile in range(NUM_OC_TILES):

        f.write(
            f"    /* OC tile {oc_tile}: "
            f"OC{oc_tile * 16}.."
            f"OC{oc_tile * 16 + 15} */\n"
        )

        f.write(
            "    {\n"
        )


        for k_tile in range(NUM_K_TILES):

            k_start = (
                k_tile *
                PE_ROWS
            )

            k_end = (
                k_start +
                PE_ROWS -
                1
            )


            f.write(
                f"        /* K tile {k_tile}: "
                f"K{k_start}..K{k_end} */\n"
            )

            f.write(
                "        {\n"
            )


            for row in range(PE_ROWS):

                values = []

                for col in range(PE_COLS):

                    value = int(
                        weight_hw[
                            oc_tile,
                            k_tile,
                            row,
                            col
                        ]
                    )

                    values.append(
                        f"0x{value:02X}"
                    )


                comma = (
                    ","
                    if row <
                    PE_ROWS - 1
                    else ""
                )


                f.write(
                    "            { "
                    +
                    ", ".join(values)
                    +
                    " }"
                    +
                    comma
                    +
                    "\n"
                )


            comma = (
                ","
                if k_tile <
                NUM_K_TILES - 1
                else ""
            )


            f.write(
                "        }"
                +
                comma
                +
                "\n"
            )


        comma = (
            ","
            if oc_tile <
            NUM_OC_TILES - 1
            else ""
        )


        f.write(
            "    }"
            +
            comma
            +
            "\n"
        )


    f.write(
        "};\n\n"
    )


    # --------------------------------------------------------
    # Conv2 bias
    # --------------------------------------------------------

    f.write(
        "/*\n"
    )

    f.write(
        " * Conv2 quantized bias\n"
    )

    f.write(
        " *\n"
    )

    f.write(
        " * Bq = round(B / (Sx2 * Sw2))\n"
    )

    f.write(
        " */\n"
    )


    f.write(
        "static const int32_t "
        "conv2_bias_q"
        "[CONV2_OUT_C] = {\n"
    )


    bias_values = [
        int(x)
        for x in bias_q.tolist()
    ]


    for start in range(
        0,
        CONV2_OUT_C,
        8
    ):

        chunk = bias_values[
            start:
            start + 8
        ]


        comma = (
            ","
            if start + 8 <
            CONV2_OUT_C
            else ""
        )


        f.write(
            "    "
            +
            ", ".join(
                str(x)
                for x in chunk
            )
            +
            comma
            +
            "\n"
        )


    f.write(
        "};\n\n"
    )


    f.write(
        "#endif /* CONV2_TARGET_DATA_H */\n"
    )


# ============================================================
# Final report
# ============================================================

file_size = os.path.getsize(
    OUTPUT_PATH
)


print()
print("===============================================")
print(" Conv2 export complete")
print("===============================================")

print("Output:")
print(OUTPUT_PATH)

print()
print("Header size       :", file_size, "bytes")

print()
print("Generated arrays:")
print(
    "  conv2_weight_hw"
    f"[{NUM_OC_TILES}]"
    f"[{NUM_K_TILES}]"
    f"[{PE_ROWS}]"
    f"[{PE_COLS}]"
)

print(
    "  conv2_bias_q"
    f"[{CONV2_OUT_C}]"
)

print()
print(
    "Conv2 PE mapping:"
)

print(
    "  OC tiles :",
    NUM_OC_TILES
)

print(
    "  K passes :",
    NUM_K_TILES,
    "per output tile"
)

print()
print(
    "Expected Conv2:"
)

print(
    "  Input  : 32 x 64 x 64"
)

print(
    "  Output : 64 x 62 x 62"
)

print("===============================================")

