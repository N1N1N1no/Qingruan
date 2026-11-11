import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F


# ============================================================
# Paths
# ============================================================

NPZ_PATH = "quant_output/aircraft_cnn_quantized.npz"

# src/dataset_loader.py
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


# ============================================================
# Quantization parameters
#
# 与已经通过 Python Integer Golden / VCS RTL 验证的参数保持一致
# ============================================================

REQUANT_SHIFT = 24
REQUANT_ROUND = 1 << 23

CONV1_REQUANT_MULT = 16860
CONV2_REQUANT_MULT = 21410
CONV3_REQUANT_MULT = 3779
FC1_REQUANT_MULT = 4518

NUM_CLASSES = 10

# 原 FP32 软件模型验证集精度
FP32_ACCURACY = 86.19


# ============================================================
# Requantization
# ============================================================

def requant_relu(x, multiplier):
    """
    与 RISC-V / VCS Integer Golden 保持一致：

        y = (x * multiplier + 2^23) >> 24
        ReLU
        clamp to [0, 127]

    输入:
        x : INT accumulator

    输出:
        uint-like INT8 activation，数值范围 0~127
        这里使用 torch.int64 保存
    """

    x = x.to(torch.int64)

    y = x * multiplier
    y = y + REQUANT_ROUND
    y = torch.bitwise_right_shift(y, REQUANT_SHIFT)

    # ReLU + saturation
    y = torch.clamp(y, 0, 127)

    return y.to(torch.int64)


# ============================================================
# MaxPool
# ============================================================

def maxpool2x2(x):
    """
    Integer MaxPool:

        kernel = 2x2
        stride = 2

    输入:
        [N, C, H, W]

    输出:
        [N, C, floor(H/2), floor(W/2)]

    当 H/W 为奇数时，最后一行/列自然丢弃。

    例如:
        29 x 29
          ↓
        14 x 14
    """

    h = x.shape[2]
    width = x.shape[3]

    out_h = h // 2
    out_w = width // 2

    # 丢弃无法组成完整 2x2 window 的最后一行/列
    x = x[:, :, :out_h * 2, :out_w * 2]

    x00 = x[:, :, 0::2, 0::2]
    x01 = x[:, :, 0::2, 1::2]
    x10 = x[:, :, 1::2, 0::2]
    x11 = x[:, :, 1::2, 1::2]

    y = torch.maximum(x00, x01)
    y = torch.maximum(y, x10)
    y = torch.maximum(y, x11)

    return y


# ============================================================
# Integer convolution
# ============================================================

