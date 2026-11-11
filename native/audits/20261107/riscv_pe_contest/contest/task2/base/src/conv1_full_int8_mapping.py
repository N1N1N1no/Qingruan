import os
import torch
from PIL import Image
from torchvision import transforms

from model import AircraftCNN


# ============================================================
# 1. 加载模型
# ============================================================

model = AircraftCNN()

state_dict = torch.load(
    "models/best_model.pth",
    map_location="cpu"
)

model.load_state_dict(state_dict)
model.eval()


# ============================================================
# 2. 读取真实图片
# ============================================================

image_dir = "dataset/B-1"

image_files = sorted([
    name for name in os.listdir(image_dir)
    if name.lower().endswith(
        (".jpg", ".jpeg", ".png", ".bmp")
    )
])

image_path = os.path.join(
    image_dir,
    image_files[0]
)

transform = transforms.Compose([
    transforms.Resize((130, 130)),
    transforms.ToTensor()
])

image = Image.open(image_path).convert("RGB")
x = transform(image).unsqueeze(0)


# ============================================================
# 3. Quantization Scale
# ============================================================

Sx = 1.0 / 127.0

weight_fp32 = model.conv1.weight.detach()
bias_fp32 = model.conv1.bias.detach()

weight_abs_max = weight_fp32.abs().max().item()

Sw = weight_abs_max / 127.0
Sacc = Sx * Sw


print("===== Quantization Scale =====")
print("Sx   =", Sx)
print("Sw   =", Sw)
print("Sacc =", Sacc)


# ============================================================
# 4. Weight Quantization
#
# [32,3,3,3] -> [32,27]
# ============================================================

weight_q = torch.round(
    weight_fp32 / Sw
).clamp(
    -127,
    127
).to(torch.int64)

weight_q_flat = weight_q.reshape(
    32,
    27
)


# ============================================================
# 5. Bias Quantization
# ============================================================

bias_q = torch.round(
    bias_fp32 / Sacc
).to(torch.int64)


# ============================================================
# 6. 构造所有 Input Patch
#
# PyTorch unfold:
#
# [1,3,130,130]
#
# ->
#
# [1,27,16384]
#
# 其中：
#
# 27    = K
# 16384 = 128×128 spatial positions
# ============================================================

patches_fp32 = torch.nn.functional.unfold(
    x,
    kernel_size=3,
    stride=1,
    padding=0
)[0]

# 转置后：
#
# [16384,27]
#
# 每一行就是一个 spatial position 的27个输入

patches_fp32 = patches_fp32.transpose(
    0,
    1
)


# ============================================================
# 7. Input Quantization
# ============================================================

input_q = torch.round(
    patches_fp32 / Sx
).clamp(
    0,
    127
).to(torch.int64)


print()
print("===== Mapping Shape =====")
print("Weight Q :", weight_q_flat.shape)
print("Input Q  :", input_q.shape)


# ============================================================
# 8. Integer Golden
#
# 数学上：
#
# [32,27] @ [27,16384]
#
# ->
#
# [32,16384]
#
# 这等价于我们已经验证过的：
#
# OC Tile
# ×
# Spatial Tile
# ×
# K Tile
#
# 这里只是用矩阵运算加速完整验证。
# ============================================================

integer_output = (
    weight_q_flat
    @
    input_q.transpose(0, 1)
)

# 加 Bias_q
integer_output += bias_q[:, None]


# ============================================================
# 9. 恢复成 Conv1 Output
#
# [32,16384]
#
# ->
#
# [32,128,128]
# ============================================================

integer_output = integer_output.reshape(
    32,
    128,
    128
)


# ============================================================
# 10. Dequantize
# ============================================================

dequant_output = (
    integer_output.to(torch.float64)
    * Sacc
)


# ============================================================
# 11. PyTorch FP32 Golden
# ============================================================

with torch.no_grad():

    fp32_output = model.conv1(x)[0]


# ============================================================
# 12. Error
# ============================================================

error = torch.abs(
    dequant_output
    -
    fp32_output
)

max_error = error.max().item()
mean_error = error.mean().item()

rmse = torch.sqrt(
    torch.mean(error ** 2)
).item()


# ============================================================
# 13. 最大误差位置
# ============================================================

flat_index = torch.argmax(error).item()

oc = flat_index // (128 * 128)

remaining = flat_index % (128 * 128)

h = remaining // 128
w = remaining % 128


# ============================================================
# 14. PSUM Range
# ============================================================

psum_min = integer_output.min().item()
psum_max = integer_output.max().item()

INT32_MIN = -(2 ** 31)
INT32_MAX = (2 ** 31) - 1

int32_safe = (
    psum_min >= INT32_MIN
    and
    psum_max <= INT32_MAX
)


# ============================================================
# 15. 统计不同误差范围
# ============================================================

num_total = error.numel()

num_lt_001 = (
    error < 0.01
).sum().item()

num_lt_002 = (
    error < 0.02
).sum().item()

num_lt_005 = (
    error < 0.05
).sum().item()


# ============================================================
# 16. 打印结果
# ============================================================

print()
print("========================================")
print("FULL CONV1 INT8 MAPPING")
print("========================================")

print()
print("Output shape :", integer_output.shape)
print("Total outputs:", integer_output.numel())


print()
print("===== INT32 PSUM Range =====")

print("Min =", psum_min)
print("Max =", psum_max)

if int32_safe:
    print("PASS: 全部PSUM均在int32范围内")
else:
    print("FAIL: 出现int32 overflow")


print()
print("===== Quantization Error =====")

print("Max Absolute Error  =", max_error)
print("Mean Absolute Error =", mean_error)
print("RMSE                =", rmse)


print()
print("===== Error Distribution =====")

print(
    "Error < 0.01:",
    num_lt_001,
    "/",
    num_total,
    "=",
    num_lt_001 / num_total * 100,
    "%"
)

print(
    "Error < 0.02:",
    num_lt_002,
    "/",
    num_total,
    "=",
    num_lt_002 / num_total * 100,
    "%"
)

print(
    "Error < 0.05:",
    num_lt_005,
    "/",
    num_total,
    "=",
    num_lt_005 / num_total * 100,
    "%"
)


print()
print("===== Worst Position =====")

print("OC =", oc)
print("H  =", h)
print("W  =", w)

print(
    "FP32        =",
    fp32_output[oc, h, w].item()
)

print(
    "INT32       =",
    integer_output[oc, h, w].item()
)

print(
    "Dequantized =",
    dequant_output[oc, h, w].item()
)

print(
    "Error       =",
    error[oc, h, w].item()
)


print()
print("========================================")
print("RESULT")
print("========================================")

if int32_safe and mean_error < 0.02:

    print(
        "PASS: 完整 Conv1 INT8 Mapping 正常"
    )

else:

    print(
        "CHECK: 需要进一步分析量化结果"
    )
