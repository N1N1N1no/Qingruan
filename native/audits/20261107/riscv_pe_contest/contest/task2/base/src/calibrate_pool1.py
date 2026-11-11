import torch

from model import AircraftCNN
from dataset_loader import val_loader


# ============================================================
# 1. 加载训练好的模型
# ============================================================

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

model = AircraftCNN().to(device)

state_dict = torch.load(
    "models/best_model.pth",
    map_location=device
)

model.load_state_dict(state_dict)
model.eval()

print("Device:", device)


# ============================================================
# 2. 统计 Conv1 -> ReLU -> Pool1 输出
# ============================================================

global_min = float("inf")
global_max = float("-inf")

num_values = 0


with torch.no_grad():

    for batch_idx, (images, labels) in enumerate(val_loader):

        images = images.to(device)

        # 与 model.py 中完全相同
        x = model.conv1(images)
        x = model.relu(x)
        x = model.pool(x)

        batch_min = x.min().item()
        batch_max = x.max().item()

        global_min = min(global_min, batch_min)
        global_max = max(global_max, batch_max)

        num_values += x.numel()

        print(
            f"Batch {batch_idx:3d}: "
            f"shape={tuple(x.shape)}, "
            f"min={batch_min:.8f}, "
            f"max={batch_max:.8f}"
        )


# ============================================================
# 3. 计算 Conv2 输入 activation scale
# ============================================================

abs_max = max(abs(global_min), abs(global_max))

Sx2 = abs_max / 127.0


print()
print("========================================")
print("Pool1 Activation Calibration")
print("========================================")

print("Global min :", global_min)
print("Global max :", global_max)
print("Abs max    :", abs_max)
print("Values     :", num_values)

print()
print("Conv2 input scale:")
print("Sx2 =", Sx2)

print()
print("Quantization formula:")
print("x_q = round(x_fp32 / Sx2)")
print("x_q = clamp(x_q, 0, 127)")