def int_conv2d_valid(x, weight, bias):
    """
    Exact integer Conv2D.

    CNN 中三个卷积层均为：

        kernel  = 3x3
        stride  = 1
        padding = 0

    输入:
        x:
            [N, Cin, H, W]

        weight:
            [Cout, Cin, 3, 3]

        bias:
            [Cout]

    输出:
        INT64 accumulator
        [N, Cout, H-2, W-2]

    数学上执行：

        output =
            sum(input * weight)
            + bias

    与 PE Array 的最终 MAC 数学结果一致。
    """

    x = x.to(torch.int64)
    weight = weight.to(torch.int64)
    bias = bias.to(torch.int64)

    n, cin, h, width = x.shape

    cout, weight_cin, kh, kw = weight.shape

    assert cin == weight_cin, (
        f"Input channel mismatch: "
        f"x Cin={cin}, weight Cin={weight_cin}"
    )

    assert kh == 3 and kw == 3, (
        f"Expected 3x3 kernel, got {kh}x{kw}"
    )

    # --------------------------------------------------------
    # unfold 提取所有 3x3 convolution windows
    #
    # PyTorch 某些版本不支持 Long tensor 的 unfold，
    # 因此临时转 float64。
    #
    # 当前 activation 最大仅 127，
    # float64 可以精确表示这些整数。
    # unfold 后立即转回 int64。
    # --------------------------------------------------------

    patches = F.unfold(
        x.to(torch.float64),
        kernel_size=(3, 3),
        dilation=1,
        padding=0,
        stride=1
    )

    patches = patches.to(torch.int64)

    # patches:
    #
    # [N, Cin*3*3, Hout*Wout]
    #
    # 即:
    #
    # [N, K, L]

    weight_flat = weight.reshape(cout, -1)

    k = weight_flat.shape[1]

    assert patches.shape[1] == k

    num_positions = patches.shape[2]

    # --------------------------------------------------------
    # 为防止一次产生过大的临时 tensor，
    # 空间位置和 output channel 都进行分块。
    # --------------------------------------------------------

    SPATIAL_CHUNK = 256
    OC_CHUNK = 16

    spatial_outputs = []

    for spatial_start in range(
        0,
        num_positions,
        SPATIAL_CHUNK
    ):

        spatial_end = min(
            spatial_start + SPATIAL_CHUNK,
            num_positions
        )

        # [N, K, S]
        patch_chunk = patches[
            :,
            :,
            spatial_start:spatial_end
        ]

        oc_outputs = []

        for oc_start in range(
            0,
            cout,
            OC_CHUNK
        ):

            oc_end = min(
                oc_start + OC_CHUNK,
                cout
            )

            # [OC, K]
            weight_chunk = weight_flat[
                oc_start:oc_end
            ]

            # ------------------------------------------------
            # patch_chunk:
            #     [N, K, S]
            #
            # weight_chunk:
            #     [OC, K]
            #
            # 广播以后：
            #
            #     [N, 1, K, S]
            #       ×
            #     [1, OC, K, 1]
            #
            # sum K
            #
            #     ↓
            #
            #     [N, OC, S]
            # ------------------------------------------------

            result = (
                patch_chunk.unsqueeze(1)
                *
                weight_chunk.unsqueeze(0).unsqueeze(-1)
            ).sum(dim=2)

            # Bias
            result = result + bias[
                oc_start:oc_end
            ].view(1, -1, 1)

            oc_outputs.append(result)

        # 拼回全部 output channels
        chunk_output = torch.cat(
            oc_outputs,
            dim=1
        )

        spatial_outputs.append(chunk_output)

    # 拼回全部空间位置
    output = torch.cat(
        spatial_outputs,
        dim=2
    )

    out_h = h - 3 + 1
    out_w = width - 3 + 1

    output = output.reshape(
        n,
        cout,
        out_h,
        out_w
    )

    return output


# ============================================================
# Integer Fully Connected
# ============================================================

def int_linear(x, weight, bias):
    """
    Exact integer fully-connected layer.

    输入:
        x:
            [N, K]

        weight:
            [OUT, K]

        bias:
            [OUT]

    输出:
        [N, OUT]

    数学：

        Y = X * W^T + B
    """

    x = x.to(torch.int64)
    weight = weight.to(torch.int64)
    bias = bias.to(torch.int64)

    out_features = weight.shape[0]

    assert x.shape[1] == weight.shape[1], (
        f"FC K mismatch: "
        f"x={x.shape}, weight={weight.shape}"
    )

    OC_CHUNK = 16

    outputs = []

    for start in range(
        0,
        out_features,
        OC_CHUNK
    ):

        end = min(
            start + OC_CHUNK,
            out_features
        )

        weight_chunk = weight[start:end]

        # x:
        #     [N, K]
        #
        # weight:
        #     [OUT, K]
        #
        # 广播:
        #
        # [N,1,K] * [1,OUT,K]
        #
        # sum K
        #
        # -> [N,OUT]

        y = (
            x.unsqueeze(1)
            *
            weight_chunk.unsqueeze(0)
        ).sum(dim=2)

        y = y + bias[
            start:end
        ].view(1, -1)

        outputs.append(y)

    return torch.cat(
        outputs,
        dim=1
    )


# ============================================================
# NPZ helper
# ============================================================

def find_key(npz, candidates):
    """
    在 NPZ 中寻找可能的参数名称。
    """

    for key in candidates:
        if key in npz:
            return key

    raise KeyError(
        "\nCannot find any of these NPZ keys:\n"
        + "\n".join(candidates)
        + "\n\nAvailable NPZ keys:\n"
        + "\n".join(npz.files)
    )


# ============================================================
# Load quantized CNN
# ============================================================

def load_quantized_model():

    if not os.path.exists(NPZ_PATH):

        raise FileNotFoundError(
            f"Cannot find:\n"
            f"  {NPZ_PATH}\n\n"
            f"Current directory:\n"
            f"  {os.getcwd()}"
        )

    q = np.load(NPZ_PATH)

    print("Loading quantized model:")
    print(f"  {NPZ_PATH}")
    print()

    def get_weight(layer):

        key = find_key(
            q,
            [
                f"{layer}_weight_q",
                f"{layer}.weight_q",
                f"{layer}_w_q",
            ]
        )

        return torch.from_numpy(
            q[key].astype(np.int64)
        )

    def get_bias(layer):

        key = find_key(
            q,
            [
                f"{layer}_bias_q",
                f"{layer}.bias_q",
                f"{layer}_b_q",
            ]
        )

        return torch.from_numpy(
            q[key].astype(np.int64)
        )

    params = {}

    for layer in [
        "conv1",
        "conv2",
        "conv3",
        "fc1",
        "fc2"
    ]:

        params[layer] = {
            "weight": get_weight(layer),
            "bias": get_bias(layer),
        }

        print(
            f"{layer:5s}: "
            f"W={tuple(params[layer]['weight'].shape)}  "
            f"B={tuple(params[layer]['bias'].shape)}"
        )

    print()

    return params


# ============================================================
# Full Integer CNN Forward
# ============================================================

@torch.no_grad()
def integer_forward(images, params):
    """
    AircraftCNN 完整整数推理。

    Pipeline:

        Input
          ↓
        INT8 Quantization
          ↓
        Conv1
          ↓
        ReLU + Q24 Requant
          ↓
        Pool1
          ↓
        Conv2
          ↓
        ReLU + Q24 Requant
          ↓
        Pool2
          ↓
        Conv3
          ↓
        ReLU + Q24 Requant
          ↓
        Pool3
          ↓
        Flatten
          ↓
        FC1
          ↓
        ReLU + Q24 Requant
          ↓
        FC2
          ↓
        logits
    """

    # ========================================================
    # Input Quantization
    #
    # ToTensor 后:
    #
    #     X ∈ [0,1]
    #
    # Integer Golden:
    #
    #     Xq = round(127 * X)
    # ========================================================

    x = torch.round(
        images.to(torch.float64) * 127.0
    )

    x = torch.clamp(
        x,
        0,
        127
    )

    x = x.to(torch.int64)

    # ========================================================
    # Conv1
    #
    # Input:
    #     3 x 130 x 130
    #
    # Output:
    #     32 x 128 x 128
    # ========================================================

    x = int_conv2d_valid(
        x,
        params["conv1"]["weight"],
        params["conv1"]["bias"]
    )

    x = requant_relu(
        x,
        CONV1_REQUANT_MULT
    )

    # ========================================================
    # Pool1
    #
    # 32 x 128 x 128
    #
    # ->
    #
    # 32 x 64 x 64
    # ========================================================

    x = maxpool2x2(x)

    # ========================================================
    # Conv2
    #
    # Input:
    #     32 x 64 x 64
    #
    # Output:
    #     64 x 62 x 62
    # ========================================================

    x = int_conv2d_valid(
        x,
        params["conv2"]["weight"],
        params["conv2"]["bias"]
    )

    x = requant_relu(
        x,
        CONV2_REQUANT_MULT
    )

    # ========================================================
    # Pool2
    #
    # 64 x 62 x 62
    #
    # ->
    #
    # 64 x 31 x 31
    # ========================================================

    x = maxpool2x2(x)

    # ========================================================
    # Conv3
    #
    # Input:
    #     64 x 31 x 31
    #
    # Output:
    #     32 x 29 x 29
    # ========================================================

    x = int_conv2d_valid(
        x,
        params["conv3"]["weight"],
        params["conv3"]["bias"]
    )

    x = requant_relu(
        x,
        CONV3_REQUANT_MULT
    )

    # ========================================================
    # Pool3
    #
    # 32 x 29 x 29
    #
    # ->
    #
    # 32 x 14 x 14
    # ========================================================

    x = maxpool2x2(x)

    # ========================================================
    # Flatten
    #
    # 32 * 14 * 14
    #
    # =
    #
    # 6272
    #
    # 顺序为 PyTorch contiguous:
    #
    # C -> H -> W
    # ========================================================

    x = x.reshape(
        x.shape[0],
        -1
    )

    assert x.shape[1] == 6272, (
        f"Flatten size error: {x.shape}"
    )

    # ========================================================
    # FC1
    #
    # 6272 -> 450
    # ========================================================

    x = int_linear(
        x,
        params["fc1"]["weight"],
        params["fc1"]["bias"]
    )

    x = requant_relu(
        x,
        FC1_REQUANT_MULT
    )

    # ========================================================
    # FC2
    #
    # 450 -> 10
    #
    # 最终不需要 requant。
    #
    # 因为我们只做 argmax，
    # 10 个 logits 共享相同 output scale。
    # ========================================================

    logits = int_linear(
        x,
        params["fc2"]["weight"],
        params["fc2"]["bias"]
    )

    return logits


# ============================================================
# Validation Dataset
# ============================================================

def build_validation_loader():
    """
    直接复用项目 src/dataset_loader.py 中已经创建好的：

        val_loader

    你的 dataset_loader.py 已经负责：

        1. 读取 Aircraft dataset
        2. Resize 到 130x130
        3. ToTensor()
        4. 使用原来的随机种子划分 dataset
        5. 得到:
               train = 4357
               val   = 1868

    这里绝对不要重新 random_split，
    否则 INT8 与 FP32 就不是在同一个 validation set 上比较。
    """

    try:

        import dataset_loader  # type: ignore
    except Exception as e:

        raise RuntimeError(
            "\nCannot import src/dataset_loader.py\n"
            f"Original error:\n{e}"
        )

    if not hasattr(
        dataset_loader,
        "val_loader"
    ):

        raise RuntimeError(
            "\nsrc/dataset_loader.py "
            "does not contain val_loader"
        )

    val_loader = dataset_loader.val_loader

    print()
    print("=" * 78)
    print(" VALIDATION DATASET")
    print("=" * 78)

    print()
    print("Using:")
    print("  dataset_loader.val_loader")

    print()
    print(
        f"Validation samples : "
        f"{len(val_loader.dataset)}"
    )

    print(
        f"Validation batches : "
        f"{len(val_loader)}"
    )

    print()

    return val_loader


# ============================================================
# Confusion Matrix
# ============================================================

def print_confusion_matrix(cm):

    print()
    print("=" * 90)
    print(" CONFUSION MATRIX")
    print("=" * 90)

    print(
        "true\\pred",
        end=""
    )

    for i in range(NUM_CLASSES):

        print(
            f"{i:>8d}",
            end=""
        )

    print()

    for true_class in range(NUM_CLASSES):

        print(
            f"{true_class:>9d}",
            end=""
        )

        for pred_class in range(NUM_CLASSES):

            print(
                f"{cm[true_class, pred_class]:>8d}",
                end=""
            )

        print()


# ============================================================
# Per-class Accuracy
# ============================================================

def print_per_class_accuracy(cm):

    print()
    print("=" * 78)
    print(" PER-CLASS ACCURACY")
    print("=" * 78)
    print()

    for c in range(NUM_CLASSES):

        total = int(
            cm[c].sum()
        )

        correct = int(
            cm[c, c]
        )

        if total > 0:

            accuracy = (
                100.0
                *
                correct
                /
                total
            )

        else:

            accuracy = 0.0

        print(
            f"class {c:2d}: "
            f"{correct:4d}/{total:4d}  "
            f"{accuracy:7.2f}%"
        )


# ============================================================
# Save Result
# ============================================================

def save_results(
    total,
    correct,
    accuracy,
    accuracy_drop,
    elapsed,
    confusion
):

    output_dir = "quant_output"

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    # --------------------------------------------------------
    # Confusion Matrix
    # --------------------------------------------------------

    matrix_path = os.path.join(
        output_dir,
        "int8_confusion_matrix.npy"
    )

    np.save(
        matrix_path,
        confusion
    )

    # --------------------------------------------------------
    # Text Result
    # --------------------------------------------------------

    result_path = os.path.join(
        output_dir,
        "int8_validation_result.txt"
    )

    incorrect = total - correct

    with open(
        result_path,
        "w"
    ) as f:

        f.write(
            "AircraftCNN INT8 Validation Result\n"
        )

        f.write(
            "=" * 70 + "\n"
        )

        f.write(
            f"Total samples : {total}\n"
        )

        f.write(
            f"Correct       : {correct}\n"
        )

        f.write(
            f"Incorrect     : {incorrect}\n"
        )

        f.write("\n")

        f.write(
            f"INT8 Accuracy : "
            f"{accuracy:.6f}%\n"
        )

        f.write(
            f"FP32 Accuracy : "
            f"{FP32_ACCURACY:.6f}%\n"
        )

        f.write(
            f"Accuracy Drop : "
            f"{accuracy_drop:.6f} "
            f"percentage points\n"
        )

        f.write("\n")

        f.write(
            f"Evaluation Time : "
            f"{elapsed:.3f} seconds\n"
        )

        if elapsed > 0:

            f.write(
                f"Throughput      : "
                f"{total / elapsed:.6f} "
                f"images/s\n"
            )

        f.write("\n")
        f.write("Confusion Matrix:\n")

        for row in confusion:

            f.write(
                " ".join(
                    str(int(x))
                    for x in row
                )
                +
                "\n"
            )

        f.write("\n")
        f.write("Per-Class Accuracy:\n")

        for c in range(NUM_CLASSES):

            class_total = int(
                confusion[c].sum()
            )

            class_correct = int(
                confusion[c, c]
            )

            if class_total > 0:

                class_acc = (
                    100.0
                    *
                    class_correct
                    /
                    class_total
                )

            else:

                class_acc = 0.0

            f.write(
                f"class {c:2d}: "
                f"{class_correct}/"
                f"{class_total} "
                f"{class_acc:.6f}%\n"
            )

    return result_path, matrix_path


# ============================================================
# Evaluation
# ============================================================

def evaluate():

    print("=" * 78)
    print(
        " AircraftCNN FULL INT8 Validation Evaluation"
    )
    print("=" * 78)
    print()

    # ========================================================
    # Load Quantized Model
    # ========================================================

    params = load_quantized_model()

    # ========================================================
    # Load Validation Dataset
    # ========================================================

    val_loader = build_validation_loader()

    total_expected = len(
        val_loader.dataset
    )

    if total_expected != 1868:

        print()
        print("=" * 78)
        print(" WARNING")
        print("=" * 78)

        print(
            f"Expected validation samples: 1868"
        )

        print(
            f"Current validation samples : "
            f"{total_expected}"
        )

        print()

    # ========================================================
    # Start
    # ========================================================

    print("=" * 78)
    print(" STARTING INT8 INFERENCE")
    print("=" * 78)

    print()

    total = 0
    correct = 0

    confusion = np.zeros(
        (
            NUM_CLASSES,
            NUM_CLASSES
        ),
        dtype=np.int64
    )

    start_time = time.time()

    # ========================================================
    # Iterate validation loader
    # ========================================================

    for batch_idx, batch in enumerate(
        val_loader
    ):

        if (
            not isinstance(
                batch,
                (tuple, list)
            )
            or
            len(batch) < 2
        ):

            raise RuntimeError(
                "Validation loader must return "
                "(images, labels)"
            )

        images = batch[0]
        labels = batch[1]

        batch_size = images.shape[0]

        # ----------------------------------------------------
        # 先逐张运行。
        #
        # 原因：
        # Integer Conv 中间 tensor 很大。
        #
        # 这样内存更加安全，也方便观察进度。
        # ----------------------------------------------------

        for i in range(batch_size):

            image = images[
                i:i + 1
            ]

            label = int(
                labels[i].item()
            )

            # ================================================
            # Full Integer CNN
            # ================================================

            logits = integer_forward(
                image,
                params
            )

            # ================================================
            # Argmax
            # ================================================

            pred = int(
                torch.argmax(
                    logits,
                    dim=1
                ).item()
            )

            # ================================================
            # Statistics
            # ================================================

            total += 1

            if pred == label:

                correct += 1

            confusion[
                label,
                pred
            ] += 1

            # ================================================
            # Progress
            # ================================================

            if (
                total == 1
                or
                total % 10 == 0
                or
                total == total_expected
            ):

                elapsed = (
                    time.time()
                    -
                    start_time
                )

                current_accuracy = (
                    100.0
                    *
                    correct
                    /
                    total
                )

                if elapsed > 0:

                    speed = (
                        total
                        /
                        elapsed
                    )

                else:

                    speed = 0.0

                if speed > 0:

                    remaining_images = (
                        total_expected
                        -
                        total
                    )

                    remaining_seconds = (
                        remaining_images
                        /
                        speed
                    )

                    eta_minutes = (
                        remaining_seconds
                        /
                        60.0
                    )

                    eta_text = (
                        f"ETA={eta_minutes:8.1f} min"
                    )

                else:

                    eta_text = (
                        "ETA=unknown"
                    )

                print(
                    f"[{total:4d}/"
                    f"{total_expected}] "
                    f"correct={correct:4d}  "
                    f"acc="
                    f"{current_accuracy:7.3f}%  "
                    f"{eta_text}",
                    flush=True
                )

    # ========================================================
    # Final Results
    # ========================================================

    elapsed = (
        time.time()
        -
        start_time
    )

    if total > 0:

        accuracy = (
            100.0
            *
            correct
            /
            total
        )

    else:

        accuracy = 0.0

    incorrect = (
        total
        -
        correct
    )

    accuracy_drop = (
        FP32_ACCURACY
        -
        accuracy
    )

    print()
    print("=" * 78)
    print(" FINAL RESULTS")
    print("=" * 78)

    print()

    print(
        f"Total samples      : "
        f"{total}"
    )

    print(
        f"Correct            : "
        f"{correct}"
    )

    print(
        f"Incorrect          : "
        f"{incorrect}"
    )

    print()

    print(
        f"INT8 Accuracy      : "
        f"{accuracy:.4f}%"
    )

    print(
        f"FP32 Accuracy      : "
        f"{FP32_ACCURACY:.4f}%"
    )

    print(
        f"Accuracy Drop      : "
        f"{accuracy_drop:.4f} "
        f"percentage points"
    )

    print()

    print(
        f"Evaluation Time    : "
        f"{elapsed:.2f} seconds"
    )

    if elapsed > 0:

        print(
            f"Throughput         : "
            f"{total / elapsed:.4f} "
            f"images/s"
        )

    # ========================================================
    # Confusion Matrix
    # ========================================================

    print_confusion_matrix(
        confusion
    )

    # ========================================================
    # Per-Class Accuracy
    # ========================================================

    print_per_class_accuracy(
        confusion
    )

    # ========================================================
    # Save
    # ========================================================

    result_path, matrix_path = save_results(
        total=total,
        correct=correct,
        accuracy=accuracy,
        accuracy_drop=accuracy_drop,
        elapsed=elapsed,
        confusion=confusion
    )

    print()
    print("=" * 78)
    print(" RESULTS SAVED")
    print("=" * 78)

    print()

    print(
        f"Text result:"
    )

    print(
        f"  {result_path}"
    )

    print()

    print(
        f"Confusion matrix:"
    )

    print(
        f"  {matrix_path}"
    )

    print()

    print("=" * 78)


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    evaluate()